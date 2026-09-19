"""Plan-faithfulness regression for the DP's grid-export capacity constraint.

Many grid connections carry a DSO feed-in ceiling. The DP modelled the
inverter's AC-output cap and the house fuse's import cap but had no ceiling on
`grid_exported`, so on a sunny low-load day it planned to push the full solar
surplus -- and any battery discharge stacked on top of it -- out to a grid that
will not accept it.

The cap is carried as a tightening of the AC-output cap (`_period_ac_cap_kwh`),
which is why there is no separate export constraint to inspect. What these
tests pin is the outcome: no planned period exports above the limit, from
either source, and surplus the limit excludes is stored or reported as clipped
rather than credited as revenue.
"""

from typing import Any

import pytest

from core.bess.dp_battery_algorithm import (
    _effective_export_cap_kwh,
    _period_ac_cap_kwh,
)
from core.bess.settings import HomeSettings
from core.bess.tests.helpers import run_scenario, run_scenario_realized

EXPORT_LIMIT_KW = 3.0
EXPORT_CAP_KWH = EXPORT_LIMIT_KW * 1.0  # dt = 1.0 in both scenarios below


def test_effective_export_cap_is_off_by_default_and_scales_with_period_length() -> None:
    assert _effective_export_cap_kwh(HomeSettings(), dt=1.0) is None
    assert _effective_export_cap_kwh(None, dt=1.0) is None

    home = HomeSettings(grid_export_power_limit_kw=EXPORT_LIMIT_KW)
    assert _effective_export_cap_kwh(home, dt=1.0) == pytest.approx(3.0)
    assert _effective_export_cap_kwh(home, dt=0.25) == pytest.approx(0.75)


def test_effective_export_cap_ignores_power_monitoring() -> None:
    """A feed-in ceiling is a property of the grid connection, not of the
    fuse-protection feature -- unlike the import cap, which is derived from the
    fuse and is off whenever monitoring is."""
    home = HomeSettings(
        grid_export_power_limit_kw=EXPORT_LIMIT_KW, power_monitoring_enabled=False
    )
    assert _effective_export_cap_kwh(home, dt=1.0) == pytest.approx(3.0)


def test_period_ac_cap_takes_the_tighter_of_the_two_ceilings() -> None:
    """`grid_exported = ac_output - home_consumption`, so an export ceiling is
    an AC-output ceiling of `home_consumption + export_cap`. Whichever of the
    two binds first is what the period runs under."""
    assert _period_ac_cap_kwh(5.0, home_consumption=1.0, export_cap_kwh=None) == 5.0
    assert _period_ac_cap_kwh(None, home_consumption=1.0, export_cap_kwh=None) is None

    # No inverter cap: the export ceiling alone bounds AC output.
    assert _period_ac_cap_kwh(None, home_consumption=1.0, export_cap_kwh=3.0) == 4.0
    assert _period_ac_cap_kwh(3.5, home_consumption=1.0, export_cap_kwh=3.0) == 3.5
    assert _period_ac_cap_kwh(9.0, home_consumption=1.0, export_cap_kwh=3.0) == 4.0
    # A bigger house load leaves more room under the same feed-in ceiling.
    assert _period_ac_cap_kwh(9.0, home_consumption=4.0, export_cap_kwh=3.0) == 7.0


def _uncapped(scenario: dict[str, Any]) -> dict[str, Any]:
    return {**scenario, "home": {**scenario["home"], "grid_export_power_limit_kw": 0.0}}


# --------------------------------------------------------------------------
# Solar-sourced export: the surplus that passes straight through the inverter.
#
# Midday surplus is 8 kWh/period against a 3 kW ceiling. The cheap evening
# (buy 0.4) is worth less than the 0.5 export the surplus would otherwise earn,
# so an uncapped day exports it all and stores nothing. Under the ceiling that
# export is unavailable, absorbing costs only wear, and storing becomes the
# better use of energy that would otherwise be clipped.
# --------------------------------------------------------------------------
SOLAR_SURPLUS_SCENARIO: dict[str, Any] = {
    "battery": {
        "max_soe_kwh": 10.0,
        "min_soe_kwh": 1.0,
        "max_charge_power_kw": 5.0,
        "max_discharge_power_kw": 10.0,
        "efficiency_charge": 1.0,
        "efficiency_discharge": 1.0,
        "cycle_cost_per_kwh": 0.10,
        "initial_soe": 1.0,
    },
    "home": {
        "phase_count": 3,
        "power_monitoring_enabled": False,
        "grid_export_power_limit_kw": EXPORT_LIMIT_KW,
    },
    "buy_price": [1.0, 1.0, 1.0, 0.4],
    "sell_price": [0.5, 0.5, 0.5, 0.2],
    "home_consumption": [1.0, 1.0, 1.0, 6.0],
    "solar_production": [0.0, 9.0, 9.0, 0.0],
    "period_duration_hours": 1.0,
}

