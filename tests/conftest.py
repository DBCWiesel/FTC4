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
