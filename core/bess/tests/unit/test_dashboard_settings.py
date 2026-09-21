"""The dashboard settings section — Energy Flow curve type.

Presentation-only, but it still has to survive a round trip: the value is
persisted server-side so the choice follows the user between browsers, rather
than living in localStorage on one machine.

The value reaches recharts' `type` prop verbatim. recharts ignores an
unrecognised type silently, so a bad value would persist and quietly do
nothing instead of surfacing — which is why the store defaults it and the API
rejects it rather than trusting the client.

The API-side validation is covered in backend/tests/test_dashboard_settings_api.py
— backend modules use flat imports that only resolve under that suite's path.
"""

from core.bess.settings_store import (
    ENERGY_FLOW_LINE_STYLES,
    OWNED_SECTIONS,
    SettingsStore,
)


class TestSectionRegistration:
    def test_dashboard_is_an_owned_section(self) -> None:
        """Unregistered sections are silently dropped by save_all/extract, so
        the choice would vanish on the next full write."""
        assert "dashboard" in OWNED_SECTIONS

    def test_step_is_the_default_style(self) -> None:
        store = SettingsStore()
        defaults = store._bootstrap_defaults()
        assert defaults["dashboard"]["energy_flow_line_style"] == "step"

    def test_both_styles_are_offered(self) -> None:
        assert set(ENERGY_FLOW_LINE_STYLES) == {"step", "monotone"}


class TestMigration:
    """Existing installs predate the section entirely."""

    def _migrated(self, data: dict) -> dict:
        store = SettingsStore()
        store.data = data
        # _migrate_schema persists to /data when it changes anything; the
        # migration logic is what is under test, not the write.
        store._write = lambda data: None  # type: ignore[method-assign]
        store._migrate_schema()
        section: dict = store.data["dashboard"]
        return section

    def test_absent_section_is_created_with_the_default(self) -> None:
        assert self._migrated({})["energy_flow_line_style"] == "step"

    def test_existing_choice_is_preserved(self) -> None:
        """The migration must not stomp a user's saved preference."""
        data = {"dashboard": {"energy_flow_line_style": "monotone"}}
        assert self._migrated(data)["energy_flow_line_style"] == "monotone"

    def test_section_present_but_key_missing_is_defaulted(self) -> None:
        """Per-key, not per-section: a store written by a build that had the
        section but not this key must still end up usable."""
        data = {"dashboard": {"something_else": 1}}
        migrated = self._migrated(data)
        assert migrated["energy_flow_line_style"] == "step"
        assert migrated["something_else"] == 1

    def test_unrecognised_value_is_reset_to_the_default(self) -> None:
        data = {"dashboard": {"energy_flow_line_style": "wobbly"}}
        assert self._migrated(data)["energy_flow_line_style"] == "step"

    def test_non_dict_section_is_replaced(self) -> None:
        """A hand-edited settings file can put anything here."""
        assert self._migrated({"dashboard": "step"})["energy_flow_line_style"] == "step"
