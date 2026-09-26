"""Tests for OfficialNordpoolSource error classification and health reporting.

Issue #583: a transient 500 from the HA Nordpool integration was reported to the
user as ``⚠️ Please fix sensor configuration for full functionality.`` because
``perform_health_check`` could not distinguish "the integration is not
configured" from "the service call transiently failed".
"""

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

from core.bess import time_utils
from core.bess.exceptions import (
    PriceDataUnavailableError,
    SystemConfigurationError,
)
from core.bess.official_nordpool_source import OfficialNordpoolSource
from core.bess.price_manager import PriceManager


def _source(ha_controller=None, config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888"):
    return OfficialNordpoolSource(
        ha_controller=ha_controller or MagicMock(),
        config_entry_id=config_entry_id,
        vat_multiplier=1.25,
        area="SE4",
    )


def _http_500():
    """The exact failure from the issue #583 debug bundle."""
    response = MagicMock()
    response.status_code = 500
    return requests.HTTPError(
        "500 Server Error: Internal Server Error for url: "
        "http://supervisor/core/api/services/nordpool/get_prices_for_date",
        response=response,
    )


class TestErrorClassification:
    def test_service_call_failure_raises_price_data_unavailable(self):
        """A 500 from the HA service call is an availability problem, not a
        configuration problem — callers must be able to tell them apart."""
        controller = MagicMock()
        controller._service_call_with_retry.side_effect = _http_500()

        with pytest.raises(PriceDataUnavailableError):
            _source(controller).get_prices_for_date(time_utils.today())

    def test_missing_config_entry_raises_system_configuration_error(self):
        """A missing config entry genuinely is a configuration problem."""
        source = _source(config_entry_id="")

        with pytest.raises(SystemConfigurationError):
            source.get_prices_for_date(time_utils.today())


class TestHealthCheckStatus:
    def test_transient_service_failure_is_reported_as_unavailable_not_misconfigured(
        self,
    ):
        """Issue #583: the system really does have no prices, so the status stays
        ERROR — but the operator-facing text must say the source is temporarily
        unavailable and will be retried, not imply the config is wrong."""
        controller = MagicMock()
        controller._service_call_with_retry.side_effect = _http_500()

        result = _source(controller).perform_health_check()

        assert result["status"] == "ERROR"
        service_check = next(
            c for c in result["checks"] if c["name"] == "Nordpool Service Call"
        )
        assert service_check["status"] == "ERROR"
        error = service_check["error"].lower()
        # Says it will retry (the #583 case) ...
        assert "retry" in error
        # ... and names what to check if it does not heal, because HA answers a
        # stale config entry with an identical 500 and we cannot tell them apart.
        assert "setup wizard" in error
        # But claims neither cause as fact.
        assert "temporarily unavailable" not in error

    def test_missing_config_entry_error_names_the_configuration_problem(self):
        """The two failures share a status, so the text is the only thing that
        distinguishes them — assert what it says, not what it omits."""
        result = _source(config_entry_id="").perform_health_check()

        config_check = result["checks"][0]
        assert config_check["status"] == "ERROR"
        assert "config entry" in config_check["error"].lower()
        assert config_check["value"] == "Missing"

    def test_missing_config_entry_still_reports_error(self):
        """The real misconfiguration must keep its ERROR status — the fix must
        not blunt the signal it exists for."""
        result = _source(config_entry_id="").perform_health_check()

        assert result["status"] == "ERROR"
        assert result["checks"][0]["status"] == "ERROR"

    def test_successful_call_reports_ok(self):
        """The response carries start/end on every entry, as the real service
        does — the health check is only healthy if a whole local day is covered."""
        today = time_utils.today()
        controller = _controller_serving(
            {today: _delivery_day(today, 500.0)}, area="SE4"
        )

        result = _source(controller).perform_health_check()

        assert result["status"] == "OK"
        service_check = next(
            c for c in result["checks"] if c["name"] == "Nordpool Service Call"
        )
        expected = time_utils.get_period_count(today)
        assert service_check["value"] == f"{expected} price periods available"


# ---------------------------------------------------------------------------
# Delivery-day alignment
#
# nordpool.get_prices_for_date(date=D) returns the CET DELIVERY day D
# (pynordpool requests /DayAheadPrices?date=D, whose response is keyed
# deliveryDateCET), with tz-aware UTC start/end on every entry. For a CET
# market area that block coincides with the local day; for an EET area
# (LT/LV/EE/FI, CET+1) it covers local 01:00 -> 01:00 next day, so the local
# day's first hour lives in delivery day D-1.
# ---------------------------------------------------------------------------

MARKET_TZ = ZoneInfo("Europe/Oslo")


def _delivery_day(cet_date: date, base_mwh: float, step_minutes: int = 15) -> list:
    """Entries as the HA service returns them for one CET delivery day.

    Prices encode their own position (base_mwh + index) so an assertion can
    name which delivery day and which period a value came from.
    """
    start = datetime.combine(cet_date, time(0, 0), tzinfo=MARKET_TZ).astimezone(UTC)
    end = datetime.combine(
        cet_date + timedelta(days=1), time(0, 0), tzinfo=MARKET_TZ
    ).astimezone(UTC)

    entries = []
    cursor, index = start, 0
    while cursor < end:
        nxt = cursor + timedelta(minutes=step_minutes)
        entries.append(
            {
                "start": cursor.isoformat(),
                "end": nxt.isoformat(),
                "price": base_mwh + index,
            }
        )
        cursor, index = nxt, index + 1
    return entries


def _controller_serving(days: dict, area: str = "LT") -> MagicMock:
    """Controller answering get_prices_for_date from a {cet_date: entries} map."""
    controller = MagicMock()

    def _call(domain: str, service: str, **kwargs: str) -> dict:
        requested = date.fromisoformat(kwargs["date"])
        if requested not in days:
            raise PriceDataUnavailableError(
                date=requested, message=f"no delivery day {requested}"
            )
        return {"service_response": {area: days[requested]}}

    controller._service_call_with_retry.side_effect = _call
    return controller


class TestLocalDayAlignment:
    def test_eet_local_day_starts_with_the_previous_delivery_days_last_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lithuania: local 00:00-01:00 is 23:00-24:00 CET of delivery day D-1.

        Reported symptom: the 08:00 price peak was displayed at 07:00, because
        the source returned delivery day D positionally and PriceManager
        stamped index 0 as local 00:00.
        """
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Vilnius"))
        target = date(2026, 9, 26)
        monkeypatch.setattr(time_utils, "today", lambda: target)

        controller = _controller_serving(
            {
                target
                - timedelta(days=1): _delivery_day(target - timedelta(days=1), 1000.0),
                target: _delivery_day(target, 2000.0),
            }
        )
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.21,
            area="LT",
        )

        prices = source.get_prices_for_date(target)

        assert len(prices) == time_utils.get_period_count(target)
        # Local 00:00-01:00 == the last four periods of delivery day D-1.
        assert prices[0] == pytest.approx((1000.0 + 92) / 1000.0)
        assert prices[3] == pytest.approx((1000.0 + 95) / 1000.0)
        # Local 01:00 onwards == delivery day D from its first period.
        assert prices[4] == pytest.approx(2000.0 / 1000.0)

    def test_cet_area_is_unchanged_and_fetches_one_delivery_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweden: the delivery day already is the local day. The fix must not
        move a single price there, nor spend a second service call."""
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Stockholm"))
        target = date(2026, 9, 26)
        monkeypatch.setattr(time_utils, "today", lambda: target)

        day = _delivery_day(target, 2000.0)
        controller = _controller_serving({target: day}, area="SE4")
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.25,
            area="SE4",
        )

        prices = source.get_prices_for_date(target)

        assert prices == [entry["price"] / 1000.0 for entry in day]
        assert controller._service_call_with_retry.call_count == 1

    def test_a_gap_in_coverage_raises_rather_than_padding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A delivery day that stops short leaves the local day's first hour
        uncovered. That must fail loudly, not silently shift or pad."""
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Vilnius"))
        target = date(2026, 9, 26)
        monkeypatch.setattr(time_utils, "today", lambda: target)

        previous = _delivery_day(target - timedelta(days=1), 1000.0)[:-4]
        controller = _controller_serving(
            {
                target - timedelta(days=1): previous,
                target: _delivery_day(target, 2000.0),
            }
        )
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.21,
            area="LT",
        )

        with pytest.raises(PriceDataUnavailableError, match="No Nordpool price covers"):
            source.get_prices_for_date(target)

    def test_hourly_entries_expand_across_their_quarterly_periods(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolution is the response's business, not the caller's: a 60-minute
        entry covers the four quarterly periods inside it."""
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Vilnius"))
        target = date(2026, 9, 26)
        monkeypatch.setattr(time_utils, "today", lambda: target)

        controller = _controller_serving(
            {
                target
                - timedelta(days=1): _delivery_day(
                    target - timedelta(days=1), 1000.0, step_minutes=60
                ),
                target: _delivery_day(target, 2000.0, step_minutes=60),
            }
        )
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.21,
            area="LT",
        )

        prices = source.get_prices_for_date(target)

        assert len(prices) == time_utils.get_period_count(target)
        # Local 00:00-01:00 is delivery day D-1's final hour, held for 4 periods.
        assert prices[0:4] == [pytest.approx((1000.0 + 23) / 1000.0)] * 4
        # Local 01:00-02:00 is delivery day D's first hour.
        assert prices[4:8] == [pytest.approx(2000.0 / 1000.0)] * 4

    @pytest.mark.parametrize(
        ("target", "expected_periods"),
        [(date(2026, 3, 29), 92), (date(2026, 10, 25), 100)],
    )
    def test_dst_local_days_are_covered_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch, target: date, expected_periods: int
    ) -> None:
        """23h and 25h local days. Positional indexing cannot express these;
        interval containment gets them for free."""
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Vilnius"))
        monkeypatch.setattr(time_utils, "today", lambda: target)

        controller = _controller_serving(
            {
                target
                - timedelta(days=1): _delivery_day(target - timedelta(days=1), 1000.0),
                target: _delivery_day(target, 2000.0),
            }
        )
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.21,
            area="LT",
        )

        prices = source.get_prices_for_date(target)

        assert len(prices) == expected_periods == time_utils.get_period_count(target)


