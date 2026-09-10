"""Tests fuer die Dekodierung der FTC4-Konfigurationsdateien (*.DAT).

Die Strukturaussagen -- Pruefsumme, 3-Byte-Records, Typ 0x0f als Temperatur --
wurden an einem kompletten SD-Karten-Abzug (11 Dateien) nachgewiesen.
"""

from __future__ import annotations

import json

import pytest

from conftest import HT_CL_RECORDS, build_dat
from src.dat_decoder import (
    DAT_SIZE,
    TYPE_SCHEDULE,
    TYPE_TEMPERATURE,
    DatDecodeError,
    DatRecord,
    FTC4DatFile,
    main,
    render_text,
)


class TestFileHandling:
    def test_from_path(self, tmp_path, synthetic_ht_cl):
        path = tmp_path / "HT&CL.DAT"
        path.write_bytes(synthetic_ht_cl)
        dat = FTC4DatFile.from_path(path)
        assert dat.filename == "HT&CL.DAT"
        assert dat.purpose == "Heizen und Kuehlen"

    def test_rejects_wrong_size(self, tmp_path):
        path = tmp_path / "kurz.DAT"
        path.write_bytes(bytes(64))
        with pytest.raises(DatDecodeError, match="64 Byte"):
            FTC4DatFile.from_path(path)

    def test_unknown_filename_has_purpose_placeholder(self, synthetic_ht_cl):
        assert FTC4DatFile(synthetic_ht_cl, "FREMD.DAT").purpose == "unbekannt"


class TestChecksum:
    """Dieselbe Regel wie bei den Logs: Summe aller 512 Byte = 0 mod 256."""

    def test_valid(self, synthetic_ht_cl):
        assert FTC4DatFile(synthetic_ht_cl).checksum_ok is True

    def test_detects_corruption(self, synthetic_ht_cl):
        broken = bytearray(synthetic_ht_cl)
        broken[0x08] ^= 0x01
        dat = FTC4DatFile(bytes(broken))
        assert dat.checksum_ok is False
        assert dat.expected_checksum() != dat.checksum_byte


class TestRecords:
    """3-Byte-Records [TYP][HI][LO]."""

    def test_record_count(self, synthetic_ht_cl):
        assert len(FTC4DatFile(synthetic_ht_cl).records()) == len(HT_CL_RECORDS)

    def test_active_length_is_multiple_of_three(self, synthetic_ht_cl):
        dat = FTC4DatFile(synthetic_ht_cl)
        assert dat.active_length % 3 == 0
        assert dat.record_aligned is True

    def test_trailing_zero_byte_does_not_truncate_record(self):
        """Ein Record, dessen LO-Byte 0x00 ist, muss vollstaendig bleiben."""
        dat = FTC4DatFile(build_dat([(0x0F, 0x00, 0x4C), (0x0F, 0x00, 0x00)]))
        assert dat.active_length == 6
        assert len(dat.records()) == 2
        assert dat.records()[1].lo == 0

    def test_record_fields(self, synthetic_ht_cl):
        record = FTC4DatFile(synthetic_ht_cl).records()[2]
        assert (record.index, record.offset) == (2, 0x06)
        assert (record.type, record.hi, record.lo) == (0x0F, 0x00, 0x4C)
        assert record.hex == "0f 00 4c"
        assert record.be16 == 0x004C

    def test_empty_file_has_no_records(self):
        assert FTC4DatFile(bytes(DAT_SIZE)).records() == []

    def test_records_by_type(self, synthetic_ht_cl):
        dat = FTC4DatFile(synthetic_ht_cl)
        assert len(dat.records_by_type(TYPE_TEMPERATURE)) == 10
        assert len(dat.records_by_type(0x02)) == 6


class TestTemperatureRecords:
    """Typ 0x0f mit HI=0: LO * 0.5 Grad -- an den Geraete-Referenzwerten belegt."""

    def test_reference_values(self, synthetic_ht_cl):
        """Gegen die Ausgabe des Hersteller-Werkzeugs: LO/2-20."""
        temps = [r.celsius for r in FTC4DatFile(synthetic_ht_cl).temperatures()]
        assert 18.0 in temps   # 0x4c
        assert 30.0 in temps   # 0x64
        assert 25.0 in temps   # 0x5a

    def test_all_setpoints(self, synthetic_ht_cl):
        dat = FTC4DatFile(synthetic_ht_cl)
        found = sorted(r.celsius for r in dat.temperatures() if r.celsius >= 15.0)
        assert found == [15.0, 18.0, 20.0, 25.0, 25.0, 30.0, 35.0, 35.0]

    @pytest.mark.parametrize("lo, celsius", [(40, 0.0), (76, 18.0), (100, 30.0), (254, 107.0)])
    def test_setpoint_scale(self, lo, celsius):
        assert DatRecord(0, 0, TYPE_TEMPERATURE, 0x00, lo).celsius == celsius

    @pytest.mark.parametrize("lo, celsius", [(80, 0.0), (90, 5.0), (50, -15.0), (0, -40.0)])
    def test_outdoor_scale(self, lo, celsius):
        assert DatRecord(0, 0, TYPE_TEMPERATURE, 0x00, lo).outdoor_celsius == celsius

    def test_unset_record_is_not_a_temperature(self):
        record = DatRecord(0, 0, TYPE_TEMPERATURE, 0xFF, 0xFF)
        assert record.is_unset is True
        assert record.is_temperature is False
        assert record.celsius is None
        assert record.describe() == "nicht belegt"

    def test_high_byte_set_is_not_a_temperature(self):
        record = DatRecord(0, 0, TYPE_TEMPERATURE, 0x01, 0x4C)
        assert record.is_temperature is False
        assert record.celsius is None

    def test_other_type_is_not_a_temperature(self):
        assert DatRecord(0, 0, 0x02, 0x00, 0x4C).is_temperature is False

    def test_describe_variants(self):
        assert "16 Bit" in DatRecord(0, 0, 0x02, 0x89, 0x25).describe()
        assert DatRecord(0, 0, 0x09, 0x00, 5).describe() == "Wert 5"


