"""Native SolaX: LOAD_SUPPORT served by the inverter's native load support.

`solax_native_load_support_enabled` is the opt-in that ports #413's trade to
native SolaX. With it on, a LOAD_SUPPORT period disables VPP and hands the
period to the inverter's self-use logic, so the battery covers the *actual*
house deficit rather than the forecast one and a prediction miss stops turning
into a grid import or export. With it off, nothing changes.

**These are command-level assertions, deliberately.** `rules.md` asks for
outcomes over commands wherever an execution model exists -- and for native
SolaX none does: `simulation/inverter_simulator` is Growatt MIN/TOU only,
`simulation/vpp_simulator` is Growatt VPP only, and
`simulation.verification.verify_plan_faithfulness` plans under
`DEFAULT_CAPABILITIES`. There is no realized-cost or SoE trajectory to assert
against on this platform, so what these tests can prove is that the mapping is
what it claims to be. The planning half of the promise is pinned separately, by
`test_control_model_classification.py`.
"""

from unittest.mock import MagicMock

import pytest

from core.bess.settings import BatterySettings
from core.bess.solax_controller import SolaxController


@pytest.fixture
def battery_settings() -> BatterySettings:
    return BatterySettings(
        total_capacity=10.0,
        max_charge_power_kw=5.0,
        max_discharge_power_kw=5.0,
        min_soc=15.0,
        max_soc=95.0,
    )


@pytest.fixture
def controller(battery_settings: BatterySettings) -> SolaxController:
    return SolaxController(battery_settings=battery_settings)


def _apply(
    controller: SolaxController, intent: str, discharge_rate: int = 60
) -> MagicMock:
    """Drive one period through the controller and return the HA mock."""
    hw = MagicMock()
    ok, err = controller.apply_period(
        hw,
        grid_charge=False,
        discharge_rate=discharge_rate,
        strategic_intent=intent,
    )
    assert ok, err
    return hw


class TestNativeLoadSupportIsOptIn:
    def test_load_support_forces_a_watt_target_by_default(
        self, controller: SolaxController
    ) -> None:
        """The setting defaults off, so today's behaviour is untouched."""
        assert controller.battery_settings.solax_native_load_support_enabled is False

        hw = _apply(controller, "LOAD_SUPPORT", discharge_rate=60)

        hw.set_solax_vpp_disabled.assert_not_called()
        hw.set_solax_active_power_control.assert_called_once_with(-3000)

    def test_load_support_is_handed_to_the_inverter_when_enabled(
        self, controller: SolaxController
    ) -> None:
        """Enabled, LOAD_SUPPORT does what SOLAR_STORAGE does: hand the period
        back to the inverter rather than command a discharge."""
        controller.battery_settings.solax_native_load_support_enabled = True

        hw = _apply(controller, "LOAD_SUPPORT", discharge_rate=60)

        hw.set_solax_vpp_disabled.assert_called_once()
        hw.set_solax_active_power_control.assert_not_called()

    def test_the_rate_is_discarded_not_scaled(
        self, controller: SolaxController
    ) -> None:
        """No rate reaches the inverter at all -- which is what makes the
        exact-cover candidate safe to plan (see the module docstring)."""
        controller.battery_settings.solax_native_load_support_enabled = True

        for rate in (1, 37, 100):
            hw = _apply(controller, "LOAD_SUPPORT", discharge_rate=rate)
            hw.set_solax_active_power_control.assert_not_called()

    def test_the_toggle_is_read_live(self, controller: SolaxController) -> None:
        """`BatterySettings.update()` mutates in place and the controller holds
        that object, so a settings change applies without reconstructing the
        controller (#398's snapshot trap is charge/discharge power only)."""
        controller.battery_settings.update(solax_native_load_support_enabled=True)
        assert _apply(controller, "LOAD_SUPPORT").set_solax_vpp_disabled.called

        controller.battery_settings.update(solax_native_load_support_enabled=False)
        assert _apply(controller, "LOAD_SUPPORT").set_solax_active_power_control.called


