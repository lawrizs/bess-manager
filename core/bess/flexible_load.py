"""Flexible load -- run a controllable load on solar that would otherwise be exported.

The motivating load is an electric water heater behind a Tasmota relay,
used as thermal storage: when the battery is full and the house is exporting
more than the heater draws, the surplus heats water instead of being sold at
the feed-in price. When the surplus goes away the heater is switched off
again -- it is acceptable for the water to cool, it is not acceptable for the
battery or the grid to pay for heating it.

The controller is purely reactive and invisible to the optimizer: it runs
every minute alongside the discharge-inhibit job, reads live power, and
issues a switch command only when the decision has changed. The optimizer
sees the heater only through measured flows; its energy is kept out of the
learned consumption baseline via `home.managed_load_sensors`
(`managed_loads.py`).
"""

import logging
from datetime import datetime, timedelta

from . import time_utils
from .ha_api_controller import HomeAssistantAPIController
from .health_check import perform_health_check
from .settings import BatterySettings, HomeSettings

logger = logging.getLogger(__name__)

# Inverters hover just under the configured ceiling rather than sitting on
# it, so "full" has to admit a little slack or the heater never starts.
FULL_SOC_TOLERANCE_PCT = 1.0

# A decision has to hold for this many consecutive one-minute ticks before it
# is acted on, so a single noisy sample (a cloud edge, a kettle) does not
# cycle the relay.
CONFIRM_TICKS = 2


def desired_switch_state(
    *,
    load_power_w: float,
    battery_soc: float,
    export_power_w: float,
    import_power_w: float,
    battery_discharge_power_w: float,
    rated_power_w: float,
    surplus_margin_w: float,
    battery_full_soc: float,
) -> bool:
    """Return whether the load should be on given one tick of live readings.

    Two regimes, told apart by whether the load is actually drawing power:

    - **Drawing** (element on): keep running unless someone other than the
      sun is paying -- grid import or battery discharge above the margin.
      Once the heater absorbs the surplus, export collapses to ~0 and the
      battery sits idle; that steady state is exactly what we want, so
      export is deliberately not consulted here.
    - **Not drawing** (switch off, or switch on with the thermostat
      satisfied): turn on only if the battery is full and the export alone
      would cover the rated draw plus the margin. A satisfied thermostat is
      judged by this same rule so that a vanished surplus turns the relay
      off before the element can re-engage against grid or battery.

    The margin doubles as the "is it drawing" threshold: a resistive heater
    is either at its rating or at a few watts of standby.
    """
    drawing = load_power_w > surplus_margin_w
    if drawing:
        someone_else_paying = (
            import_power_w > surplus_margin_w
            or battery_discharge_power_w > surplus_margin_w
        )
        return not someone_else_paying
    battery_full = battery_soc >= battery_full_soc
    return battery_full and export_power_w >= rated_power_w + surplus_margin_w


