"""BatterySystemManager wiring for the flexible load controller."""

from unittest.mock import MagicMock

import pytest

from core.bess.battery_system_manager import BatterySystemManager
from core.bess.ha_api_controller import HomeAssistantAPIController
from core.bess.price_manager import MockSource

_DEFAULT_OPTIONS = {"inverter": {"platform": "growatt_server_min"}}


@pytest.fixture
def system(mock_controller: HomeAssistantAPIController) -> BatterySystemManager:
    return BatterySystemManager(
        controller=mock_controller,
        price_source=MockSource([1.0] * 96),
        addon_options=_DEFAULT_OPTIONS,
    )


def test_disabled_by_default_and_tick_touches_nothing(
    system: BatterySystemManager,
    mock_controller: HomeAssistantAPIController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert system._flexible_load is None
    switch = MagicMock()
    monkeypatch.setattr(mock_controller, "set_flexible_load_switch", switch)
    system.apply_flexible_load()
    switch.assert_not_called()


def test_enabling_at_runtime_creates_the_controller(
    system: BatterySystemManager,
) -> None:
    system.update_settings({"home": {"flexible_load_enabled": True}})
    assert system._flexible_load is not None


def test_disabling_at_runtime_drops_the_controller_without_touching_the_switch(
    system: BatterySystemManager,
    mock_controller: HomeAssistantAPIController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    switch = MagicMock()
    monkeypatch.setattr(mock_controller, "set_flexible_load_switch", switch)
    system.update_settings({"home": {"flexible_load_enabled": True}})
    system.update_settings({"home": {"flexible_load_enabled": False}})
    assert system._flexible_load is None
    switch.assert_not_called()


def test_tick_delegates_to_the_controller_when_enabled(
    system: BatterySystemManager,
) -> None:
    system.update_settings({"home": {"flexible_load_enabled": True}})
    system._flexible_load = MagicMock()
    system.apply_flexible_load()
    system._flexible_load.apply.assert_called_once()


def test_status_when_disabled_reports_only_the_flag(
    system: BatterySystemManager,
) -> None:
    assert system.flexible_load_status() == {"enabled": False}
