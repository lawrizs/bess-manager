"""CONTROL_MODEL must correctly classify every controller's real hardware model."""

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from core.bess.growatt_min_controller import GrowattMinController
from core.bess.growatt_sph_controller import GrowattSphController
from core.bess.huawei_controller import HuaweiController
from core.bess.solax_controller import SolaxController
from core.bess.solax_modbus_growatt_controller import SolaxModbusGrowattController
from core.bess.solis_modbus_controller import SolisModbusController
from core.bess.tests.helpers import make_battery_settings


def _make(cls, battery_settings=None, **kwargs):
    return cls(
        battery_settings=battery_settings or make_battery_settings(),
        **kwargs,
    )


def test_growatt_min_is_tou_register():
    controller = _make(GrowattMinController)
    assert controller.CONTROL_MODEL == "tou_register"


def test_solax_modbus_growatt_tou_mode_is_tou_register():
    controller = _make(SolaxModbusGrowattController, control_mode="tou")
    assert controller.CONTROL_MODEL == "tou_register"


def test_solax_modbus_growatt_vpp_mode_is_vpp_power():
    controller = _make(SolaxModbusGrowattController, control_mode="vpp")
    assert controller.CONTROL_MODEL == "vpp_power"


def test_solax_controller_is_vpp_power():
    controller = _make(SolaxController)
    assert controller.CONTROL_MODEL == "vpp_power"


def test_growatt_sph_is_period_list():
    controller = _make(GrowattSphController)
    assert controller.CONTROL_MODEL == "period_list"


def test_solis_modbus_is_period_list():
    controller = _make(SolisModbusController)
    assert controller.CONTROL_MODEL == "period_list"


def test_huawei_is_period_list():
    controller = _make(HuaweiController)
    assert controller.CONTROL_MODEL == "period_list"


# ── The two capability flags, and what binds them (Phase 4b) ────────────────


def _exact_cover_controllers():
    """Every shipped controller configuration, as (label, instance)."""
    return [
        ("growatt_min", _make(GrowattMinController)),
        (
            "solax_modbus_growatt/tou",
            _make(SolaxModbusGrowattController, control_mode="tou"),
        ),
        (
            "solax_modbus_growatt/vpp",
            _make(SolaxModbusGrowattController, control_mode="vpp"),
        ),
        ("solax_native", _make(SolaxController)),
        (
            "solax_native/native_load_support",
            _make(
                SolaxController,
                battery_settings=make_battery_settings(
                    solax_native_load_support_enabled=True
                ),
            ),
        ),
        ("growatt_sph", _make(GrowattSphController)),
        ("solis_modbus", _make(SolisModbusController)),
        ("huawei", _make(HuaweiController)),
    ]


def test_exact_cover_is_only_declared_where_the_written_rate_can_deliver_it():
    """Two different flags decide two halves of the same promise, and nothing
    in the type system stops them diverging.

    `load_support_delivers_exact_cover` decides whether the DP may plan a
    partial load cover (`action_selector._residual_cover_p`).
    `discharge_rate_is_load_following` decides whether the written rate is
    rounded UP as a ceiling or to nearest as a target
    (`execution_model.command_index`). If a platform ever declares
    cover=True with a *target* rate, the DP plans a delivery the write path
    rounds to something else -- R != P, the #282 shape this phase exists to
    end.

    Two configurations are in that combination today, and both are safe for
    the same reason, which lives in a third file: they write *no* LOAD_SUPPORT
    rate at all -- the period is handed to the inverter's own self-use
    load-following, so there is no rounding to get wrong. solax-modbus Growatt
    in VPP mode does it unconditionally (#413); native SolaX does it only when
    `solax_native_load_support_enabled` is set, which is why that platform
    appears twice in the fixture list and only the released one may declare
    cover. This test pins the reasoning per configuration, so a new platform
    cannot inherit the combination without someone proving the same thing.
    """
    # Each releasing configuration proves its own release, in the vocabulary
    # its own write path speaks. A configuration reaching the cover-with-
    # target-rate combination without an entry here fails below.
    provers: dict[str, Callable[[Any], None]] = {
        "solax_modbus_growatt/vpp": _growatt_vpp_discards_the_rate,
        "solax_native/native_load_support": _solax_native_discards_the_rate,
    }

    # Anti-vacuity: this test is a loop of `continue`s, so it would pass
    # just as happily if the interesting case stopped existing. Count it.
    checked = 0
    for label, controller in _exact_cover_controllers():
        if not controller.load_support_delivers_exact_cover:
            continue
        if controller.discharge_rate_is_load_following:
            continue  # ceiling: rounded up, delivers the cover exactly
        checked += 1
        assert label in provers, (
            f"{label} declares load_support_delivers_exact_cover=True while "
            f"its discharge rate is a forced power. The DP will plan a "
            f"partial load cover this platform cannot deliver unless it "
            f"discards the LOAD_SUPPORT rate entirely -- prove that here."
        )
        provers[label](controller)

    assert checked == len(provers), (
        f"expected {len(provers)} cover-with-target-rate configurations to "
        f"check, found {checked}. If one stopped reaching the combination its "
        f"prover is now dead weight -- remove it rather than leaving this "
        f"green and under-checking."
    )


def _growatt_vpp_discards_the_rate(controller: SolaxModbusGrowattController) -> None:
    vpp_power, remote_control = controller._intent_to_vpp(
        strategic_intent="LOAD_SUPPORT",
        discharge_rate=57,  # any value; it must not reach the inverter
        grid_charge=False,
        block_passive_charging=False,
    )
    assert (vpp_power, remote_control) == (0, False), (
        "VPP LOAD_SUPPORT must release the period to native load-following "
        "(#413). If it starts writing a rate, the exact-cover candidate "
        "has to be withdrawn from this platform."
    )


def _solax_native_discards_the_rate(controller: SolaxController) -> None:
    hardware = MagicMock()
    controller.apply_period(
        hardware,
        grid_charge=False,
        discharge_rate=57,  # any value; it must not reach the inverter
        strategic_intent="LOAD_SUPPORT",
    )
    hardware.set_solax_vpp_disabled.assert_called_once()
    hardware.set_solax_active_power_control.assert_not_called()
