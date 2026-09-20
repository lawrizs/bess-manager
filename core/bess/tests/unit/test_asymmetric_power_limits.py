"""Asymmetric charge/discharge power limits.

The two caps were one UI field for a long time, so every install had them
equal and nothing exercised the case where they differ. These pin the pieces
that only matter once they do.
"""

import numpy as np

from core.bess.action_selector import _discharge_candidates
from core.bess.dp_battery_algorithm import (
    _discretize_state_action_space,
    _run_dynamic_programming,
    _state_transition_grid,
)
from core.bess.settings import BatterySettings


def _settings(charge: float, discharge: float) -> BatterySettings:
    return BatterySettings(
        total_capacity=30.0,
        min_soc=10,
        max_soc=100,
        max_charge_power_kw=charge,
        max_discharge_power_kw=discharge,
    )


class TestActionLattice:
    """The lattice spans max(charge, discharge) in both directions, so on its
    own it cannot bound the smaller side -- the feasibility mask has to."""

    def test_lattice_reaches_the_larger_of_the_two_limits(self) -> None:
        _, power = _discretize_state_action_space(_settings(15.0, 5.0))
        assert power.max() > 5.0
        assert power.min() < -5.0


class TestBackwardPassRespectsBothLimits:
    """The backward pass values states over the lattice. If it values actions
    the hardware cannot perform, the forward replay -- which does cap
    correctly -- picks its plan against an optimistic value function."""

    def _value_function(self, charge: float, discharge: float) -> np.ndarray:
        """Solve over a cheap-then-expensive spread, so banking energy early
        and delivering it late is worth doing. Under flat prices the optimal
        policy never cycles, both caps stay slack, and the comparison below
        would pass for the wrong reason."""
        horizon = 8
        buy_price = [0.1] * 4 + [2.0] * 4
        return _run_dynamic_programming(
            horizon=horizon,
            buy_price=buy_price,
            sell_price=[p * 0.5 for p in buy_price],
            home_consumption=[2.0] * horizon,
            battery_settings=_settings(charge, discharge),
            dt=0.25,
            solar_production=[0.0] * horizon,
        )

    def test_a_smaller_discharge_limit_lowers_the_value_function(self) -> None:
        """A battery that can only discharge at 5 kW is worth no more than one
        that can discharge at 15 kW, all else equal. If the mask ignores the
        configured cap, both solve over the same actions and the values match
        exactly -- which is the bug."""
        generous = self._value_function(charge=15.0, discharge=15.0)
        limited = self._value_function(charge=15.0, discharge=5.0)

        assert limited.shape == generous.shape
        assert np.all(limited <= generous + 1e-9)
        assert not np.allclose(limited, generous)

    def test_store_physics_bound_charge_without_needing_a_mask(self) -> None:
        """Why the charge side needs no feasibility mask of its own.

        STORE is binary in the power value: _state_transition_grid bounds the
        transfer by `max_charge_power_kw * dt` itself, so a lattice level above
        the charge cap transitions exactly like the cap instead of
        over-charging. That is what makes the unmasked charge levels harmless
        duplicates rather than over-valued actions -- if this ever stops
        holding, the charge side needs the same treatment as discharge."""
        settings = _settings(charge=5.0, discharge=15.0)
        dt = 0.25
        soe = np.array([[10.0]])
        at_cap = _state_transition_grid(
            soe,
            np.array([[5.0]]),
            settings,
            dt,
            home_consumption=2.0,
            solar_production=0.0,
        )
        above_cap = _state_transition_grid(
            soe,
            np.array([[15.0]]),
            settings,
            dt,
            home_consumption=2.0,
            solar_production=0.0,
        )
        next_at = float(np.ravel(at_cap[0] if isinstance(at_cap, tuple) else at_cap)[0])
        next_above = float(
            np.ravel(above_cap[0] if isinstance(above_cap, tuple) else above_cap)[0]
        )

        assert next_at == next_above
        gain = next_at - 10.0
        assert gain <= 5.0 * dt * settings.efficiency_charge + 1e-9


class TestForwardReplayAlreadyCaps:
    """Regression guard: the replay pass has always bounded discharge by its
    own limit, and must keep doing so."""

    def test_discharge_candidates_never_exceed_the_discharge_limit(self) -> None:
        settings = _settings(charge=15.0, discharge=5.0)
        candidates = _discharge_candidates(
            soe=settings.max_soe_kwh,
            battery_settings=settings,
            dt=0.25,
            home_consumption=0.5,
            solar_production=0.0,
        )
        assert candidates, "expected a non-empty candidate set at full SOE"
        assert max(candidates) <= 5.0 + 1e-9
