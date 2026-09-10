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

#: Rohwerte des synthetischen HT_CL.DAT.
SYNTHETIC_HEATING_RAW = 0x5A  # 90 -> 45.0 C
SYNTHETIC_COOLING_RAW = 0x18  # 24 -> 12.0 C


@pytest.fixture
def synthetic_ht_cl() -> bytes:
    """Ein kuenstliches HT_CL.DAT mit bekannten Sollwerten.

    ACHTUNG: Das sind keine Geraetedaten. Die Datei bildet nur die
    dokumentierte Struktur nach (512 Byte, Sollwerte auf Offset 0x02 und 0x04,
    Rest Padding) und dient dazu, die Dekodierlogik deterministisch zu testen.
    Die Bestaetigung der Offsets erfolgt gegen echte Dateien -- siehe
    ``real_ht_cl`` und docs/TEMPERATURE_DECODING.md.
    """
    data = bytearray(TemperatureDecoder.DAT_FILE_SIZE)
    data[0x00] = 0x01  # Modus/Marker, wie in der Formatanalyse beobachtet
    data[0x02] = SYNTHETIC_HEATING_RAW
    data[0x04] = SYNTHETIC_COOLING_RAW
    data[0x06] = 0x06  # Beginn eines Parameterblocks (06 00 38)
    data[0x08] = 0x38
    return bytes(data)


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
