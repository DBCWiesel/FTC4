"""Tests fuer den Gesamt-Export eines SD-Karten-Abzugs."""

from __future__ import annotations

import pytest

from conftest import HT_CL_RECORDS, build_dat, build_log
from src.decode_all import build_report, find_files, main


@pytest.fixture
def card(tmp_path, synthetic_log):
    """Ein Kartenabzug im Aufbau der FTC4: SETTING/ und LOG/JJJJ/MM/TT/."""
    setting = tmp_path / "SETTING"
    setting.mkdir()
    (setting / "HT&CL.DAT").write_bytes(build_dat(HT_CL_RECORDS))
    (setting / "DHW.DAT").write_bytes(build_dat([(0x0F, 0x00, 0x7E)]))

    day = tmp_path / "LOG" / "2026" / "09" / "09"
    day.mkdir(parents=True)
    (day / "223619.LOG").write_bytes(synthetic_log)
    (day / "223719.LOG").write_bytes(
        build_log({0x4E: (2050).to_bytes(2, "little")}, hour=22, minute=37)
    )
    test_dir = tmp_path / "LOG" / "DIR1" / "DIR2" / "DIR3"
    test_dir.mkdir(parents=True)
    (test_dir / "TEST.LOG").write_bytes(bytes(range(16)))
    return tmp_path


class TestFindFiles:
    def test_separates_dat_log_and_test(self, card):
        dats, logs, tests = find_files(card)
        assert [p.name for p in dats] == ["DHW.DAT", "HT&CL.DAT"]
        assert [p.name for p in logs] == ["223619.LOG", "223719.LOG"]
        assert [p.name for p in tests] == ["TEST.LOG"]

    def test_empty_directory(self, tmp_path):
        assert find_files(tmp_path) == ([], [], [])


class TestBuildReport:
    def test_covers_configuration_and_logs(self, card):
        report = build_report(card)
        assert "TEIL 1 -- KONFIGURATION" in report
        assert "TEIL 2 -- BETRIEBSLOGS" in report
        assert "HT&CL.DAT" in report
        assert "18.0 C" in report

    def test_counts_files(self, card):
        report = build_report(card)
        assert "Konfigurationsdateien : 2" in report
        assert "Logdateien            : 2" in report
        assert "Kartentestdateien     : 1" in report

    def test_separates_moving_from_constant_fields(self, card):
        report = build_report(card)
        assert "VERAENDERLICHE FELDER" in report
        assert "KONSTANTE FELDER" in report

    def test_reports_checksum_summary(self, card):
        assert "2 von 2 in Ordnung" in build_report(card)

    def test_flags_broken_checksum(self, card, synthetic_log):
        broken = bytearray(synthetic_log)
        broken[0x50] ^= 0x01
        (card / "LOG" / "2026" / "09" / "09" / "223819.LOG").write_bytes(bytes(broken))
        assert "fehlerhaft" in build_report(card)

    def test_states_what_is_unproven(self, card):
        report = build_report(card)
        assert "NICHT belegt" in report
        assert "Platzhalter" in report

    def test_full_records_option(self, card):
        long_dat = build_dat([(0x06, 0x00, 0x01)] * 100)
        (card / "SETTING" / "SCH_1.DAT").write_bytes(long_dat)
        assert "weitere Records" in build_report(card, full_records=False)
        assert "weitere Records" not in build_report(card, full_records=True)

    def test_handles_missing_logs(self, tmp_path):
        setting = tmp_path / "SETTING"
        setting.mkdir()
        (setting / "DHW.DAT").write_bytes(build_dat([(0x0F, 0x00, 0x7E)]))
        report = build_report(tmp_path)
        assert "TEIL 1" in report
        assert "TEIL 2" not in report


class TestCli:
    def test_writes_report_file(self, card, tmp_path, capsys):
        target = tmp_path / "report.txt"
        assert main([str(card), "-o", str(target)]) == 0
        assert target.is_file()
        assert "TEIL 1" in target.read_text(encoding="utf-8")
        assert "Report geschrieben" in capsys.readouterr().out

    def test_prints_to_stdout(self, card, capsys):
        assert main([str(card)]) == 0
        assert "FTC4 SD-KARTEN-ABZUG" in capsys.readouterr().out

    def test_csv_export(self, card, tmp_path, capsys):
        target = tmp_path / "reihe.csv"
        assert main([str(card), "-o", str(tmp_path / "r.txt"), "--csv", str(target)]) == 0
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3          # Kopfzeile + zwei Logs

    def test_rejects_non_directory(self, tmp_path, capsys):
        assert main([str(tmp_path / "gibtsnicht")]) == 2
        assert "Kein Verzeichnis" in capsys.readouterr().err


class TestAgainstRealCard:
    """Gegen einen echten Abzug in data/, wenn vorhanden."""

    def test_report_builds(self, real_dat_files, real_logs, tmp_path):
        setting = tmp_path / "SETTING"
        setting.mkdir()
        for path in real_dat_files:
            (setting / path.name).write_bytes(path.read_bytes())
        day = tmp_path / "LOG" / "2026" / "09" / "09"
        day.mkdir(parents=True)
        for path in real_logs[:20]:
            (day / path.name).write_bytes(path.read_bytes())

        report = build_report(tmp_path)
        assert "TEIL 1 -- KONFIGURATION" in report
        assert "TEIL 2 -- BETRIEBSLOGS" in report
        assert "in Ordnung" in report
        assert "fehlerhaft" not in report
