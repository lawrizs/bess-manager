"""API validation for the dashboard settings section.

The Energy Flow curve type is handed straight to recharts' `type` prop. An
unrecognised value there does not error — recharts silently falls back — so a
bad value would persist and quietly do nothing rather than surface. The API
rejects it at the boundary instead of trusting the client.

The store-side defaults and migration are covered in
core/bess/tests/unit/test_dashboard_settings.py.
"""

import pytest
from api import _SECTION_MAP, _validate_dashboard_section
from fastapi import HTTPException


class TestSectionRouting:
    def test_camelcase_key_maps_to_the_store_section(self) -> None:
        """Without this entry PATCH /api/settings answers 400 'Unknown
        settings section' and the choice can never be saved."""
        assert _SECTION_MAP["dashboard"] == "dashboard"


class TestCurveTypeValidation:
    @pytest.mark.parametrize("style", ["step", "monotone"])
    def test_known_styles_are_accepted(self, style: str) -> None:
        _validate_dashboard_section({"energy_flow_line_style": style})

    @pytest.mark.parametrize(
        "style",
        [
            "wobbly",  # not a recharts curve type
            "linear",  # a real recharts type, but not one this UI offers
            "",
            "Step",  # case matters — recharts is case-sensitive
            "MONOTONE",
            None,
            3,
        ],
    )
    def test_unknown_styles_are_rejected_with_422(self, style: object) -> None:
        with pytest.raises(HTTPException) as exc:
            _validate_dashboard_section({"energy_flow_line_style": style})
        assert exc.value.status_code == 422

    def test_missing_key_is_rejected(self) -> None:
        """PATCH deep-merges onto the persisted section before validating, so
        an absent key here means the store itself lost it."""
        with pytest.raises(HTTPException) as exc:
            _validate_dashboard_section({})
        assert exc.value.status_code == 422

    def test_rejection_names_the_offending_value(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _validate_dashboard_section({"energy_flow_line_style": "wobbly"})
        assert "wobbly" in str(exc.value.detail)
