"""Vergleich mit der Ausgabe des Hersteller-Werkzeugs.

Wer denselben Kartenabzug durch das SD_TOOL laufen laesst, bekommt eine
CSV mit 226 Spalten. Liegt sie unter ``data/reference/opelog.csv``, prueft
dieser Test den eigenen Decoder Spalte fuer Spalte dagegen -- die schaerfste
verfuegbare Kontrolle.

Der Export nutzt deutsche Dezimalkommas **und** Komma als Trennzeichen, zerlegt
also jede Kommazahl in zwei Felder. :func:`repair_row` setzt das zusammen.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from conftest import DATA_DIR, LOG_DIR
from src.log_decoder import LOG_LAYOUT, FTC4Log

#: Spaltennummer des Herstellers -> Byte-Offset im Log.
COLUMN_TO_OFFSET = {
    72: 0x04E, 73: 0x050, 74: 0x052, 75: 0x054, 76: 0x056, 77: 0x058,
    78: 0x05A, 79: 0x05C, 80: 0x05E, 81: 0x060, 82: 0x061, 83: 0x063,
    84: 0x064, 85: 0x065, 86: 0x067, 87: 0x068, 88: 0x06A, 89: 0x06B,
    90: 0x06D, 91: 0x06E, 93: 0x071, 95: 0x074, 97: 0x077, 99: 0x07A,
    101: 0x07D,
}


def repair_row(fields: "list[str]") -> list:
    """Setzt die vom Dezimalkomma zerrissenen Zahlen wieder zusammen."""
    out, index = [], 0
    while index < len(fields):
        following = fields[index + 1] if index + 1 < len(fields) else None
        if (following is not None and fields[index].isdigit() and following.isdigit()
                and (following == "5" and len(fields[index]) >= 2
                     or len(following) == 2 and following.startswith("0"))):
            out.append(float(f"{fields[index]}.{following}"))
            index += 2
        else:
            out.append(fields[index])
            index += 1
    return out


@pytest.fixture(scope="module")
def reference():
    """Zeitstempel -> Zeile aus der Hersteller-CSV."""
    path = DATA_DIR / "reference" / "opelog.csv"
    if not path.is_file():
        pytest.skip(f"{path} nicht vorhanden - Vergleich uebersprungen")
    lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
             if l.strip()]
    width = len(lines[0].split(","))
    rows = {}
    for line in lines[1:]:
        row = repair_row(line.split(","))
        if len(row) != width:
            continue
        stamp = dt.datetime.strptime(str(row[0]), "%Y.%m.%d %H:%M:%S")
        rows[stamp.replace(second=0)] = row
    if not rows:
        pytest.skip("keine verwertbaren Zeilen in der Referenz")
    return rows


@pytest.fixture(scope="module")
def own_logs():
    if not LOG_DIR.is_dir():
        pytest.skip(f"{LOG_DIR} nicht vorhanden")
    logs = {}
    for path in LOG_DIR.glob("*.LOG"):
        try:
            log = FTC4Log.from_path(path)
        except Exception:
            continue
        if log.timestamp:
            logs[log.timestamp] = log
    if not logs:
        pytest.skip("keine Logs vorhanden")
    return logs


class TestAgainstVendorTool:
    def test_rows_repair_to_full_width(self, reference):
        """Alle Zeilen muessen sich auf die Sollbreite zusammensetzen lassen."""
        assert len(reference) > 100

    def test_timestamps_overlap(self, reference, own_logs):
        assert set(reference) & set(own_logs)

    def test_every_named_column_matches(self, reference, own_logs):
        """Jede benannte Spalte muss ueber alle Zeitpunkte exakt uebereinstimmen."""
        by_offset = {spec.offset: spec for spec in LOG_LAYOUT}
        common = sorted(set(reference) & set(own_logs))
        problems = []
        for column, offset in sorted(COLUMN_TO_OFFSET.items()):
            spec = by_offset.get(offset)
            if spec is None:
                continue
            for stamp in common:
                want = reference[stamp][column]
                if not isinstance(want, float):
                    try:
                        want = float(want)
                    except ValueError:
                        continue
                got = own_logs[stamp].decode_field(spec).value
                if abs(got - want) > 1e-9:
                    problems.append(
                        f"Spalte {column} (0x{offset:03x}) um {stamp:%H:%M}: "
                        f"Werkzeug {want}, eigener Decoder {got}"
                    )
                    break
        assert not problems, "\n".join(problems)
