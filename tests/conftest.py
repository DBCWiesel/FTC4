"""Gemeinsame Fixtures fuer die Testsuite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.temperature_decoder import TemperatureDecoder  # noqa: E402

#: Ablage der echten, vom Geraet gesicherten DAT-Dateien.
DATA_DIR = PROJECT_ROOT / "data"

def build_dat(records: "list[tuple[int, int, int]]") -> bytes:
    """Baut eine 512-Byte-DAT-Datei aus 3-Byte-Records inklusive Pruefsumme.

    ``records`` ist eine Liste von ``(typ, hi, lo)``. Die Pruefsumme wird so
    gesetzt, dass die Summe aller 512 Bytes 0 modulo 256 ergibt -- die Regel,
    die an allen elf Dateien des Referenzabzugs gilt.
    """
    data = bytearray(512)
    for index, (type_byte, hi, lo) in enumerate(records):
        data[index * 3 : index * 3 + 3] = bytes([type_byte, hi, lo])
    data[511] = (-sum(data[:511])) % 256
    return bytes(data)


#: Nachbau von HT&CL.DAT: dieselbe Recordfolge wie im echten Geraeteabzug.
#: Records 2..6 und 10..12 tragen Temperaturen, darunter die drei am Geraet
#: abgelesenen Referenzwerte 0x4c/0x64/0x5a.
HT_CL_RECORDS = [
    (0x0F, 0x00, 0x00),   # 0.0 C  -- eher Modus-Flag
    (0x0F, 0x00, 0x02),   # 1.0 C  -- eher Modus-Flag
    (0x0F, 0x00, 0x4C),   # 38.0 C
    (0x0F, 0x00, 0x64),   # 50.0 C
    (0x0F, 0x00, 0x5A),   # 45.0 C
    (0x0F, 0x00, 0x5A),   # 45.0 C
    (0x0F, 0x00, 0x46),   # 35.0 C
    (0x02, 0x89, 0x25),
    (0x02, 0x18, 0x25),
    (0x02, 0x95, 0x60),
    (0x0F, 0x00, 0x50),   # 40.0 C
    (0x0F, 0x00, 0x6E),   # 55.0 C
    (0x0F, 0x00, 0x6E),   # 55.0 C
    (0x02, 0x95, 0x40),
    (0x02, 0x35, 0x25),
    (0x02, 0x95, 0x60),
]


@pytest.fixture
def synthetic_ht_cl() -> bytes:
    """Ein HT&CL.DAT im nachgewiesenen Recordaufbau.

    Keine Geraetedaten, aber strukturgleich zum echten Abzug: 16 Records a
    3 Byte, Temperaturen im LO-Byte der Typ-0x0f-Records, gueltige Pruefsumme.
    """
    return build_dat(HT_CL_RECORDS)


@pytest.fixture
def real_ht_cl() -> bytes:
    """Echtes HT_CL.DAT aus ``data/`` -- uebersprungen, wenn nicht vorhanden.

    Die DAT-Dateien liegen nicht im Repository. Wer sie von der SD-Karte nach
    ``data/`` kopiert, bekommt die Validierung gegen Geraetedaten automatisch
    mitgetestet.
    """
    path = DATA_DIR / "HT_CL.DAT"
    if not path.is_file():
        pytest.skip(f"{path} nicht vorhanden - Validierung gegen Geraetedaten uebersprungen")
    return path.read_bytes()


# ----------------------------------------------------------------------
# Logdateien
# ----------------------------------------------------------------------

LOG_DIR = DATA_DIR / "logs"


def build_log(fields: "dict[int, bytes]", year=26, month=9, day=9, hour=22, minute=36) -> bytes:
    """Baut eine syntaktisch korrekte 512-Byte-Logdatei inklusive Pruefsumme.

    ``fields`` bildet Offsets auf Rohbytes ab. Die Pruefsumme wird so gesetzt,
    dass die Summe aller 512 Bytes 0 modulo 256 ergibt -- genau wie es die
    FTC4 in beiden Referenzdateien tut.
    """
    data = bytearray(512)
    data[0:6] = bytes([year, month, day, hour, minute, 0x12])
    for offset, raw in fields.items():
        data[offset : offset + len(raw)] = raw
    data[511] = (-sum(data[:511])) % 256
    return bytes(data)


@pytest.fixture
def synthetic_log() -> bytes:
    """Ein kuenstliches Log mit bekannten Werten an den nachgewiesenen Offsets.

    Keine Geraetedaten -- bildet nur die Struktur nach: 20.00 C als LE16/100
    auf 0x4e, 25.50 C auf 0x65 mit Statusbyte, und ein Byte im 0.5-Grad-Raster
    mit Nullpunkt -40 auf 0x60.
    """
    return build_log({
        0x4E: (2000).to_bytes(2, "little"),   # 20.00 C
        0x52: (2850).to_bytes(2, "little"),   # 28.50 C
        0x60: bytes([119]),                   # 119/2-40 = 19.5 C
        0x65: (2550).to_bytes(2, "little"),   # 25.50 C
        0x67: bytes([131]),                   # 131/2-40 = 25.5 C
    })


@pytest.fixture
def real_logs() -> "list[Path]":
    """Echte .LOG-Dateien aus ``data/logs/`` -- uebersprungen, wenn keine da sind."""
    if not LOG_DIR.is_dir():
        pytest.skip(f"{LOG_DIR} nicht vorhanden - Validierung gegen Geraetedaten uebersprungen")
    found = sorted(p for p in LOG_DIR.glob("*.LOG") if p.stat().st_size == 512)
    if not found:
        pytest.skip(f"keine 512-Byte-Logs in {LOG_DIR}")
    return found


@pytest.fixture
def real_dat_files() -> "list[Path]":
    """Echte .DAT-Dateien aus ``data/`` -- uebersprungen, wenn keine da sind."""
    if not DATA_DIR.is_dir():
        pytest.skip(f"{DATA_DIR} nicht vorhanden")
    found = sorted(p for p in DATA_DIR.glob("*.DAT") if p.stat().st_size == 512)
    if not found:
        pytest.skip(f"keine 512-Byte-DAT-Dateien in {DATA_DIR}")
    return found