class TestGridFeePairing:
    """The spot price and the time-of-use grid fee must describe the same slot.

    PriceManager pairs raw_prices[i] with grid_fee_for(local 00:00 + i*15min).
    While the source returned a CET delivery day positionally, an EET install
    charged every spot price the fee of the hour before the one it belonged to
    — invisible inside a zone, wrong at every boundary.
    """

    def test_spot_and_fee_describe_the_same_slot_across_a_zone_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Vilnius"))
        target = date(2026, 9, 28)  # Monday: the four-zone weekday schedule
        monkeypatch.setattr(time_utils, "today", lambda: target)

        controller = _controller_serving(
            {
                target
                - timedelta(days=1): _delivery_day(target - timedelta(days=1), 1000.0),
                target: _delivery_day(target, 2000.0),
            }
        )
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.21,
            area="LT",
        )
        manager = PriceManager(
            price_source=source,
            markup_rate=0.0,
            vat_multiplier=1.0,
            additional_costs=0.0,
            tax_reduction=0.0,
            area="LT",
            grid_fee_enabled=True,
            grid_fee_night=0.01,
            grid_fee_morning=0.02,
            grid_fee_day=0.03,
            grid_fee_evening=0.04,
        )

        entries = manager.get_price_data(target)

        # 06:45 local is 05:45 CET on delivery day D -> that day's entry 23.
        assert entries[27]["timestamp"] == "2026-09-28 06:45"
        assert entries[27]["buyPrice"] == pytest.approx(2.023 + 0.02)
        # 07:00 local is 06:00 CET -> entry 24, and the morning/day boundary.
        assert entries[28]["timestamp"] == "2026-09-28 07:00"
        assert entries[28]["buyPrice"] == pytest.approx(2.024 + 0.03)


