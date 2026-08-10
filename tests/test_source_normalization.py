"""Tests for entity-source normalization.

Values here mirror the real entities recorded in docs/reference-installation.md.
"""

import pytest
from custom_components.bitcruise.source_normalization import (
    ChargerStatus,
    DataFreshness,
    PlugStatus,
    SourceUnavailable,
    normalize_charger_status,
    normalize_energy_kwh,
    normalize_freshness,
    normalize_number,
    normalize_percentage,
    normalize_plug_status,
)

ENTITY = "sensor.test"


class TestNumericParsing:
    """Raw state parsing."""

    def test_parses_numeric_string(self) -> None:
        assert normalize_number(ENTITY, "47.0") == 47.0

    def test_parses_float(self) -> None:
        assert normalize_number(ENTITY, 81.608) == 81.608

    @pytest.mark.parametrize("raw", ["unknown", "unavailable", "", None])
    def test_rejects_non_values(self, raw: str | None) -> None:
        with pytest.raises(SourceUnavailable):
            normalize_number(ENTITY, raw)

    def test_rejects_non_numeric(self) -> None:
        with pytest.raises(SourceUnavailable, match="not numeric"):
            normalize_number(ENTITY, "idle")

    def test_error_names_the_entity(self) -> None:
        """The user must be told which input is missing, not just that one is."""
        with pytest.raises(SourceUnavailable) as err:
            normalize_number("sensor.volvo_xc40_battery", "unavailable")
        assert err.value.entity_id == "sensor.volvo_xc40_battery"


class TestPercentage:
    """State of charge and target parsing."""

    def test_accepts_valid_percentage(self) -> None:
        assert normalize_percentage(ENTITY, "47.0") == 47.0

    @pytest.mark.parametrize("raw", ["0", "100"])
    def test_accepts_bounds(self, raw: str) -> None:
        assert normalize_percentage(ENTITY, raw) in (0.0, 100.0)

    @pytest.mark.parametrize("raw", ["-1", "101", "1000"])
    def test_rejects_out_of_range(self, raw: str) -> None:
        with pytest.raises(SourceUnavailable, match=r"outside 0\.\.100"):
            normalize_percentage(ENTITY, raw)


class TestEnergyConversion:
    """Capacity readings arrive in different units."""

    def test_kwh_passes_through(self) -> None:
        assert normalize_energy_kwh(ENTITY, "81.608", "kWh") == pytest.approx(81.608)

    def test_wh_converts(self) -> None:
        assert normalize_energy_kwh(ENTITY, "81608", "Wh") == pytest.approx(81.608)

    def test_mwh_converts(self) -> None:
        assert normalize_energy_kwh(ENTITY, "0.081608", "MWh") == pytest.approx(81.608)

    def test_missing_unit_is_refused(self) -> None:
        """Assuming kWh when a sensor reports Wh is wrong by 1000x and looks fine."""
        with pytest.raises(SourceUnavailable, match="no unit_of_measurement"):
            normalize_energy_kwh(ENTITY, "81.608", None)

    def test_unsupported_unit_is_refused(self) -> None:
        with pytest.raises(SourceUnavailable, match="unsupported energy unit"):
            normalize_energy_kwh(ENTITY, "81.608", "J")


class TestPlugStatus:
    """Plug state arrives as a binary_sensor or as an enum sensor."""

    @pytest.mark.parametrize("raw", ["connected", "on", "plugged_in", "CONNECTED"])
    def test_connected_variants(self, raw: str) -> None:
        assert normalize_plug_status(raw) is PlugStatus.CONNECTED

    @pytest.mark.parametrize("raw", ["disconnected", "off", "unplugged"])
    def test_disconnected_variants(self, raw: str) -> None:
        assert normalize_plug_status(raw) is PlugStatus.DISCONNECTED

    def test_fault_is_not_disconnected(self) -> None:
        """A charging fault is actionable and must not be hidden as 'not connected'."""
        assert normalize_plug_status("fault") is PlugStatus.FAULT

    @pytest.mark.parametrize("raw", ["unavailable", "something_else", None])
    def test_unrecognised_is_unknown(self, raw: str | None) -> None:
        assert normalize_plug_status(raw) is PlugStatus.UNKNOWN


class TestFreshness:
    """Vehicle readings can be stale without being unavailable."""

    def test_available_is_fresh(self) -> None:
        assert normalize_freshness("available") is DataFreshness.FRESH

    def test_car_in_use_is_fresh(self) -> None:
        """The car being driven does not make its reported SoC stale."""
        assert normalize_freshness("car_in_use") is DataFreshness.FRESH

    @pytest.mark.parametrize(
        "raw", ["no_internet", "power_saving_mode", "ota_installation_in_progress"]
    )
    def test_stale_states(self, raw: str) -> None:
        assert normalize_freshness(raw) is DataFreshness.STALE

    @pytest.mark.parametrize("raw", [None, "unknown", "unavailable"])
    def test_unknown_states(self, raw: str | None) -> None:
        assert normalize_freshness(raw) is DataFreshness.UNKNOWN


class TestChargerStatus:
    """The full enum the reference charger declares, read from the live entity.

    Its ``options`` attribute lists exactly these five. The Home Assistant UI
    shows translated labels — ``connected_requesting`` renders as "Waiting" —
    so the raw state is what must be matched, never what the dashboard says.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("unknown", ChargerStatus.UNKNOWN),
            ("disconnected", ChargerStatus.DISCONNECTED),
            ("connected_requesting", ChargerStatus.CONNECTED),
            ("connected_charging", ChargerStatus.CHARGING),
            ("connected_finished", ChargerStatus.FINISHED),
        ],
    )
    def test_the_reference_charger_vocabulary(
        self, raw: str, expected: ChargerStatus
    ) -> None:
        assert normalize_charger_status(raw) is expected

    def test_unavailable_is_not_read_as_no_car(self) -> None:
        """Control entities go unavailable while unplugged; that is not a fact."""
        assert normalize_charger_status("unavailable") is ChargerStatus.UNKNOWN

    def test_an_unrecognised_state_is_unknown_rather_than_guessed(self) -> None:
        assert normalize_charger_status("nonsense") is ChargerStatus.UNKNOWN

    def test_a_plain_binary_sensor_charger_works_too(self) -> None:
        assert normalize_charger_status("on") is ChargerStatus.CHARGING
        assert normalize_charger_status("off") is ChargerStatus.CONNECTED
