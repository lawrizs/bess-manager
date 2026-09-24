"""SolaX inverter controller.

SolaX inverters are controlled via the homeassistant-solax-modbus integration
using VPP (Virtual Power Plant) commands.  Unlike Growatt, SolaX has no
persistent TOU schedule stored in the inverter.  Instead, commands are issued
per 15-minute period and kept active via the integration's autorepeat mechanism.

Control model:
- BESS calls ``_write_period_to_hardware()`` at each period boundary.
- ``set_solax_active_power_control(watts)`` sends the four-step VPP sequence:
  1. Enable battery control mode.
  2. Set active power target (positive = charge, negative = discharge).
  3. Set autorepeat duration to 1 200 s (covers 15-min period plus margin).
  4. Press trigger.
- For SOLAR_STORAGE the inverter is placed back into self-use mode via
  ``set_solax_vpp_disabled()``; autorepeat on the previous command expires
  naturally, providing a safe fallback.
- For IDLE the battery is held instead, via ``set_solax_no_discharge_hold()``
  — the same trigger and autorepeat window, selecting the vendor's "Enabled
  No Discharge" mode rather than a power target.

Intent-to-power mapping:
- GRID_CHARGING    → +(rate% x max_charge_W)  (charge at the plan's rate, #754)
- SOLAR_STORAGE    → disable VPP      (let solar charge naturally)
- LOAD_SUPPORT     -> -(rate% x max_discharge_W)  (discharge to cover load), or
  disable VPP when ``solax_native_load_support_enabled`` is set, handing the
  period to the inverter's own self-use load-following so it covers the actual
  deficit instead of the forecast one (the #413 trade, opt-in here)
- BATTERY_EXPORT -> -max_discharge_W (full discharge for export)
- IDLE             → "Enabled No Discharge" hold: house load is covered from
  grid/solar rather than the battery, while surplus PV still charges it.
  Ports the Growatt VPP IDLE hold (#466) to this platform, where the vendor
  mode gets both halves of ``_idle_battery_flows`` right instead of trading
  one for the other. The hold itself is **confirmed on real hardware** — a
  battery held through IDLE periods instead of being spent on house load.
  The surplus-PV half and the reserve-floor release below are read off the
  solax_modbus plugin source and have not been separately observed.
- IDLE at the reserve floor → disable VPP (#592): nothing left to protect,
  and a standing hold would keep the inverter from ever idling down.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from . import time_utils
from .dp_schedule import DPSchedule
from .inverter_controller import InverterController
from .settings import BatterySettings

if TYPE_CHECKING:
    from .ha_api_controller import HomeAssistantAPIController

logger = logging.getLogger(__name__)


class SolaxController(InverterController):
    """SolaX inverter controller using VPP active-power commands.

    SolaX does not use a persistent TOU schedule.  ``sync_to_hardware``
    is a no-op; the actual hardware writes happen period-by-period via
    ``_write_period_to_hardware``, called from
    ``BatterySystemManager._apply_period_schedule``.

    ``active_tou_intervals`` is always empty — there are no segments to
    push to the inverter.
    """

    supports_charge_rate_control: ClassVar[bool] = False

    # SolaX has no mode concept -- discharge_rate always becomes an
    # immediate forced watt-level command (_write_period_to_hardware),
    # never a load-following ceiling. See #324.
    discharge_rate_is_load_following: ClassVar[bool] = False

    CONTROL_MODEL: ClassVar[str] = "vpp_power"

    # mypy: "Cannot override writeable attribute with read-only property" --
    # the base declares this as a ClassVar and there is no way to narrow one to
    # a read-only property. The three capability properties on
    # SolaxModbusGrowattController (:152, :159, :173) are the same shape and
    # carry the same error in the baseline; nothing assigns to it.
    @property
    def load_support_delivers_exact_cover(self) -> bool:  # type: ignore[override]
        """Is a planned LOAD_SUPPORT discharge delivered as
        `min(plan, actual load)`? Only when native load support is opted into.

        By default LOAD_SUPPORT is a forced `-(rate% x max_discharge)` here,
        so a partial cover is delivered at the commanded rate whatever the
        house is really drawing -- the DP must not plan one.
        `solax_native_load_support_enabled` ports #413's trade to this
        platform: the period is handed to the inverter's own self-use
        load-following and *no rate is written at all*, so a cover plan is
        delivered exactly and the off-lattice candidate becomes legal.

        A property rather than a ClassVar because the answer is a user
        setting, read live off the `BatterySettings` instance BSM shares with
        this controller. `PlatformCapabilities.from_controller` reads this by
        plain attribute access and `BatterySystemManager.platform_capabilities`
        rebuilds on every access, so the next optimization sees a change with
        no reconstruction. Same shape as
        `SolaxModbusGrowattController.discharge_rate_is_load_following`, which
        is likewise settings-derived.

        Deliberately NOT paired with a change to
        `discharge_rate_is_load_following`: that asks whether a *written* rate
        is a ceiling, and BATTERY_EXPORT still writes a forced watt target
        through the same path either way. Conflating the two was caught in
        review once already -- see `execution_model.PlatformCapabilities`.
        """
        return self.battery_settings.solax_native_load_support_enabled

    def __init__(self, battery_settings: BatterySettings) -> None:
        """Initialise the SolaX controller."""
        super().__init__(battery_settings)

    # ── Abstract property ─────────────────────────────────────────────────────

    @property
    def active_tou_intervals(self) -> list[dict]:
        """SolaX has no stored TOU intervals — always empty."""
        return []

    # ── Schedule creation ─────────────────────────────────────────────────────

    def _build_candidate(self, schedule: DPSchedule) -> list[str]:
        """Return the candidate intent list for a schedule.

        SolaX requires no TOU conversion — the candidate IS the raw strategic
        intent list. Kept as a named method for interface symmetry with the
        other inverter controller subclasses, even though it does no
        transformation.
        """
        return schedule.original_dp_results["strategic_intent"]

    def apply_intents(self, schedule: DPSchedule, current_period: int = 0) -> None:
        """Store strategic intents from a DPSchedule.

        SolaX requires no TOU conversion. Intents are applied period-by-period
        via ``_write_period_to_hardware`` and are not pushed as a batch to the
        inverter hardware.

        Args:
            schedule: DPSchedule containing strategic_intent list.
            current_period: Unused for SolaX.
        """
        logger.info("Creating SolaX schedule from strategic intents")

        self.strategic_intents = self._build_candidate(schedule)
        self.current_schedule = schedule

        logger.info(
            "SolaX: %d strategic intents loaded (quarterly resolution)",
            len(self.strategic_intents),
        )

    # ── Hardware interface ────────────────────────────────────────────────────

    def apply_period(
        self,
        controller: "HomeAssistantAPIController",
        grid_charge: bool,
        discharge_rate: int,
        block_passive_charging: bool = False,
        strategic_intent: str = "",
        at_reserve_floor: bool = False,
        charge_rate: int = 100,
    ) -> tuple[bool, str]:
        """Write period control settings to hardware.

        Overrides the base class only to thread `charge_rate`,
        `strategic_intent` and `at_reserve_floor` through to
        `_write_period_to_hardware` -- the base `apply_period` passes on none
        of them, since register-based platforms realize the rate via a
        separate register instead and have no intent to distinguish (see
        `apply_period`'s own docstring). The intent is needed here because
        `grid_charge`/`discharge_rate` collapse LOAD_SUPPORT, BATTERY_EXPORT
        and IDLE onto shared values, and each is handled differently; the
        floor flag is what releases IDLE's hold (#592).
        """
        return self._write_period_to_hardware(
            controller,
            grid_charge,
            discharge_rate,
            block_passive_charging,
            charge_rate,
            strategic_intent,
            at_reserve_floor,
        )

    def _write_period_to_hardware(
        self,
        controller,
        grid_charge: bool,
        discharge_rate: int,
        block_passive_charging: bool = False,
        charge_rate: int = 100,
        strategic_intent: str = "",
        at_reserve_floor: bool = False,
    ) -> tuple[bool, str]:
        """Issue a SolaX VPP command for the current period.

        Derives the power target in watts from the abstract control
        parameters supplied by `apply_period`:

        - ``grid_charge=True``  → charge at `charge_rate` (#754), the plan's
          action-derived rate.
        - ``grid_charge=False, intent=IDLE`` → the vendor's "Enabled No
          Discharge" hold. Self-use would cover house load from the battery,
          but IDLE's own cost model never credits that discharge
          (`_idle_battery_flows`), so self-consumption must come from grid
          and solar. The mode still lets surplus PV charge the battery, which
          that same model *does* credit -- see
          `HomeAssistantAPIController.set_solax_no_discharge_hold`.
        - ``grid_charge=False, intent=IDLE, at_reserve_floor=True`` → disable
          VPP instead (#592). The hold protects stored energy; at the floor
          there is none left to protect, and holding would keep rearming the
          autorepeat window every period, so the inverter is never handed
          back and its BMS never idles down. Safer here than on Growatt VPP,
          which skips `sync_soc_limits` entirely: `initialize_hardware` does
          write the inverter's own min SOC, so released self-use cannot
          discharge below the configured floor.
        - ``grid_charge=False, discharge_rate=0`` → disable VPP
          (SOLAR_STORAGE, and SOLAR_EXPORT -- see the
          ``block_passive_charging`` note below).
        - ``grid_charge=False, intent=LOAD_SUPPORT`` with native load support on
          → disable VPP, handing the period to the inverter's own self-use
          load-following so it covers the actual deficit rather than the
          forecast one. The rate is discarded, not scaled -- which is what
          lets `load_support_delivers_exact_cover` be True.
        - ``grid_charge=False, discharge_rate>0`` → discharge at the given rate.

        ``block_passive_charging`` is accepted but currently unused: unlike
        the Growatt VPP fix (#355, register-verified against the vendor
        protocol spec), no equivalent real-SolaX-hardware verification has
        been done here. SolaX likely has the same underlying gap (disabling
        VPP at discharge_rate=0 lets solar recharge the battery during
        SOLAR_EXPORT too) but fixing it without spec/hardware confirmation
        would be speculation -- tracked as a follow-up, not fixed here.

        Args:
            controller: HomeAssistantAPIController instance.
            grid_charge: Whether grid charging is requested.
            discharge_rate: Discharge power as a percentage (0-100).
            block_passive_charging: Unused -- see docstring above.
            charge_rate: The plan's action-derived GRID_CHARGING rate
                (0-100%, #754), supplied by the caller (BatterySystemManager,
                via apply_period) -- not re-derived here from wall-clock
                time. See InverterController.apply_period's docstring for
                why only the caller can supply this correctly, including on
                a retry.
            strategic_intent: The period's intent. Needed because
                grid_charge/discharge_rate collapse LOAD_SUPPORT and
                BATTERY_EXPORT to the same values and only the former is
                eligible for native load support -- the same reason #413
                threaded it for Growatt VPP -- and because IDLE shares
                SOLAR_STORAGE's values while needing the opposite command.
            at_reserve_floor: Whether the battery is at (or below) its
                configured minimum SoE, from a live SoC read rather than the
                plan: the hold exists to protect stored energy, so what
                matters is whether any is actually there now.

        Returns:
            Tuple of (success, error_message). error_message is empty on success.
        """
        use_native_load_support = (
            strategic_intent == "LOAD_SUPPORT"
            and self.battery_settings.solax_native_load_support_enabled
        )
        try:
            if grid_charge:
                target_watts = int(self.max_charge_power_kw * charge_rate / 100 * 1000)
                controller.set_solax_active_power_control(target_watts)
            elif strategic_intent == "IDLE" and not at_reserve_floor:
                controller.set_solax_no_discharge_hold()
            elif discharge_rate == 0 or use_native_load_support:
                controller.set_solax_vpp_disabled()
            else:
                target_watts = -int(
                    self.max_discharge_power_kw * discharge_rate / 100 * 1000
                )
                controller.set_solax_active_power_control(target_watts)
            return True, ""
        except Exception as e:
            logger.error("FAILED: SolaX VPP period write: %s", e)
            return False, str(e)

    def _vpp_display_state(
        self,
        grid_charge: bool,
        discharge_rate: int,
        block_passive_charging: bool = False,
        strategic_intent: str = "",
        at_reserve_floor: bool = False,
        charge_rate: int = 100,
    ) -> tuple[int, bool]:
        """Map (grid_charge, discharge_rate) to (power_pct, remote_control_enabled)
        for display, mirroring _write_period_to_hardware()'s branches exactly.

        `strategic_intent` is read for the same reason the write path reads
        it: with `solax_native_load_support_enabled` on, LOAD_SUPPORT is
        handed to the inverter rather than commanded, and a display that
        still reported `-rate%` would show a period the inverter was never
        told to run.

        `block_passive_charging` is still ignored, because the write path
        ignores it too -- SolaX never received the #355 Growatt VPP fix, so
        its real SOLAR_EXPORT behaviour differs and there is nothing here to
        mirror. It stays in the signature only so the base class's
        _mode_display_fields() can call _vpp_display_state() uniformly across
        all vpp_power controllers.

        at_reserve_floor (#592) *is* read now that IDLE holds rather than
        releasing: a displayed hold for a period production releases is the
        fabrication _mode_display_fields exists to prevent.

        The IDLE hold reports 0% with remote control on, which is what the
        write path actually does -- the vendor mode computes its own battery
        power, so BESS commands none. Reporting a figure here would invent a
        command nothing sends.

        charge_rate (#754) likewise changes what this reports, mirroring how
        _write_period_to_hardware scales its actual watts command to the
        plan's rate instead of always writing max_charge_power_kw.

        Returns:
            (power_pct, remote_control_enabled) -- power_pct expressed as a
            percent of max charge/discharge power, matching discharge_rate's
            own convention (not raw watts).
        """
        use_native_load_support = (
            strategic_intent == "LOAD_SUPPORT"
            and self.battery_settings.solax_native_load_support_enabled
        )
        if grid_charge:
            return charge_rate, True
        if strategic_intent == "IDLE" and not at_reserve_floor:
            return 0, True
        if discharge_rate == 0 or use_native_load_support:
            return 0, False
        return -discharge_rate, True

    def sync_to_hardware(
        self,
        controller,
        effective_period: int,
    ) -> tuple[int, int]:
        """No-op for SolaX — schedule is applied period-by-period via VPP.

        SolaX has no persistent TOU schedule to push in bulk.  Inverter state
        is fully controlled by per-period ``_write_period_to_hardware`` calls.

        Returns:
            (0, 0) — no writes or disables performed.
        """
        logger.debug("SolaX: sync_to_hardware is a no-op (per-period VPP)")
        return 0, 0

    def initialize_hardware(self, controller) -> None:
        self.sync_soc_limits(controller)

    def sync_soc_limits(self, controller) -> None:
        """Sync battery minimum SOC from config to the SolaX inverter.

        Reads the current minimum SOC from the inverter and writes the
        configured value only when a mismatch is detected.

        Args:
            controller: HomeAssistantAPIController instance.
        """
        configured_min_soc = int(self.battery_settings.min_soc)

        current_mode = controller.get_solax_power_control_mode()
        logger.debug("SolaX: current power control mode = %r", current_mode)

        actual_min_soc = controller.get_solax_min_soc()
        if actual_min_soc == configured_min_soc:
            logger.info("SolaX: battery minimum SOC verified: %d%%", configured_min_soc)
            return

        controller.set_solax_min_soc(configured_min_soc)
        logger.info("SolaX: battery minimum SOC set to %d%%", configured_min_soc)

    def read_and_initialize_from_hardware(self, controller, current_hour: int) -> None:
        """Read current inverter state and initialise this controller.

        SolaX has no stored schedule to read back.  We log the current power
        control mode for diagnostics and leave ``strategic_intents`` empty
        (they will be populated when the next schedule is created).

        Args:
            controller: HomeAssistantAPIController instance.
            current_hour: Current hour (0-23), unused for SolaX.
        """
        current_mode = controller.get_solax_power_control_mode()
        logger.info(
            "SolaX: initialised from hardware — current power control mode = %r",
            current_mode,
        )

    # ── Schedule comparison ───────────────────────────────────────────────────

    def _diff_intents(
        self, current: list[str], new: list[str], from_period: int
    ) -> tuple[bool, str]:
        """Compare two strategic-intent lists from ``from_period`` onward.

        Used by ``evaluate_intents`` (compares against a candidate built
        from a ``DPSchedule``).
        """
        if not current and not new:
            return False, ""

        if len(current) != len(new):
            return True, (f"SolaX intent count differs: {len(current)} vs {len(new)}")

        for period in range(from_period, len(current)):
            if current[period] != new[period]:
                logger.info(
                    "DECISION: SolaX intent differs at period %d — "
                    "current=%s new=%s",
                    period,
                    current[period],
                    new[period],
                )
                return True, (f"SolaX strategic intents differ from period {period}")

        logger.info("DECISION: SolaX schedules match")
        return False, ""

    def evaluate_intents(
        self, schedule: DPSchedule, current_period: int = 0
    ) -> tuple[bool, str]:
        """Compare the committed intents against a candidate schedule.

        Two schedules differ when any period at or after ``current_period``
        has a different strategic intent.

        Args:
            schedule: DPSchedule to evaluate against the committed intents.
            current_period: First period to compare (earlier periods ignored).

        Returns:
            Tuple of (schedules_differ, reason).
        """
        candidate = self._build_candidate(schedule)
        return self._diff_intents(self.strategic_intents, candidate, current_period)

    # ── TOU display ───────────────────────────────────────────────────────────

    def get_daily_TOU_settings(self) -> list[dict]:
        """Return an empty list — SolaX has no TOU segments."""
        return []

    def log_current_TOU_schedule(self, header: str = "") -> None:
        """Log current SolaX VPP intent summary."""
        if header:
            logger.info(header)

        if not self.strategic_intents:
            logger.info("SolaX: no schedule loaded")
            return

        now = time_utils.now()
        current_period = now.hour * 4 + now.minute // 15

        if current_period < len(self.strategic_intents):
            intent = self.strategic_intents[current_period]
            logger.info(
                "SolaX: current period %d (%02d:%02d) → %s",
                current_period,
                now.hour,
                (now.minute // 15) * 15,
                intent,
            )
        else:
            logger.info("SolaX: %d intents loaded", len(self.strategic_intents))

    def log_detailed_schedule(self, header: str = "") -> None:
        """Log detailed schedule with per-period strategic intents."""
        if header:
            logger.info(header)

        if not self.strategic_intents:
            logger.info("SolaX: no schedule data available")
            return

        now = time_utils.now()
        current_period = now.hour * 4 + now.minute // 15

        lines = [
            "\n╔═══════════════╦══════════════════╦════════════╗",
            "║  Time Period  ║ Strategic Intent ║ VPP Action ║",
            "╠═══════════════╬══════════════════╬════════════╣",
        ]

        num_periods = len(self.strategic_intents)
        period = 0
        while period < num_periods:
            intent = self.strategic_intents[period]
            run_start = period
            while (
                period + 1 < num_periods
                and self.strategic_intents[period + 1] == intent
            ):
                period += 1
            run_end = period

            sh, sm = run_start // 4, (run_start % 4) * 15
            eh, em = run_end // 4, (run_end % 4) * 15
            em += 14

            time_range = f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"
            is_current = run_start <= current_period <= run_end
            marker = "*" if is_current else " "

            if intent == "GRID_CHARGING":
                action = "+charge"
            elif intent == "IDLE":
                action = "hold"
            elif intent == "SOLAR_STORAGE":
                action = "self-use"
            else:
                action = "-discharge"

            lines.append(f"║{marker}{time_range:13} ║ {intent:16} ║ {action:10} ║")
            period += 1

        lines.append("╚═══════════════╩══════════════════╩════════════╝")
        lines.append("* indicates current period")

        logger.info("\n".join(lines))

    # ── API / display methods ─────────────────────────────────────────────────

    def get_all_tou_segments(self) -> list[dict]:
        """Return strategic intent groups as display segments.

        SolaX has no hardware TOU intervals, so we represent the schedule as
        consecutive intent groups for the dashboard's schedule view.
        """
        groups = self.get_detailed_period_groups()
        if not groups:
            return [
                {
                    "segment_id": 0,
                    "start_time": "00:00",
                    "end_time": "23:59",
                    "enabled": False,
                    "is_default": True,
                }
            ]

        result = []
        for i, group in enumerate(groups, 1):
            result.append(
                {
                    "segment_id": i,
                    "start_time": group["start_time"],
                    "end_time": group["end_time"],
                    "vpp_power_pct": group["vpp_power_pct"],
                    "vpp_remote_control": group["vpp_remote_control"],
                    "enabled": True,
                    "strategic_intent": group["intent"],
                }
            )
        return result

    # ── Health check ──────────────────────────────────────────────────────────

    # VPP entities required for battery control.
    _VPP_ENTITIES: ClassVar[list[tuple[str, str]]] = [
        ("solax_power_control_mode", "Power Control Mode"),
        ("solax_active_power", "Active Power"),
        ("solax_autorepeat_duration", "Autorepeat Duration"),
        ("solax_power_control_trigger", "Trigger"),
        ("solax_battery_min_soc", "Battery Min SOC"),
    ]

    def check_health(self, controller) -> list:
        """Check SolaX VPP control entity availability.

        Verifies that all VPP control entities are configured and readable,
        confirming connectivity to the solax-modbus integration.
        """
        checks = []
        has_error = False

        for sensor_key, display_name in self._VPP_ENTITIES:
            entity_id = controller.sensors.get(sensor_key, "")
            if not entity_id:
                checks.append(
                    {
                        "name": display_name,
                        "key": sensor_key,
                        "entity_id": "Not configured",
                        "status": "ERROR",
                        "rawValue": None,
                        "displayValue": "N/A",
                        "error": f"Entity not configured — set {sensor_key} in sensor config",
                    }
                )
                has_error = True
                continue

            # Power control mode is the only one we can read; others are write-only
            if sensor_key == "solax_power_control_mode":
                try:
                    mode = controller.get_solax_power_control_mode()
                    if mode is not None:
                        checks.append(
                            {
                                "name": display_name,
                                "key": sensor_key,
                                "entity_id": entity_id,
                                "status": "OK",
                                "rawValue": mode,
                                "displayValue": str(mode),
                                "error": None,
                            }
                        )
                    else:
                        checks.append(
                            {
                                "name": display_name,
                                "key": sensor_key,
                                "entity_id": entity_id,
                                "status": "ERROR",
                                "rawValue": None,
                                "displayValue": "N/A",
                                "error": "Entity returned None — check sensor config",
                            }
                        )
                        has_error = True
                except Exception as e:
                    checks.append(
                        {
                            "name": display_name,
                            "key": sensor_key,
                            "entity_id": entity_id,
                            "status": "ERROR",
                            "rawValue": None,
                            "displayValue": "N/A",
                            "error": f"Read failed: {e}",
                        }
                    )
                    has_error = True
            else:
                checks.append(
                    {
                        "name": display_name,
                        "key": sensor_key,
                        "entity_id": entity_id,
                        "status": "OK",
                        "rawValue": None,
                        "displayValue": "Configured",
                        "error": None,
                    }
                )

        health_check = {
            "name": "Battery Control (SolaX)",
            "description": "Controls SolaX inverter via VPP active-power commands",
            "required": True,
            "status": "ERROR" if has_error else "OK",
            "checks": checks,
            "last_run": datetime.now().isoformat(),
        }

        return [health_check]