# --------------------------------------------------------------------------
# Battery-sourced export: a full battery and a late price spike, with no solar
# anywhere. Nothing but the ceiling bounds how hard the battery may push into
# the grid.
# --------------------------------------------------------------------------
BATTERY_EXPORT_SCENARIO: dict[str, Any] = {
    "battery": {
        **SOLAR_SURPLUS_SCENARIO["battery"],
        "initial_soe": 10.0,
    },
    "home": {**SOLAR_SURPLUS_SCENARIO["home"]},
    "buy_price": [1.0, 1.0, 1.0, 1.0],
    "sell_price": [0.1, 0.1, 0.1, 2.0],
    "home_consumption": [1.0, 1.0, 1.0, 1.0],
    "solar_production": [0.0, 0.0, 0.0, 0.0],
    "period_duration_hours": 1.0,
}


@pytest.mark.parametrize(
    "scenario",
    [SOLAR_SURPLUS_SCENARIO, BATTERY_EXPORT_SCENARIO],
    ids=["solar_surplus", "battery_export"],
)
def test_no_planned_period_exports_above_the_limit(scenario: dict[str, Any]) -> None:
    result, realized_cost = run_scenario_realized(scenario)

    assert realized_cost == pytest.approx(
        result.economic_summary.battery_solar_cost, abs=0.01
    ), "Plan is not faithfully executable (R != P)"

    for t, period in enumerate(result.period_data):
        assert period.energy.grid_exported <= EXPORT_CAP_KWH + 0.01, (
            f"Period {t} exports {period.energy.grid_exported:.2f} kWh, above "
            f"the {EXPORT_CAP_KWH:.2f} kWh feed-in ceiling"
        )


@pytest.mark.parametrize(
    "scenario",
    [SOLAR_SURPLUS_SCENARIO, BATTERY_EXPORT_SCENARIO],
    ids=["solar_surplus", "battery_export"],
)
def test_the_fixtures_actually_exercise_the_cap(scenario: dict[str, Any]) -> None:
    """Guard: uncapped, each of these days exports well above the ceiling, so
    the assertions above measure the constraint rather than a day that never
    reached it."""
    result = run_scenario(_uncapped(scenario))

    assert any(
        period.energy.grid_exported > EXPORT_CAP_KWH + 0.01
        for period in result.period_data
    ), "fixture no longer exports above the ceiling when uncapped"


def test_surplus_the_ceiling_excludes_is_stored_rather_than_exported() -> None:
    """The ceiling must change decisions, not just readings: energy that cannot
    leave is worth storing, so the capped day ends up having absorbed solar the
    uncapped day sold."""
    capped = run_scenario(SOLAR_SURPLUS_SCENARIO)
    uncapped = run_scenario(_uncapped(SOLAR_SURPLUS_SCENARIO))

    capped_stored = sum(p.energy.battery_charged for p in capped.period_data)
    uncapped_stored = sum(p.energy.battery_charged for p in uncapped.period_data)
    assert capped_stored > uncapped_stored + 1.0, (
        f"capped run stored {capped_stored:.2f} kWh vs {uncapped_stored:.2f} "
        "uncapped -- the ceiling is not making absorption more valuable"
    )


def test_solar_the_ceiling_excludes_is_clipped_not_credited() -> None:
    """Surplus that neither the house, the battery nor the ceiling can take is
    lost. It must be reported as clipped rather than booked as export revenue,
    or the plan promises income the connection will not deliver."""
    capped = run_scenario(SOLAR_SURPLUS_SCENARIO)
    uncapped = run_scenario(_uncapped(SOLAR_SURPLUS_SCENARIO))

    assert sum(p.energy.clipped_solar for p in uncapped.period_data) == pytest.approx(
        0.0, abs=1e-9
    )
    assert sum(p.energy.clipped_solar for p in capped.period_data) > 0.0, (
        "the ceiling excluded no solar at all -- with 8 kWh of surplus per "
        "period against a 3 kWh ceiling and 9 kWh of battery room, some PV "
        "cannot be saved"
    )
