"""Battery-to-grid export cap.

Distinct from the home's `grid_export_power_limit_kw`, which bounds *total*
feed-in. This one bounds only the battery's share, so solar may still export
freely while stored energy is held back -- the shape several grid connections
require.

The flow derivation serves home from solar first, then from the battery, and
exports whatever battery discharge is left over (core/bess/models.py). So
capping battery_to_grid is exactly bounding discharge to the load the battery
still has to cover, plus the cap.
"""

from typing import Any

import pytest

from core.bess.action_selector import _discharge_candidates
from core.bess.settings import BatterySettings

DT = 0.25


def _settings(**overrides: Any) -> BatterySettings:
    kwargs: dict[str, Any] = {
        "total_capacity": 30.0,
        "min_soc": 10,
        "max_soc": 100,
        "max_charge_power_kw": 15.0,
        "max_discharge_power_kw": 15.0,
    }
    kwargs.update(overrides)
    return BatterySettings(**kwargs)


def _max_discharge(settings: BatterySettings, home: float, solar: float) -> float:
    candidates = _discharge_candidates(
        soe=settings.max_soe_kwh,
        battery_settings=settings,
        dt=DT,
        home_consumption=home,
        solar_production=solar,
    )
    return max(candidates) if candidates else 0.0


class TestDisabled:
    def test_a_configured_cap_alone_does_not_apply(self) -> None:
        """Only the flag enables it -- so that a cap of 0.0 is expressible."""
        settings = _settings(
            max_battery_to_grid_enabled=False, max_battery_to_grid_power_kw=0.0
        )
        assert _max_discharge(settings, home=0.0, solar=0.0) > 0.0

    def test_discharge_is_bounded_only_by_power_and_energy(self) -> None:
        settings = _settings(max_battery_to_grid_power_kw=0.0)
        assert _max_discharge(settings, home=0.0, solar=0.0) == pytest.approx(
            15.0, abs=0.2
        )


class TestEnabled:
    def test_zero_means_the_battery_never_exports(self) -> None:
        """The case the flag exists for: cover load, export nothing."""
        settings = _settings(
            max_battery_to_grid_enabled=True, max_battery_to_grid_power_kw=0.0
        )
        assert _max_discharge(settings, home=0.0, solar=0.0) == 0.0

    def test_zero_still_allows_covering_home_load(self) -> None:
        """Capping export must not stop the battery serving the house."""
        settings = _settings(
            max_battery_to_grid_enabled=True, max_battery_to_grid_power_kw=0.0
        )
        # 2 kW of load over a quarter hour the battery may still cover.
        assert _max_discharge(settings, home=2.0 * DT, solar=0.0) == pytest.approx(
            2.0, abs=0.2
        )

    def test_a_nonzero_cap_allows_exactly_that_much_export(self) -> None:
        settings = _settings(
            max_battery_to_grid_enabled=True, max_battery_to_grid_power_kw=3.0
        )
        assert _max_discharge(settings, home=0.0, solar=0.0) == pytest.approx(
            3.0, abs=0.2
        )

    def test_the_cap_stacks_on_top_of_the_load_the_battery_covers(self) -> None:
        """Load the battery serves is not export, so it is not capped."""
        settings = _settings(
            max_battery_to_grid_enabled=True, max_battery_to_grid_power_kw=3.0
        )
        assert _max_discharge(settings, home=2.0 * DT, solar=0.0) == pytest.approx(
            5.0, abs=0.2
        )

    def test_solar_covering_the_load_leaves_only_the_cap(self) -> None:
        """Solar serves home first, so the battery's remaining room is export
        only -- the cap then binds on its own."""
        settings = _settings(
            max_battery_to_grid_enabled=True, max_battery_to_grid_power_kw=3.0
        )
        home = 2.0 * DT
        assert _max_discharge(settings, home=home, solar=home) == pytest.approx(
            3.0, abs=0.2
        )

    def test_it_never_raises_the_discharge_limit(self) -> None:
        """A cap above the battery's own power limit changes nothing."""
        settings = _settings(
            max_discharge_power_kw=5.0,
            max_battery_to_grid_enabled=True,
            max_battery_to_grid_power_kw=50.0,
        )
        assert _max_discharge(settings, home=0.0, solar=0.0) == pytest.approx(
            5.0, abs=0.2
        )


class TestSettingsValidation:
    def test_a_negative_cap_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_battery_to_grid_power_kw"):
            _settings(max_battery_to_grid_power_kw=-1.0)