class TestBcdRecords:
    """Typ 0x03 und 0x04 sind BCD -- gegen die Werksvorgaben des Herstellers
    geprueft: acht unveraenderte Werte in DHW.DAT und HOL.DAT kommen exakt
    heraus."""

    @pytest.mark.parametrize("hi, lo, expected", [
        (0x01, 0x50, 15.0),    # HOL Zone1 Raumtemperatur, Werksvorgabe
        (0x03, 0x50, 35.0),    # HOL Zone1 Vorlauftemperatur, Werksvorgabe
        (0x01, 0x00, 10.0),    # DHW temp. drop, Werksvorgabe
        (0x06, 0x00, 60.0),    # DHW max. operation time, Werksvorgabe
        (0x00, 0x30, 3.0),     # DHW max. operation time (Stunden)
        (0x12, 0x34, 123.4),
    ])
    def test_bcd_number(self, hi, lo, expected):
        assert DatRecord(0, 0, 0x03, hi, lo).bcd_value == expected

    def test_bcd_number_describes_itself(self):
        assert DatRecord(0, 0, 0x03, 0x01, 0x50).describe() == "15  (BCD)"

    @pytest.mark.parametrize("hi, lo, expected", [
        (0x13, 0x00, "13:00"),
        (0x00, 0x00, "00:00"),
        (0x23, 0x59, "23:59"),
    ])
    def test_bcd_time(self, hi, lo, expected):
        assert DatRecord(0, 0, 0x04, hi, lo).bcd_time == expected

    def test_bcd_time_describes_itself(self):
        assert DatRecord(0, 0, 0x04, 0x13, 0x00).describe() == "13:00 Uhr"

    @pytest.mark.parametrize("hi, lo", [(0x0A, 0x00), (0x00, 0xFE), (0x7E, 0x00)])
    def test_invalid_bcd_yields_none(self, hi, lo):
        """Ein Halbbyte ueber 9 ist keine BCD-Ziffer."""
        assert DatRecord(0, 0, 0x03, hi, lo).bcd_value is None

    @pytest.mark.parametrize("hi, lo", [(0x25, 0x00), (0x13, 0x70)])
    def test_impossible_time_yields_none(self, hi, lo):
        assert DatRecord(0, 0, 0x04, hi, lo).bcd_time is None

    def test_other_types_have_no_bcd(self):
        assert DatRecord(0, 0, 0x0F, 0x01, 0x50).bcd_value is None
        assert DatRecord(0, 0, 0x03, 0x13, 0x00).bcd_time is None

    def test_unset_record_has_no_bcd(self):
        record = DatRecord(0, 0, 0x03, 0xFF, 0xFF)
        assert record.bcd_value is None
        assert record.describe() == "nicht belegt"


class TestSchedule:
    """SCH-Dateien: 35 Typ-0x06-Records = 7 Tage zu 5 Zeitfenstern."""

    @pytest.fixture
    def schedule(self) -> bytes:
        day = [(TYPE_SCHEDULE, 0x00, 0x38), (TYPE_SCHEDULE, 0x78, 0xC0)]
        day += [(TYPE_SCHEDULE, 0xFF, 0xFF)] * 3
        return build_dat(day * 7)

    def test_seven_days(self, schedule):
        days = FTC4DatFile(schedule).schedule_days()
        assert len(days) == 7
        assert all(len(d) == 5 for d in days)

    def test_two_slots_used_per_day(self, schedule):
        for day in FTC4DatFile(schedule).schedule_days():
            assert sum(not r.is_unset for r in day) == 2

    def test_rendered_report_marks_unused_slots(self, schedule):
        text = render_text(FTC4DatFile(schedule, "SCH_1.DAT"))
        assert "ZEITPROGRAMM" in text
        assert "nicht belegt" in text
        assert "ungeprueft" in text