class TestEveryOtherIntentIsUntouched:
    """The regression that actually matters: native load support must reach
    LOAD_SUPPORT and nothing else. BATTERY_EXPORT in particular collapses to
    the same (grid_charge, discharge_rate) as LOAD_SUPPORT, which is why the
    intent has to be threaded to the write path at all."""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_battery_export_always_forces_its_watt_target(
        self, controller: SolaxController, enabled: bool
    ) -> None:
        controller.battery_settings.solax_native_load_support_enabled = enabled

        hw = _apply(controller, "BATTERY_EXPORT", discharge_rate=100)

        hw.set_solax_active_power_control.assert_called_once_with(-5000)
        hw.set_solax_vpp_disabled.assert_not_called()

    @pytest.mark.parametrize("enabled", [False, True])
    def test_grid_charging_always_forces_its_watt_target(
        self, controller: SolaxController, enabled: bool
    ) -> None:
        controller.battery_settings.solax_native_load_support_enabled = enabled
        hw = MagicMock()

        ok, err = controller.apply_period(
            hw,
            grid_charge=True,
            discharge_rate=0,
            strategic_intent="GRID_CHARGING",
            charge_rate=80,
        )

        assert ok, err
        hw.set_solax_active_power_control.assert_called_once_with(4000)
        hw.set_solax_vpp_disabled.assert_not_called()

    @pytest.mark.parametrize("enabled", [False, True])
    def test_solar_storage_hands_over_whatever_the_flag_says(
        self, controller: SolaxController, enabled: bool
    ) -> None:
        controller.battery_settings.solax_native_load_support_enabled = enabled

        hw = _apply(controller, "SOLAR_STORAGE", discharge_rate=0)

        hw.set_solax_vpp_disabled.assert_called_once()
        hw.set_solax_active_power_control.assert_not_called()

    @pytest.mark.parametrize("enabled", [False, True])
    def test_idle_holds_whatever_the_flag_says(
        self, controller: SolaxController, enabled: bool
    ) -> None:
        """IDLE commands the no-discharge hold rather than handing the period
        over, but that is the IDLE hold's doing, not native load support's --
        what this class asserts is that the flag reaches LOAD_SUPPORT and
        nothing else, and the command must be identical either way."""
        controller.battery_settings.solax_native_load_support_enabled = enabled

        hw = _apply(controller, "IDLE", discharge_rate=0)

        hw.set_solax_no_discharge_hold.assert_called_once()
        hw.set_solax_vpp_disabled.assert_not_called()
        hw.set_solax_active_power_control.assert_not_called()


class TestDisplayMatchesTheCommand:
    """`_vpp_display_state` is what the UI reports the inverter was told. If it
    stops mirroring the write path, the dashboard shows a period that was never
    commanded."""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_display_mirrors_the_load_support_write(
        self, controller: SolaxController, enabled: bool
    ) -> None:
        controller.battery_settings.solax_native_load_support_enabled = enabled

        power_pct, remote_control = controller._vpp_display_state(
            grid_charge=False,
            discharge_rate=60,
            strategic_intent="LOAD_SUPPORT",
        )

        if enabled:
            assert (power_pct, remote_control) == (0, False)
        else:
            assert (power_pct, remote_control) == (-60, True)

    def test_display_still_reports_battery_export_as_forced(
        self, controller: SolaxController
    ) -> None:
        controller.battery_settings.solax_native_load_support_enabled = True

        assert controller._vpp_display_state(
            grid_charge=False,
            discharge_rate=100,
            strategic_intent="BATTERY_EXPORT",
        ) == (-100, True)


class TestPlanningCapabilityFollowsTheToggle:
    """The write and the plan are two halves of one promise. The DP may plan a
    partial load cover only where the inverter actually delivers
    `min(plan, actual load)` -- true exactly when native load support is on."""

    def test_exact_cover_is_declared_only_with_native_load_support(
        self, controller: SolaxController
    ) -> None:
        assert controller.load_support_delivers_exact_cover is False

        controller.battery_settings.solax_native_load_support_enabled = True
        assert controller.load_support_delivers_exact_cover is True

    def test_rate_semantics_are_unaffected(self, controller: SolaxController) -> None:
        """`discharge_rate_is_load_following` answers a different question --
        whether a *written* rate is a ceiling -- and BATTERY_EXPORT still
        writes a forced watt target either way, so it must not move."""
        controller.battery_settings.solax_native_load_support_enabled = True

        assert controller.discharge_rate_is_load_following is False
