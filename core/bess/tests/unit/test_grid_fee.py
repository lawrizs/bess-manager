"""Time-of-use grid fee: zone mapping and its effect on the buy price."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from core.bess import grid_fee
from core.bess.price_manager import MockSource, PriceManager

# 2026-09-14 is a Monday, so +0..+4 are weekdays and +5/+6 the weekend.
MONDAY = date(2026, 9, 14)
SATURDAY = date(2026, 9, 19)
SUNDAY = date(2026, 9, 20)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


class TestWeekdayZones:
    @pytest.mark.parametrize(
        "hour, expected",
        [
            (0, grid_fee.ZONE_NIGHT),
            (4, grid_fee.ZONE_NIGHT),
            (5, grid_fee.ZONE_MORNING),
            (6, grid_fee.ZONE_MORNING),
            (7, grid_fee.ZONE_DAY),
            (16, grid_fee.ZONE_DAY),
            (17, grid_fee.ZONE_EVENING),
            (21, grid_fee.ZONE_EVENING),
            (22, grid_fee.ZONE_NIGHT),
            (23, grid_fee.ZONE_NIGHT),
        ],
    )
    def test_each_hour_maps_to_its_published_zone(
        self, hour: int, expected: str
    ) -> None:
        assert grid_fee.zone_for(_at(MONDAY, hour)) == expected

    def test_every_weekday_shares_the_schedule(self) -> None:
        for offset in range(5):
            day = MONDAY + timedelta(days=offset)
            assert grid_fee.zone_for(_at(day, 18)) == grid_fee.ZONE_EVENING

    def test_boundary_belongs_to_the_zone_it_opens(self) -> None:
        """17:00 is evening, 16:45 is still day — the boundary is inclusive-left."""
        assert grid_fee.zone_for(_at(MONDAY, 16, 45)) == grid_fee.ZONE_DAY
        assert grid_fee.zone_for(_at(MONDAY, 17, 0)) == grid_fee.ZONE_EVENING


class TestWeekendZones:
    @pytest.mark.parametrize("day", [SATURDAY, SUNDAY])
    @pytest.mark.parametrize(
        "hour, expected",
        [
            (0, grid_fee.ZONE_NIGHT),
            (6, grid_fee.ZONE_NIGHT),
            (7, grid_fee.ZONE_DAY),
            (12, grid_fee.ZONE_DAY),
            (18, grid_fee.ZONE_DAY),
            (21, grid_fee.ZONE_DAY),
            (22, grid_fee.ZONE_NIGHT),
        ],
    )
    def test_weekend_has_only_night_and_day(
        self, day: date, hour: int, expected: str
    ) -> None:
        assert grid_fee.zone_for(_at(day, hour)) == expected

    @pytest.mark.parametrize("day", [SATURDAY, SUNDAY])
    def test_morning_and_evening_never_apply(self, day: date) -> None:
        zones = {grid_fee.zone_for(_at(day, hour)) for hour in range(24)}
        assert grid_fee.ZONE_MORNING not in zones
        assert grid_fee.ZONE_EVENING not in zones


def _price_manager(**overrides: Any) -> PriceManager:
    kwargs: dict[str, Any] = {
        "price_source": MockSource([1.0] * 96),
        "markup_rate": 0.0,
        "vat_multiplier": 1.0,
        "additional_costs": 0.0,
        "tax_reduction": 0.0,
        "area": "LT",
    }
    kwargs.update(overrides)
    return PriceManager(**kwargs)


LT_RATES: dict[str, float] = {
    "grid_fee_night": 0.06292,
    "grid_fee_morning": 0.08349,
    "grid_fee_day": 0.10406,
    "grid_fee_evening": 0.14641,
}


class TestBuyPriceEffect:
    def test_disabled_leaves_the_buy_price_untouched(self) -> None:
        pm = _price_manager(grid_fee_enabled=False, **LT_RATES)
        assert pm._calculate_buy_price(1.0, _at(MONDAY, 18)) == pytest.approx(1.0)

    def test_enabled_adds_the_rate_for_the_period_zone(self) -> None:
        pm = _price_manager(grid_fee_enabled=True, **LT_RATES)

        assert pm._calculate_buy_price(1.0, _at(MONDAY, 3)) == pytest.approx(1.06292)
        assert pm._calculate_buy_price(1.0, _at(MONDAY, 6)) == pytest.approx(1.08349)
        assert pm._calculate_buy_price(1.0, _at(MONDAY, 10)) == pytest.approx(1.10406)
        assert pm._calculate_buy_price(1.0, _at(MONDAY, 18)) == pytest.approx(1.14641)

    def test_fee_stacks_on_top_of_the_flat_additional_costs(self) -> None:
        """The two are independent components, not alternatives."""
        pm = _price_manager(additional_costs=0.25, grid_fee_enabled=True, **LT_RATES)
        assert pm._calculate_buy_price(1.0, _at(MONDAY, 18)) == pytest.approx(
            1.0 + 0.25 + 0.14641
        )

    def test_same_spot_price_costs_more_in_the_evening_than_at_night(self) -> None:
        """The whole point: the optimizer must see a spread the spot price alone
        does not carry."""
        pm = _price_manager(grid_fee_enabled=True, **LT_RATES)
        night = pm._calculate_buy_price(1.0, _at(MONDAY, 2))
        evening = pm._calculate_buy_price(1.0, _at(MONDAY, 19))
        assert evening - night == pytest.approx(0.14641 - 0.06292)

    def test_a_full_day_of_prices_varies_by_period(self) -> None:
        """get_price_data() must place each of the 96 periods in its own zone,
        not apply one zone to the whole day."""
        pm = _price_manager(grid_fee_enabled=True, **LT_RATES)
        prices = pm.get_price_data(MONDAY)

        by_hour: dict[int, float] = {}
        for index, entry in enumerate(prices):
            by_hour.setdefault(index // 4, entry["buyPrice"])

        assert by_hour[2] == pytest.approx(1.06292)
        assert by_hour[6] == pytest.approx(1.08349)
        assert by_hour[10] == pytest.approx(1.10406)
        assert by_hour[19] == pytest.approx(1.14641)
        assert by_hour[23] == pytest.approx(1.06292)

    def test_sell_price_is_unaffected(self) -> None:
        """The distribution fee is billed on import only."""
        pm = _price_manager(grid_fee_enabled=True, **LT_RATES)
        prices = pm.get_price_data(MONDAY)
        assert {entry["sellPrice"] for entry in prices} == {1.0}
