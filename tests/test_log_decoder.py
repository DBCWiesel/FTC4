"""Tests fuer die Dekodierung der FTC4-Logdateien.

Die Struktur-Aussagen (Pruefsumme, Zeitstempel, beide Temperaturkodierungen)
wurden an zwei echten Logs des Geraets nachgewiesen; hier werden sie gegen
synthetische Dateien und -- wenn vorhanden -- gegen die echten geprueft.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import build_log
from src.log_decoder import (
    LOG_LAYOUT,
    LOG_SIZE,
    TEMP_CENTI,
    TEMP_HALF40,
    FTC4Log,
    LogDecodeError,
    load_names,
    main,
    render_diff,
    render_hexdump,
    render_text,
    write_csv,
)


def field_by_key(log: FTC4Log, key: str):
    return next(f for f in log.decode_all() if f.spec.key == key)


class TestFileHandling:
    """Einlesen, Groessenpruefung, Kartentestdatei."""

    def test_from_path(self, tmp_path, synthetic_log):
        path = tmp_path / "223619.LOG"
        path.write_bytes(synthetic_log)
        log = FTC4Log.from_path(path)
        assert log.filename == "223619.LOG"
        assert len(log.data) == LOG_SIZE

    def test_rejects_wrong_size(self, tmp_path):
        path = tmp_path / "kurz.LOG"
        path.write_bytes(bytes(100))
        with pytest.raises(LogDecodeError, match="100 Byte"):
            FTC4Log.from_path(path)

    def test_recognises_card_test_file(self, tmp_path):
        """TEST.LOG ist der Schreibtest der FTC4 (00 01 .. 0f), kein Log."""
        path = tmp_path / "TEST.LOG"
        path.write_bytes(bytes(range(16)))
        assert FTC4Log.is_card_test_file(path) is True
        with pytest.raises(LogDecodeError, match="Kartentestdatei"):
            FTC4Log.from_path(path)

    def test_card_test_check_on_normal_log(self, tmp_path, synthetic_log):
        path = tmp_path / "223619.LOG"
        path.write_bytes(synthetic_log)
        assert FTC4Log.is_card_test_file(path) is False

    def test_card_test_check_on_missing_file(self, tmp_path):
        assert FTC4Log.is_card_test_file(tmp_path / "fehlt.LOG") is False


class TestChecksum:
    """Pruefsumme: Summe aller 512 Bytes = 0 (mod 256)."""

    def test_valid(self, synthetic_log):
        assert FTC4Log(synthetic_log).checksum_ok is True

    def test_detects_corruption(self, synthetic_log):
        broken = bytearray(synthetic_log)
        broken[0x50] ^= 0x01
        log = FTC4Log(bytes(broken))
        assert log.checksum_ok is False
        assert log.expected_checksum() != log.checksum_byte

    def test_expected_checksum_repairs(self, synthetic_log):
        broken = bytearray(synthetic_log)
        broken[0x50] ^= 0x01
        log = FTC4Log(bytes(broken))
        repaired = bytearray(broken)
        repaired[511] = log.expected_checksum()
        assert FTC4Log(bytes(repaired)).checksum_ok is True


class TestTimestamp:
    """Zeitstempel aus Header und Dateiname."""

    def test_header_timestamp(self, synthetic_log):
        assert FTC4Log(synthetic_log).timestamp == dt.datetime(2026, 9, 9, 22, 36)

    def test_invalid_date_yields_none(self):
        log = FTC4Log(build_log({}, month=13, day=40))
        assert log.timestamp is None

    def test_time_from_filename(self, synthetic_log):
        log = FTC4Log(synthetic_log, filename="223619.LOG")
        assert log.timestamp_from_filename == dt.time(22, 36, 19)

    @pytest.mark.parametrize("name", ["TEST.LOG", "abc.LOG", "12345.LOG", "", "996199.LOG"])
    def test_unparsable_filename_yields_none(self, synthetic_log, name):
        assert FTC4Log(synthetic_log, filename=name).timestamp_from_filename is None


class TestEncodings:
    """Die beiden nachgewiesenen Temperaturkodierungen."""

    def test_temp_centi_is_le16_over_100(self, synthetic_log):
        decoded = field_by_key(FTC4Log(synthetic_log), "soll01")
        assert decoded.raw == 2000
        assert decoded.value == 20.0
        assert decoded.hex == "d0 07"

    def test_temp_centi_resolves_half_degrees(self, synthetic_log):
        assert field_by_key(FTC4Log(synthetic_log), "soll03").value == 28.5

    def test_temp_half40_byte(self, synthetic_log):
        """Byte/2-40: 119 -> 19.5 C."""
        decoded = field_by_key(FTC4Log(synthetic_log), "vor01")
        assert decoded.raw == 119
        assert decoded.value == 19.5

    @pytest.mark.parametrize("raw, celsius", [(0, -40.0), (80, 0.0), (120, 20.0), (255, 87.5)])
    def test_half40_covers_negative_range(self, raw, celsius):
        log = FTC4Log(build_log({0x60: bytes([raw])}))
        assert field_by_key(log, "vor01").value == celsius

    def test_status_byte_stays_raw_with_temperature_alternative(self, synthetic_log):
        decoded = field_by_key(FTC4Log(synthetic_log), "rec01_status")
        assert decoded.raw == 131
        assert decoded.value == 131          # roh, nicht umgerechnet
        assert decoded.alternatives["temp_half40"] == 25.5

    def test_implausible_value_is_flagged(self):
        log = FTC4Log(build_log({0x4E: (60000).to_bytes(2, "little")}))
        assert field_by_key(log, "soll01").plausible is False


class TestLayout:
    """Das Feldlayout deckt die belegten Bytes ab."""

    def test_layout_offsets_within_file(self):
        for spec in LOG_LAYOUT:
            assert 0 <= spec.offset + spec.width <= LOG_SIZE

    def test_layout_keys_unique(self):
        keys = [spec.key for spec in LOG_LAYOUT]
        assert len(keys) == len(set(keys))

    def test_layout_fields_do_not_overlap(self):
        used = {}
        for spec in LOG_LAYOUT:
            for offset in range(spec.offset, spec.offset + spec.width):
                assert offset not in used, (
                    f"0x{offset:03x}: {spec.key} ueberlappt mit {used[offset]}"
                )
                used[offset] = spec.key

    def test_nine_setpoints_and_nine_records(self):
        keys = {spec.key for spec in LOG_LAYOUT}
        assert {f"soll{i:02d}" for i in range(1, 10)} <= keys
        assert {f"rec{i:02d}_wert" for i in range(1, 10)} <= keys

    def test_unmapped_bytes_reports_stray_data(self, synthetic_log):
        data = bytearray(synthetic_log)
        data[0x120] = 0x42                      # Offset, den kein Feld abdeckt
        data[511] = (-sum(data[:511])) % 256
        assert (0x120, 0x42) in FTC4Log(bytes(data)).unmapped_bytes()

    def test_no_unmapped_bytes_in_clean_log(self, synthetic_log):
        assert FTC4Log(synthetic_log).unmapped_bytes() == []


class TestTextReport:
    """Klartext-Ausgabe."""

    def test_contains_core_information(self, synthetic_log):
        text = render_text(FTC4Log(synthetic_log, filename="223619.LOG"))
        assert "223619.LOG" in text
        assert "09.09.2026 22:36" in text
        assert "Pruefsumme                     : OK" in text
        assert "20.00 C" in text
        assert "19.5 C" in text

    def test_warns_about_broken_checksum(self, synthetic_log):
        broken = bytearray(synthetic_log)
        broken[0x50] ^= 0x01
        text = render_text(FTC4Log(bytes(broken)))
        assert "FEHLERHAFT" in text
        assert "beschaedigt" in text

    def test_states_that_field_meanings_are_unproven(self, synthetic_log):
        """Der Report darf keine Feldbedeutung behaupten, die nicht belegt ist."""
        assert "NICHT" in render_text(FTC4Log(synthetic_log))

    def test_custom_names_replace_labels(self, synthetic_log):
        text = render_text(FTC4Log(synthetic_log), names={"soll01": "Raumtemp Zone 1"})
        assert "Raumtemp Zone 1" in text
        assert "Sollwert 01" not in text

    def test_hexdump_skips_empty_lines(self, synthetic_log):
        dump = render_hexdump(FTC4Log(synthetic_log, filename="x.LOG"))
        assert "0x040" in dump          # Zeile mit 0x4e ist belegt
        assert "0x120" not in dump      # reiner Nullbereich

    def test_as_dict_is_json_serialisable(self, synthetic_log):
        payload = FTC4Log(synthetic_log, filename="223619.LOG").as_dict()
        assert json.loads(json.dumps(payload))["checksum_ok"] is True


class TestDiff:
    """Vergleich zweier Logs -- das Werkzeug zur Feldzuordnung."""

    def test_lists_changed_field(self):
        older = FTC4Log(build_log({0x4E: (2000).to_bytes(2, "little")}, minute=0), "000000.LOG")
        newer = FTC4Log(build_log({0x4E: (2050).to_bytes(2, "little")}, minute=1), "000100.LOG")
        text = render_diff(older, newer)
        assert "Sollwert 01" in text
        assert "+0.50" in text

    def test_reports_no_change(self, synthetic_log):
        log = FTC4Log(synthetic_log, "a.LOG")
        assert "keine Feldaenderung" in render_diff(log, log)

    def test_flags_changes_outside_the_layout(self, synthetic_log):
        """Aenderungen an nicht zugeordneten Bytes muessen auffallen."""
        other = bytearray(synthetic_log)
        other[0x120] = 0x07
        other[511] = (-sum(other[:511])) % 256
        text = render_diff(FTC4Log(synthetic_log, "a.LOG"), FTC4Log(bytes(other), "b.LOG"))
        assert "0x120" in text


class TestCsv:
    """Zeitreihen-Export."""

    def test_writes_row_per_log(self, tmp_path, synthetic_log):
        logs = [
            FTC4Log(build_log({0x4E: (2000).to_bytes(2, "little")}, minute=5), "000500.LOG"),
            FTC4Log(build_log({0x4E: (2100).to_bytes(2, "little")}, minute=1), "000100.LOG"),
        ]
        target = write_csv(logs, tmp_path / "reihe.csv")
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("datei;zeitstempel;pruefsumme_ok;")
        # nach Zeitstempel sortiert, nicht in Eingabereihenfolge
        assert lines[1].startswith("000100.LOG")
        assert lines[2].startswith("000500.LOG")

    def test_uses_custom_names_in_header(self, tmp_path, synthetic_log):
        target = write_csv([FTC4Log(synthetic_log, "a.LOG")], tmp_path / "r.csv",
                           names={"soll01": "WW-Solltemp"})
        assert "WW-Solltemp" in target.read_text(encoding="utf-8").splitlines()[0]


class TestNames:
    """Eigene Feldnamen aus JSON."""

    def test_loads_valid_file(self, tmp_path):
        path = tmp_path / "namen.json"
        path.write_text(json.dumps({"soll06": "WW-Solltemp"}), encoding="utf-8")
        assert load_names(path) == {"soll06": "WW-Solltemp"}

    def test_rejects_unknown_key(self, tmp_path):
        path = tmp_path / "namen.json"
        path.write_text(json.dumps({"gibtsnicht": "X"}), encoding="utf-8")
        with pytest.raises(LogDecodeError, match="Unbekannte Feldschluessel"):
            load_names(path)

    def test_rejects_non_object(self, tmp_path):
        path = tmp_path / "namen.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(LogDecodeError, match="JSON-Objekt"):
            load_names(path)


class TestCli:
    """Kommandozeile."""

    @pytest.fixture
    def log_dir(self, tmp_path, synthetic_log):
        (tmp_path / "223619.LOG").write_bytes(synthetic_log)
        (tmp_path / "000047.LOG").write_bytes(
            build_log({0x4E: (2050).to_bytes(2, "little")}, day=10, hour=0, minute=0)
        )
        (tmp_path / "TEST.LOG").write_bytes(bytes(range(16)))
        return tmp_path

    def test_decodes_directory_and_skips_test_log(self, log_dir, capsys):
        assert main([str(log_dir)]) == 0
        captured = capsys.readouterr()
        assert "223619.LOG" in captured.out
        assert "Kartentestdatei" in captured.err

    def test_diff_mode(self, log_dir, capsys):
        assert main([str(log_dir), "--diff"]) == 0
        assert "VERGLEICH" in capsys.readouterr().out

    def test_diff_needs_exactly_two(self, tmp_path, synthetic_log, capsys):
        (tmp_path / "223619.LOG").write_bytes(synthetic_log)
        assert main([str(tmp_path), "--diff"]) == 2
        assert "genau zwei" in capsys.readouterr().err

    def test_csv_mode(self, log_dir, tmp_path, capsys):
        target = tmp_path / "out.csv"
        assert main([str(log_dir), "--csv", str(target)]) == 0
        assert target.is_file()
        assert "2 Log(s)" in capsys.readouterr().out

    def test_json_mode(self, log_dir, capsys):
        assert main([str(log_dir), "--json"]) == 0
        assert len(json.loads(capsys.readouterr().out)) == 2

    def test_hex_mode(self, log_dir, capsys):
        assert main([str(log_dir / "223619.LOG"), "--hex"]) == 0
        assert "HEXDUMP" in capsys.readouterr().out

    def test_names_option(self, log_dir, tmp_path, capsys):
        names = tmp_path / "namen.json"
        names.write_text(json.dumps({"soll01": "Raumtemp"}), encoding="utf-8")
        assert main([str(log_dir / "223619.LOG"), "--names", str(names)]) == 0
        assert "Raumtemp" in capsys.readouterr().out

    def test_bad_names_file(self, log_dir, tmp_path, capsys):
        names = tmp_path / "namen.json"
        names.write_text("{kaputt", encoding="utf-8")
        assert main([str(log_dir / "223619.LOG"), "--names", str(names)]) == 2
        assert "Namensdatei" in capsys.readouterr().err

    def test_no_usable_file(self, tmp_path, capsys):
        (tmp_path / "TEST.LOG").write_bytes(bytes(range(16)))
        assert main([str(tmp_path)]) == 1
        assert "Keine verwertbare Logdatei" in capsys.readouterr().err


class TestAgainstRealLogs:
    """Validierung gegen echte Logdateien in data/logs/.

    Uebersprungen, solange dort nichts liegt.
    """

    def test_checksum_holds_for_every_log(self, real_logs):
        """Die Pruefsummenregel muss fuer jedes echte Log gelten."""
        for path in real_logs:
            log = FTC4Log.from_path(path)
            assert log.checksum_ok, f"{path.name}: Pruefsumme stimmt nicht"

    def test_header_timestamp_matches_filename(self, real_logs):
        """Header-Uhrzeit und Dateiname muessen auf die Minute uebereinstimmen."""
        for path in real_logs:
            log = FTC4Log.from_path(path)
            stamp, from_name = log.timestamp, log.timestamp_from_filename
            if stamp is None or from_name is None:
                continue
            assert (stamp.hour, stamp.minute) == (from_name.hour, from_name.minute), (
                f"{path.name}: Header {stamp:%H:%M} vs. Dateiname {from_name:%H:%M}"
            )

    def test_setpoints_are_plausible_temperatures(self, real_logs):
        for path in real_logs:
            log = FTC4Log.from_path(path)
            for decoded in log.decode_all():
                if decoded.spec.encoding is TEMP_CENTI:
                    assert decoded.plausible, (
                        f"{path.name}/{decoded.spec.key}: {decoded.value} C ist "
                        f"keine plausible Temperatur -- Layout pruefen"
                    )

    def test_every_active_byte_is_mapped(self, real_logs):
        """Kein belegtes Byte darf unbemerkt durchrutschen."""
        for path in real_logs:
            unmapped = FTC4Log.from_path(path).unmapped_bytes()
            assert unmapped == [], f"{path.name}: nicht zugeordnete Bytes {unmapped}"

    def test_text_report_renders(self, real_logs):
        for path in real_logs:
            assert "FTC4-LOG" in render_text(FTC4Log.from_path(path))