class FlexibleLoadController:
    """Drive one controllable load from solar surplus, one tick per minute."""

    def __init__(
        self,
        ha_controller: HomeAssistantAPIController,
        home_settings: HomeSettings,
        battery_settings: BatterySettings,
    ) -> None:
        self.controller = ha_controller
        # Held by reference, not copied: a settings PATCH mutates these in
        # place and the next tick must see the new values without a restart.
        self.home_settings = home_settings
        self.battery_settings = battery_settings
        self._pending_state: bool | None = None
        self._pending_ticks = 0
        self._last_switch_at: datetime | None = None
        self._last_switch_on: bool | None = None
        self._last_power_w: float | None = None
        logger.info(
            "Initialized FlexibleLoadController: rated %.0f W, margin %.0f W, "
            "min off %d min",
            home_settings.flexible_load_rated_power_w,
            home_settings.flexible_load_surplus_margin_w,
            home_settings.flexible_load_min_off_minutes,
        )

    def apply(self) -> None:
        """Read live state, decide, and write the switch only on a confirmed change.

        Fails closed: an unreadable power or SOC reading switches the load
        off at once rather than waiting out the confirmation ticks -- with
        no way to tell who is paying, "off" is the only state that matches
        the user's rule. An unreadable switch state writes nothing, since
        there is nothing to compare a decision against.
        """
        switch_on = self.controller.get_flexible_load_switch_on()
        self._last_switch_on = switch_on
        if switch_on is None:
            logger.warning("Flexible load switch state unreadable; skipping this tick")
            self._reset_pending()
            return

        readings = {
            "load_power_w": self.controller.get_flexible_load_power(),
            "battery_soc": self.controller.get_battery_soc(),
            "export_power_w": self.controller.get_export_power(),
            "import_power_w": self.controller.get_import_power(),
            "battery_discharge_power_w": self.controller.get_battery_discharge_power(),
        }
        self._last_power_w = readings["load_power_w"]
        missing = [name for name, value in readings.items() if value is None]
        if missing:
            logger.warning(
                "Flexible load readings unavailable (%s); switching off",
                ", ".join(missing),
            )
            self._reset_pending()
            if switch_on:
                self._switch(False)
            return

        desired = desired_switch_state(
            rated_power_w=self.home_settings.flexible_load_rated_power_w,
            surplus_margin_w=self.home_settings.flexible_load_surplus_margin_w,
            battery_full_soc=self.battery_settings.max_soc - FULL_SOC_TOLERANCE_PCT,
            **readings,
        )
        if desired == switch_on:
            self._reset_pending()
            return

        if self._pending_state != desired:
            self._pending_state = desired
            self._pending_ticks = 0
        self._pending_ticks += 1
        if self._pending_ticks < CONFIRM_TICKS:
            return

        # The minimum-off time gates turn-on only: a deficit is never
        # tolerated for the relay's sake, so turn-off goes through as soon
        # as it is confirmed.
        if desired and not self._min_off_elapsed():
            logger.debug(
                "Flexible load surplus confirmed but minimum off time not elapsed"
            )
            return

        self._switch(desired)
        self._reset_pending()

    def status(self) -> dict:
        """Last tick's view of the load, for the dashboard -- no HA calls."""
        return {
            "enabled": True,
            "switch_on": self._last_switch_on,
            "power_w": self._last_power_w,
        }

    def check_health(self) -> list:
        """Check the sensors the flexible load rule reads and the switch it writes."""
        health_check = perform_health_check(
            component_name="Flexible Load",
            description="Runs a controllable load on solar surplus",
            is_required=False,
            controller=self.controller,
            all_methods=[
                "get_flexible_load_switch_on",
                "get_flexible_load_power",
                "get_battery_soc",
                "get_export_power",
                "get_import_power",
                "get_battery_discharge_power",
            ],
        )
        # Same promotion the power monitor does: the feature is optional, but
        # once enabled every one of these reads is load-bearing -- an
        # unmapped one makes apply() fail off every minute.
        had_missing_sensor = False
        for check in health_check["checks"]:
            if check["status"] == "SKIPPED":
                had_missing_sensor = True
                check["status"] = "ERROR"
                check["error"] = (
                    "Flexible load is enabled but this sensor is not configured "
                    "— map it in Settings → Sensors, or disable the flexible "
                    "load in Settings → Home."
                )
                check["displayValue"] = "Not configured"
        if had_missing_sensor:
            health_check["status"] = "ERROR"
        return [health_check]

    def _min_off_elapsed(self) -> bool:
        if self._last_switch_at is None:
            return True
        min_off = timedelta(minutes=self.home_settings.flexible_load_min_off_minutes)
        return time_utils.now() - self._last_switch_at >= min_off

    def _switch(self, on: bool) -> None:
        logger.info("Flexible load: switching %s", "ON" if on else "OFF")
        self.controller.set_flexible_load_switch(on)
        self._last_switch_at = time_utils.now()
        self._last_switch_on = on

    def _reset_pending(self) -> None:
        self._pending_state = None
        self._pending_ticks = 0
