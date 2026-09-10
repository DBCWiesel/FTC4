"""Tests fuer die Benennung der Einstellungen anhand der Herstellertabelle.

``load_items`` liest eine .xls-Datei, die nicht im Repository liegt -- die
Tabelle gehoert zum Hersteller-Werkzeug. Getestet wird deshalb die Logik
darueber: Zuordnung Record zu Eintrag, Aufloesen der Lesarten und die Ausgabe.
Liegt eine echte Tabelle unter ``data/Teigi/``, laeuft zusaetzlich der Test
gegen sie.
"""

from __future__ import annotations

import pytest

from conftest import DATA_DIR, build_dat
from src.dat_decoder import DatRecord, FTC4DatFile
from src.rc_settings import (
    TAB_TO_FILE,
    DecodedSetting,
    SettingItem,
    decode_file,
    load_items,
    render_text,
)


def item(offset, title="Test", unit="C", minimum=None, maximum=None,
         default="", choices="", tab=1, code=1):
    return SettingItem(code=code, tab=tab, title=title, unit=unit, minimum=minimum,
                       maximum=maximum, default=default, choices=choices, offset=offset)


class TestSettingItem:
    def test_range_check(self):
        spec = item(0, minimum=10, maximum=30)
        assert spec.in_range(20) is True
        assert spec.in_range(10) is True
        assert spec.in_range(31) is False

    def test_open_range_accepts_everything(self):
        assert item(0).in_range(-999) is True

    def test_filename_from_tab(self):
        assert item(0, tab=2).filename == "HT&CL.DAT"
        assert item(0, tab=99).filename == ""


class TestDecode:
    """Die Kodierung wird aus dem dokumentierten Wertebereich erschlossen."""

    def test_bcd_wins_when_only_it_fits(self):
        dat = FTC4DatFile(build_dat([(0x03, 0x01, 0x50)]))
        entry = decode_file([item(0x000, minimum=10, maximum=30)], dat)[0]
        assert entry.value == 15.0
        assert entry.status == "eindeutig"

    def test_half_degree_wins_for_flow_temperature(self):
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 0x64)]))
        entry = decode_file([item(0x000, minimum=25, maximum=60)], dat)[0]
        assert entry.value == 50.0

    def test_offset_encoding_wins_for_outdoor_temperature(self):
        """LO 50 ist -15 C, wenn der Bereich Minusgrade zulaesst."""
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 50)]))
        entry = decode_file([item(0x000, minimum=-15, maximum=10)], dat)[0]
        assert entry.value == -15.0

    def test_ambiguous_readings_are_all_reported(self):
        dat = FTC4DatFile(build_dat([(0x03, 0x03, 0x00)]))
        entry = decode_file([item(0x000, minimum=1, maximum=120)], dat)[0]
        assert entry.value is None
        assert "Lesarten moeglich" in entry.status
        assert entry.readings["BCD"] == 30.0

    def test_no_fitting_reading_keeps_all_variants(self):
        """Passt nichts, muessen die Lesarten trotzdem sichtbar bleiben."""
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 126)]))
        entry = decode_file([item(0x000, minimum=40, maximum=60)], dat)[0]
        assert entry.readings == {}
        assert entry.all_readings["Halbgrad"] == 63.0
        assert entry.status == "keine Lesart im erlaubten Bereich"

    def test_time_record(self):
        dat = FTC4DatFile(build_dat([(0x04, 0x13, 0x00)]))
        entry = decode_file([item(0x000, minimum=0, maximum=0.95)], dat)[0]
        assert entry.time == "13:00"
        assert entry.status == "eindeutig"

    def test_choice_list_is_resolved(self):
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 0x01)]))
        entry = decode_file([item(0x000, choices="0:Normal/1:Eco")], dat)[0]
        assert entry.choice == "Eco"

    def test_unknown_choice_stays_none(self):
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 0x07)]))
        assert decode_file([item(0x000, choices="0:Normal/1:Eco")], dat)[0].choice is None

    def test_two_value_record_offers_both_bytes(self):
        """Die Heizkurve traegt Aussen- und Vorlauftemperatur in einem Record."""
        dat = FTC4DatFile(build_dat([(0x02, 0x89, 0x25)]))
        entry = decode_file([item(0x000, minimum=25, maximum=60)], dat)[0]
        assert "LO Rohwert" in entry.all_readings
        assert "HI Halbgrad -40" in entry.all_readings

    def test_missing_record_is_reported(self):
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 0x01)]))
        entry = decode_file([item(0x1A0)], dat)[0]
        assert entry.record is None
        assert entry.status == "kein Record an diesem Offset"


class TestReport:
    def test_renders_values_and_source_note(self):
        dat = FTC4DatFile(build_dat([(0x03, 0x01, 0x50)]))
        text = render_text(decode_file([item(0x000, title="Raumtemperatur",
                                             minimum=10, maximum=30)], dat), "HOL.DAT")
        assert "HOL.DAT" in text
        assert "Raumtemperatur" in text
        assert "15 C" in text
        assert "Hersteller-Werkzeugs" in text

    def test_shows_variants_when_ambiguous(self):
        dat = FTC4DatFile(build_dat([(0x03, 0x03, 0x00)]))
        text = render_text(decode_file([item(0x000, minimum=1, maximum=120)], dat), "X")
        assert "BCD=30" in text


class TestAgainstRealTable:
    """Gegen eine echte Herstellertabelle, wenn eine unter data/Teigi/ liegt."""

    @pytest.fixture
    def table(self):
        found = sorted(DATA_DIR.glob("Teigi/RCSetting_*.xls"))
        if not found:
            pytest.skip("keine RCSetting_*.xls unter data/Teigi/ -- uebersprungen")
        return found[0]

    def test_all_byte_numbers_are_record_aligned(self, table):
        """Jede Byte-Nummer der Tabelle liegt auf einer Recordgrenze.

        Das ist die unabhaengige Bestaetigung der 3-Byte-Struktur: sie stammt
        vom Hersteller, nicht aus unserer Analyse.
        """
        items = load_items(table)
        assert items, "Tabelle enthaelt keine Eintraege mit Byte-Nummer"
        schief = [i for i in items if i.offset % 3]
        assert not schief, f"nicht auf Recordgrenze: {[hex(i.offset) for i in schief]}"

    def test_tabs_cover_known_files(self, table):
        tabs = {i.tab for i in load_items(table)}
        assert tabs & set(TAB_TO_FILE)