class TestRetryWarningSuppression:
    def test_only_the_unpublished_delivery_day_is_fetched_quietly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fetching local tomorrow in EET spans two delivery days: the current
        one, already published, and the next, which routinely is not.

        Asserted at the call rather than on an outcome because the behaviour
        *is* log verbosity — there is no state or value it changes. Silencing
        the published day too would hide a real outage behind the daily
        not-yet-published case.
        """
        monkeypatch.setattr(time_utils, "TIMEZONE", ZoneInfo("Europe/Vilnius"))
        today = date(2026, 9, 26)
        tomorrow = today + timedelta(days=1)
        monkeypatch.setattr(time_utils, "today", lambda: today)
        monkeypatch.setattr(
            time_utils,
            "now",
            lambda: datetime(2026, 9, 26, 14, 0, tzinfo=ZoneInfo("Europe/Vilnius")),
        )

        controller = _controller_serving(
            {
                today: _delivery_day(today, 1000.0),
                tomorrow: _delivery_day(tomorrow, 2000.0),
            }
        )
        source = OfficialNordpoolSource(
            ha_controller=controller,
            config_entry_id="01K3Y99FD3MDZYVFX2XSWR4888",
            vat_multiplier=1.21,
            area="LT",
        )

        source.get_prices_for_date(tomorrow)

        suppressed = {
            call.kwargs["date"]: call.kwargs["suppress_retry_warnings"]
            for call in controller._service_call_with_retry.call_args_list
        }
        assert suppressed == {"2026-09-26": False, "2026-09-27": True}
