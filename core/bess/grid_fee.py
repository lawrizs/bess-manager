"""Time-of-use distribution (grid) fee zones.

Some distribution system operators bill the per-kWh grid fee at a rate that
depends on the time of day and whether the day is a working day, rather than
at the single flat rate PriceSettings.additional_costs models. This module
owns the wall-clock -> zone mapping; PriceManager owns the per-zone rates and
applies the resulting fee to the buy price.

The window boundaries below are the Lithuanian ESO schedule (all times local,
Europe/Vilnius). Only the four rates are user-configurable — the boundaries
are a published tariff structure, not a tuning knob.

    Zone       Mon-Fri          Sat/Sun
    morning    05:00-07:00      -
    day        07:00-17:00      07:00-22:00
    evening    17:00-22:00      -
    night      22:00-05:00      22:00-07:00

Public holidays follow the weekend schedule in the published tariff. They are
deliberately not modelled here: doing so needs a holiday calendar the add-on
does not have, and the effect is confined to the ~12 days a year on which the
morning and evening zones are overstated.
"""

from datetime import datetime

ZONE_NIGHT = "night"
ZONE_MORNING = "morning"
ZONE_DAY = "day"
ZONE_EVENING = "evening"

ZONES = (ZONE_NIGHT, ZONE_MORNING, ZONE_DAY, ZONE_EVENING)

_SATURDAY = 5


def zone_for(moment: datetime) -> str:
    """Return the tariff zone a local wall-clock moment falls in.

    Args:
        moment: Start of the pricing period, in local wall-clock time.

    Returns:
        One of the ZONE_* constants.
    """
    hour = moment.hour

    if moment.weekday() >= _SATURDAY:
        return ZONE_DAY if 7 <= hour < 22 else ZONE_NIGHT

    if hour < 5:
        return ZONE_NIGHT
    if hour < 7:
        return ZONE_MORNING
    if hour < 17:
        return ZONE_DAY
    if hour < 22:
        return ZONE_EVENING
    return ZONE_NIGHT