class TestTextReport:
    def test_contains_core_information(self, synthetic_ht_cl):
        text = render_text(FTC4DatFile(synthetic_ht_cl, "HT&CL.DAT"))
        assert "HT&CL.DAT" in text
        assert "Heizen und Kuehlen" in text
        assert "Pruefsumme      : OK" in text
        assert "16 Records" in text
        assert "18.0 C" in text

    def test_warns_about_broken_checksum(self, synthetic_ht_cl):
        broken = bytearray(synthetic_ht_cl)
        broken[0x08] ^= 0x01
        text = render_text(FTC4DatFile(bytes(broken)))
        assert "FEHLERHAFT" in text
        assert "beschaedigt" in text

    def test_states_that_meanings_are_unproven(self, synthetic_ht_cl):
        text = render_text(FTC4DatFile(synthetic_ht_cl))
        assert "NICHT" in text and "nachgewiesen" in text

    def test_truncates_long_record_lists(self):
        dat = FTC4DatFile(build_dat([(0x06, 0x00, 0x01)] * 100))
        assert "weitere Records" in render_text(dat, max_records=10)
        assert "weitere Records" not in render_text(dat, max_records=None)

    def test_as_dict_is_json_serialisable(self, synthetic_ht_cl):
        payload = FTC4DatFile(synthetic_ht_cl, "HT&CL.DAT").as_dict()
        assert json.loads(json.dumps(payload))["record_count"] == 16


class TestCli:
    @pytest.fixture
    def dat_dir(self, tmp_path, synthetic_ht_cl):
        (tmp_path / "HT&CL.DAT").write_bytes(synthetic_ht_cl)
        (tmp_path / "DHW.DAT").write_bytes(build_dat([(0x0F, 0x00, 0x7E)]))
        return tmp_path

    def test_decodes_directory(self, dat_dir, capsys):
        assert main([str(dat_dir)]) == 0
        out = capsys.readouterr().out
        assert "HT&CL.DAT" in out
        assert "DHW.DAT" in out

    def test_json_mode(self, dat_dir, capsys):
        assert main([str(dat_dir), "--json"]) == 0
        assert len(json.loads(capsys.readouterr().out)) == 2

    def test_skips_wrong_size(self, tmp_path, capsys):
        (tmp_path / "kaputt.DAT").write_bytes(bytes(10))
        assert main([str(tmp_path)]) == 1
        assert "Keine verwertbare DAT-Datei" in capsys.readouterr().err


class TestAgainstRealDatFiles:
    """Validierung gegen echte Konfigurationsdateien in data/.

    Uebersprungen, solange dort nichts liegt.
    """

    def test_checksum_holds_for_every_file(self, real_dat_files):
        for path in real_dat_files:
            assert FTC4DatFile.from_path(path).checksum_ok, f"{path.name}: Pruefsumme"

    def test_every_file_is_record_aligned(self, real_dat_files):
        """Die Kernaussage: jede Datei geht glatt in 3-Byte-Records auf."""
        for path in real_dat_files:
            dat = FTC4DatFile.from_path(path)
            assert dat.record_aligned, (
                f"{path.name}: {dat.active_length} Byte sind kein Vielfaches von 3"
            )

    def test_temperatures_are_plausible(self, real_dat_files):
        """Was der Decoder als Temperatur ausweist, muss plausibel sein."""
        for path in real_dat_files:
            for record in FTC4DatFile.from_path(path).temperatures():
                assert 0.0 <= record.celsius <= 90.0, (
                    f"{path.name} Record {record.index}: {record.celsius} C"
                )

    def test_ht_cl_setpoints_match_device_readings(self, real_dat_files):
        """HT&CL.DAT muss die drei am Geraet abgelesenen Werte enthalten."""
        for path in real_dat_files:
            if "HT" not in path.name.upper():
                continue
            temps = {r.celsius for r in FTC4DatFile.from_path(path).temperatures()}
            assert {18.0, 30.0, 25.0} <= temps, f"{path.name}: {sorted(temps)}"

    def test_single_values_are_not_all_temperatures(self, real_dat_files):
        """Typ 0x0f ist ein Einzelwert, keine Temperaturzusicherung.

        SER_1.DAT enthaelt Typ-0x0f-Records mit Werten, die als Temperatur
        unsinnig waeren. Der Decoder muss die als unplausibel kennzeichnen
        statt sie als Temperatur auszugeben.
        """
        implausible = [
            (path.name, r.index, r.lo)
            for path in real_dat_files
            for r in FTC4DatFile.from_path(path).single_values()
            if not r.celsius_plausible
        ]
        assert implausible, "erwartet werden Einzelwerte ausserhalb des Temperaturfensters"
        for name, index, lo in implausible:
            record = FTC4DatFile.from_path(
                next(p for p in real_dat_files if p.name == name)
            ).records()[index]
            assert "unplausibel" in record.describe()

    def test_schedule_files_hold_seven_days(self, real_dat_files):
        for path in real_dat_files:
            if not path.name.upper().startswith("SCH_"):
                continue
            entries = FTC4DatFile.from_path(path).records_by_type(TYPE_SCHEDULE)
            assert len(entries) == 35, f"{path.name}: {len(entries)} Zeitfenster statt 35"

    def test_text_report_renders(self, real_dat_files):
        for path in real_dat_files:
            assert "FTC4-KONFIGURATION" in render_text(FTC4DatFile.from_path(path))
