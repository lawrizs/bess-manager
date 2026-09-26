"""
Official Home Assistant Nordpool integration price source.

This source uses the official HA Nordpool integration's service actions
instead of sensor attributes, providing compatibility with the core integration.
"""

import logging
from bisect import bisect_right
from datetime import UTC, date, datetime, time, timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo

from . import time_utils
from .exceptions import PriceDataUnavailableError, SystemConfigurationError
from .price_manager import PriceSource

logger = logging.getLogger(__name__)

# Nord Pool delivery days run 00:00-24:00 market time; Europe/Oslo tracks it.
MARKET_TIMEZONE = ZoneInfo("Europe/Oslo")


class OfficialNordpoolSource(PriceSource):
    """Price source that uses the official Home Assistant Nordpool integration.

    Uses the nordpool.get_prices_for_date service action instead of sensor attributes.
    The official integration was added to HA Core and provides different API than custom components.
    """

    # Nord Pool day-ahead auction clears ~12:42 CET, prices generally
    # available by 13:00 CET (Europe/Oslo tracks the market timezone).
    TOMORROW_EARLIEST: ClassVar[tuple[int, int, str]] = (12, 0, "Europe/Oslo")

    def __init__(
        self, ha_controller, config_entry_id: str, vat_multiplier: float, area: str = ""
    ) -> None:
        """Initialize with Home Assistant controller and config entry ID.

        Args:
            ha_controller: Controller with access to Home Assistant services
            config_entry_id: Configuration entry ID for the Nordpool integration
            vat_multiplier: VAT multiplier (prices from official integration are VAT-exclusive)
            area: Market area code (e.g. "SE4", "NO1"). When provided it is passed
                  to the service call and used as the response key for an exact lookup.
                  When empty the first list in the response is used as a fallback.
        """
        self.ha_controller = ha_controller
        self.config_entry_id = config_entry_id
        self.vat_multiplier = vat_multiplier
        self.area = area

    def get_prices_for_date(self, target_date: date) -> list[float]:
        """Get prices for the local calendar day ``target_date``.

        Args:
            target_date: The date to get prices for

        Returns:
            One VAT-exclusive price per quarterly period of the local day,
            index 0 being local 00:00

        Raises:
            ValueError: If target_date is neither today nor tomorrow. This is a
                caller error, raised before the fetch is attempted, and is
                deliberately left untyped — ``price_manager.get_price_data``
                wraps it as PriceDataUnavailableError, which mislabels a
                programming error as an availability problem, so callers should
                not rely on that path.
            SystemConfigurationError: If the integration is not configured
            PriceDataUnavailableError: If prices cannot be fetched right now, or
                do not cover the whole local day
        """
        if not self.config_entry_id:
            raise SystemConfigurationError(
                component="Nordpool",
                message=(
                    "Nordpool integration not configured: config_entry_id is missing. "
                    "Run the setup wizard to configure the Nordpool integration."
                ),
            )

        logger.info(
            f"Fetching Nordpool prices for {target_date} using official integration"
        )

        # Only support today and tomorrow (official integration limitation)
        current_date = time_utils.today()
        tomorrow_date = current_date + timedelta(days=1)

        if target_date not in (current_date, tomorrow_date):
            raise ValueError(
                f"Official Nordpool integration only supports today and tomorrow, not {target_date}"
            )

        day_start = datetime.combine(
            target_date, time(0, 0), tzinfo=time_utils.TIMEZONE
        ).astimezone(UTC)
        day_end = datetime.combine(
            target_date + timedelta(days=1), time(0, 0), tzinfo=time_utils.TIMEZONE
        ).astimezone(UTC)

        # Whether a fetch may routinely come back empty is a property of the
        # delivery day, not of the local date asked for: only a day beyond the
        # one currently being delivered is still unpublished (the auction
        # clears ~12:42 CET). A local day east of CET also needs an earlier
        # delivery day, and that one failing is a real anomaly worth warning
        # about even while the later one is expected to be missing.
        market_today = time_utils.now().astimezone(MARKET_TIMEZONE).date()

        entries: list[tuple[datetime, datetime, float]] = []
        for delivery_date in self._delivery_days_covering(day_start, day_end):
            entries.extend(
                self._fetch_delivery_day(
                    delivery_date,
                    suppress_retry_warnings=(delivery_date > market_today),
                )
            )
        entries.sort(key=lambda entry: entry[0])
        starts = [entry[0] for entry in entries]

        prices = []
        for index in range(time_utils.get_period_count(target_date)):
            moment = day_start + timedelta(minutes=index * time_utils.INTERVAL_MINUTES)
            position = bisect_right(starts, moment) - 1
            if position < 0 or entries[position][1] <= moment:
                raise PriceDataUnavailableError(
                    date=target_date,
                    message=(
                        f"No Nordpool price covers "
                        f"{moment.astimezone(time_utils.TIMEZONE)} — the delivery "
                        f"days fetched for {target_date} leave it uncovered"
                    ),
                )
            prices.append(entries[position][2])

        logger.info(
            f"Successfully fetched {len(prices)} prices from official Nordpool integration"
        )
        logger.debug(f"Price range: {min(prices):.3f} - {max(prices):.3f} per kWh")

        return prices

    @staticmethod
    def _delivery_days_covering(day_start: datetime, day_end: datetime) -> list[date]:
        """CET delivery days overlapping the half-open UTC interval given.

        The service's ``date`` selects a Nord Pool delivery day, which runs
        00:00-24:00 market time, not the user's local day. A CET area needs one
        delivery day; an EET area (LT/LV/EE/FI) also reaches into the previous
        one, because 00:00 EET is 23:00 CET the day before.
        """
        first = day_start.astimezone(MARKET_TIMEZONE).date()
        last = (day_end - timedelta(microseconds=1)).astimezone(MARKET_TIMEZONE).date()
        return [
            first + timedelta(days=offset) for offset in range((last - first).days + 1)
        ]

    def _fetch_delivery_day(
        self, delivery_date: date, suppress_retry_warnings: bool
    ) -> list[tuple[datetime, datetime, float]]:
        """Fetch one CET delivery day as (start, end, price per kWh) entries.

        Accepts any date, unlike the public method that is bounded to today and
        tomorrow: a local day east of CET reaches back into the delivery day
        before it, which is always a past day and so always published.

        A PriceDataUnavailableError raised here carries ``date=delivery_date``
        — the market day whose fetch failed, which for an EET area is not
        necessarily the local day the caller asked for.
        """
        service_data: dict = {
            "config_entry": self.config_entry_id,
            "date": delivery_date.strftime("%Y-%m-%d"),
        }
        if self.area:
            service_data["areas"] = self.area

        try:
            response = self.ha_controller._service_call_with_retry(
                "nordpool",
                "get_prices_for_date",
                suppress_retry_warnings=suppress_retry_warnings,
                **service_data,
                return_response=True,
            )

            if not response or "service_response" not in response:
                raise PriceDataUnavailableError(
                    date=delivery_date,
                    message=f"No response from nordpool.get_prices_for_date for {delivery_date}",
                )

            service_response = response["service_response"]
            price_entries = self._entries_for_area(service_response)

            if not price_entries:
                raise PriceDataUnavailableError(
                    date=delivery_date,
                    message=(
                        f"No price entries returned for {delivery_date}. "
                        f"Available keys: {list(service_response.keys())}"
                    ),
                )

            # Official integration returns prices in [Currency]/MWh
            return [
                (
                    datetime.fromisoformat(entry["start"]),
                    datetime.fromisoformat(entry["end"]),
                    float(entry["price"]) / 1000.0,
                )
                for entry in price_entries
            ]

        except (PriceDataUnavailableError, SystemConfigurationError):
            raise
        except Exception as e:
            # Service-call and transport failures are classified as availability
            # problems: they are usually transient and the next fetch succeeds
            # (issue #583). Note this cannot be a certain classification — HA
            # answers a stale config_entry with the same 500 and the same body
            # as an upstream outage, so perform_health_check words its message
            # to cover both.
            raise PriceDataUnavailableError(
                date=delivery_date,
                message=f"Failed to get prices from official integration for {delivery_date}: {e}",
            ) from e

    def _entries_for_area(self, service_response: dict) -> list:
        """Pick this area's entry list out of the area-keyed response.

        Uses the configured area for an exact lookup; falls back to the first
        list in the response for installs where area is not yet configured.
        """
        if self.area:
            price_entries: list = service_response.get(self.area, [])
            if not price_entries:
                # Area codes are always uppercase in the response
                price_entries = service_response.get(self.area.upper(), [])
            if price_entries:
                logger.debug(f"Found price data under area key: {self.area}")
                return price_entries

        for key, value in service_response.items():
            if isinstance(value, list) and value:
                logger.debug(f"Found price data under key: {key} (fallback)")
                return value

        return []

    def perform_health_check(self):
        """Perform health check for official Nordpool integration.

        Returns:
            dict: Health check results
        """
        health_check = {
            "component_name": "Official Nordpool Integration",
            "description": "Official Home Assistant Nordpool price source",
            "is_required": True,
            "status": "OK",
            "checks": [],
        }

        # Check config entry ID first — no point calling the API without it
        config_check = {
            "name": "Configuration Entry",
            "status": "OK",
            "error": None,
            "value": f"ID: {self.config_entry_id}",
        }

        if not self.config_entry_id:
            config_check.update(
                {
                    "status": "ERROR",
                    "error": "No config entry ID configured",
                    "value": "Missing",
                }
            )
            health_check["status"] = "ERROR"
            health_check["checks"] = [config_check]
            return health_check

        # Test the service call with today's date
        today = time_utils.today()

        service_check = {
            "name": "Nordpool Service Call",
            "status": "OK",
            "error": None,
            "value": "Available",
        }

        try:
            prices = self.get_prices_for_date(today)
            service_check.update(
                {"status": "OK", "value": f"{len(prices)} price periods available"}
            )
        except PriceDataUnavailableError as e:
            # Still ERROR — without prices the system genuinely cannot optimize.
            # What changes is the explanation. The failure is usually transient
            # (#583), but Home Assistant answers a stale config_entry with the
            # same 500 and the same body as an upstream outage — verified
            # against a live HA — so this text must not assert either cause.
            # It states what is known, and names the thing to check if the
            # failure persists.
            service_check.update(
                {
                    "status": "ERROR",
                    "error": (
                        f"Nordpool price fetch failed, will retry on the next "
                        f"fetch. If this persists, check that the Nordpool "
                        f"integration is still loaded in Home Assistant and "
                        f"re-run the setup wizard if it was re-added: {e!s}"
                    ),
                    "value": "Unavailable",
                }
            )
            health_check["status"] = "ERROR"
        except Exception as e:
            service_check.update(
                {
                    "status": "ERROR",
                    "error": f"Service call failed: {e!s}",
                    "value": "N/A",
                }
            )
            health_check["status"] = "ERROR"

        health_check["checks"] = [service_check, config_check]
        return health_check
