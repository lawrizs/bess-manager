"""Unit tests for the flexible load (solar-surplus water heater) controller.

There is no execution model for the heater -- the inverter simulator knows
nothing about a resistive load behind a Tasmota relay -- so these tests pin
the switch commands the controller issues over a sequence of live readings.
The physics they stand in for: a heater that only ever runs on solar that
would otherwise be exported, and stops before the battery or the grid pays
for it.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from core.bess import time_utils
from core.bess.flexible_load import (
    FlexibleLoadController,
    desired_switch_state,
)
from core.bess.settings import BatterySettings, HomeSettings

RATED_W = 2200.0
MARGIN_W = 200.0
FULL_SOC = 94.0  # battery max_soc 95 minus the 1 pp hover tolerance


def _decide(**overrides: float) -> bool:
    readings = {
        "load_power_w": 0.0,
        "battery_soc": 95.0,
        "export_power_w": 3000.0,
        "import_power_w": 0.0,
        "battery_discharge_power_w": 0.0,
        "rated_power_w": RATED_W,
        "surplus_margin_w": MARGIN_W,
        "battery_full_soc": FULL_SOC,
    }
    readings.update(overrides)
    return desired_switch_state(**readings)


class TestDesiredSwitchState:
    def test_idle_heater_turns_on_when_battery_full_and_export_covers_it(self) -> None:
        assert _decide(export_power_w=RATED_W + MARGIN_W) is True

    def test_idle_heater_stays_off_when_export_is_just_short(self) -> None:
        assert _decide(export_power_w=RATED_W + MARGIN_W - 1) is False

    def test_idle_heater_stays_off_when_battery_is_not_full(self) -> None:
        assert _decide(battery_soc=FULL_SOC - 0.5) is False

    def test_idle_heater_turns_on_at_the_full_soc_tolerance(self) -> None:
        assert _decide(battery_soc=FULL_SOC) is True

    def test_running_heater_keeps_running_while_nothing_else_pays(self) -> None:
        # Once the heater absorbs the surplus, export drops to ~0 and the
        # battery neither charges nor discharges -- that is the steady state.
        assert (
            _decide(load_power_w=2168.0, export_power_w=50.0, battery_soc=93.0) is True
        )

    def test_running_heater_stops_when_the_battery_starts_paying(self) -> None:
        assert (
            _decide(
                load_power_w=2168.0,
                export_power_w=0.0,
                battery_discharge_power_w=MARGIN_W + 1,
            )
            is False
        )

    def test_running_heater_stops_when_the_grid_starts_paying(self) -> None:
        assert (
            _decide(
                load_power_w=2168.0, export_power_w=0.0, import_power_w=MARGIN_W + 1
            )
            is False
        )

    def test_running_heater_tolerates_draw_within_the_margin(self) -> None:
        assert (
            _decide(
                load_power_w=2168.0,
                export_power_w=0.0,
                battery_discharge_power_w=MARGIN_W,
            )
            is True
        )

    def test_thermostat_satisfied_heater_is_judged_as_idle(self) -> None:
        # Switch on, element off: no draw, so the on-rule applies again and
        # a vanished surplus turns the relay off before the thermostat can
        # re-engage against grid or battery.
        assert _decide(load_power_w=5.0, export_power_w=500.0) is False
        assert _decide(load_power_w=5.0, export_power_w=RATED_W + MARGIN_W) is True


def _stub_controller(
    *,
    switch_on: bool | None = False,
    load_power_w: float | None = 0.0,
    battery_soc: float | None = 95.0,
    export_power_w: float | None = 3000.0,
    import_power_w: float | None = 0.0,
    battery_discharge_power_w: float | None = 0.0,
) -> MagicMock:
    controller = MagicMock()
    controller.get_flexible_load_switch_on.return_value = switch_on
    controller.get_flexible_load_power.return_value = load_power_w
    controller.get_battery_soc.return_value = battery_soc
    controller.get_export_power.return_value = export_power_w
    controller.get_import_power.return_value = import_power_w
    controller.get_battery_discharge_power.return_value = battery_discharge_power_w
    return controller


def _make(controller: MagicMock) -> FlexibleLoadController:
    home = HomeSettings(
        flexible_load_enabled=True,
        flexible_load_rated_power_w=RATED_W,
        flexible_load_surplus_margin_w=MARGIN_W,
        flexible_load_min_off_minutes=5,
    )
    battery = BatterySettings(max_soc=95.0)
    return FlexibleLoadController(
        controller, home_settings=home, battery_settings=battery
    )


def _switch_commands(controller: MagicMock) -> list[bool]:
    return [c.args[0] for c in controller.set_flexible_load_switch.call_args_list]


class TestControllerSwitching:
    def test_surplus_must_persist_for_two_ticks_before_turning_on(self) -> None:
        controller = _stub_controller()
        ctl = _make(controller)

        ctl.apply()
        assert _switch_commands(controller) == []
        ctl.apply()
        assert _switch_commands(controller) == [True]

    def test_a_single_tick_of_surplus_does_not_turn_on(self) -> None:
        controller = _stub_controller()
        ctl = _make(controller)

        ctl.apply()
        controller.get_export_power.return_value = 500.0
        ctl.apply()
        controller.get_export_power.return_value = 3000.0
        ctl.apply()
        assert _switch_commands(controller) == []

    def test_no_write_when_switch_already_matches(self) -> None:
        controller = _stub_controller(
            switch_on=True, load_power_w=2168.0, export_power_w=100.0
        )
        ctl = _make(controller)

        for _ in range(5):
            ctl.apply()
        assert _switch_commands(controller) == []

    def test_deficit_for_two_ticks_turns_off(self) -> None:
        controller = _stub_controller(
            switch_on=True,
            load_power_w=2168.0,
            export_power_w=0.0,
            battery_discharge_power_w=800.0,
        )
        ctl = _make(controller)

        ctl.apply()
        assert _switch_commands(controller) == []
        ctl.apply()
        assert _switch_commands(controller) == [False]

    def test_turn_on_waits_out_the_minimum_off_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        controller = _stub_controller(
            switch_on=True,
            load_power_w=2168.0,
            export_power_w=0.0,
            battery_discharge_power_w=800.0,
        )
        ctl = _make(controller)
        t0 = time_utils.now()
        monkeypatch.setattr(time_utils, "now", lambda: t0)

        ctl.apply()
        ctl.apply()
        assert _switch_commands(controller) == [False]

        # Sun is back straight away: the relay must not be cycled within 5 min.
        controller.get_flexible_load_switch_on.return_value = False
        controller.get_flexible_load_power.return_value = 0.0
        controller.get_export_power.return_value = 3000.0
        controller.get_battery_discharge_power.return_value = 0.0
        monkeypatch.setattr(time_utils, "now", lambda: t0 + timedelta(minutes=3))
        ctl.apply()
        ctl.apply()
        ctl.apply()
        assert _switch_commands(controller) == [False]

        monkeypatch.setattr(time_utils, "now", lambda: t0 + timedelta(minutes=5))
        ctl.apply()
        assert _switch_commands(controller) == [False, True]

    def test_turn_off_is_not_delayed_by_the_minimum_off_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        controller = _stub_controller()
        ctl = _make(controller)
        t0 = time_utils.now()
        monkeypatch.setattr(time_utils, "now", lambda: t0)
        ctl.apply()
        ctl.apply()
        assert _switch_commands(controller) == [True]

        controller.get_flexible_load_switch_on.return_value = True
        controller.get_flexible_load_power.return_value = 2168.0
        controller.get_export_power.return_value = 0.0
        controller.get_battery_discharge_power.return_value = 1500.0
        monkeypatch.setattr(time_utils, "now", lambda: t0 + timedelta(minutes=1))
        ctl.apply()
        ctl.apply()
        assert _switch_commands(controller) == [True, False]

    def test_unreadable_power_reading_fails_off_immediately(self) -> None:
        controller = _stub_controller(
            switch_on=True, load_power_w=2168.0, export_power_w=100.0
        )
        controller.get_battery_discharge_power.return_value = None
        ctl = _make(controller)

        ctl.apply()
        assert _switch_commands(controller) == [False]

    def test_unreadable_reading_with_switch_already_off_writes_nothing(self) -> None:
        controller = _stub_controller()
        controller.get_export_power.return_value = None
        ctl = _make(controller)

        ctl.apply()
        assert _switch_commands(controller) == []

    def test_unreadable_switch_state_writes_nothing(self) -> None:
        controller = _stub_controller()
        controller.get_flexible_load_switch_on.return_value = None
        ctl = _make(controller)

        ctl.apply()
        ctl.apply()
        assert _switch_commands(controller) == []

    def test_status_reflects_the_last_tick(self) -> None:
        controller = _stub_controller(
            switch_on=True, load_power_w=2168.0, export_power_w=100.0
        )
        ctl = _make(controller)

        ctl.apply()
        status = ctl.status()
        assert status["switch_on"] is True
        assert status["power_w"] == pytest.approx(2168.0)

    def test_status_reflects_a_command_just_issued(self) -> None:
        controller = _stub_controller(
            switch_on=True, load_power_w=2168.0, export_power_w=100.0
        )
        controller.get_export_power.return_value = None
        ctl = _make(controller)

        ctl.apply()
        assert _switch_commands(controller) == [False]
        assert ctl.status()["switch_on"] is False


class TestHomeSettingsValidation:
    def test_defaults_are_disabled_and_valid(self) -> None:
        home = HomeSettings()
        assert home.flexible_load_enabled is False
        assert home.flexible_load_rated_power_w > 0

    @pytest.mark.parametrize(
        "field, value",
        [
            ("flexible_load_rated_power_w", 0.0),
            ("flexible_load_surplus_margin_w", -1.0),
            ("flexible_load_min_off_minutes", -1),
        ],
    )
    def test_rejects_nonsense_values(self, field: str, value: float) -> None:
        home = HomeSettings()
        with pytest.raises(ValueError, match=field):
            home.update(**{field: value})
