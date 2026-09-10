# FTC4-Analyzer — Temperature Decoder

Dekodierung von Mitsubishi-Ecodan-FTC4-Konfigurationsdateien (11 × 512 Byte
DAT-Dateien von der SD-Karte).

Dieses Repository enthält aktuell den **Temperature Decoder** aus dem
Feature-Branch `feature/temperature-decoder`.

```
src/temperature_decoder.py           Dekodierung/Kodierung, Sollwert-Extraktion
tests/test_temperature_decoder.py    77 Unit Tests + 3 Tests gegen Gerätedaten
docs/TEMPERATURE_DECODING.md         Hypothesen und Verifikationsverfahren
```

## Verwendung

```python
from src.temperature_decoder import TemperatureDecoder

# 0.5-Grad-Schritte
TemperatureDecoder.decode_0_5_degree_steps(0x4c)   # -> 38.0
TemperatureDecoder.encode_0_5_degree_steps(38.0)   # -> 76

# Sollwerte aus HT_CL.DAT
data = Path("data/HT_CL.DAT").read_bytes()
TemperatureDecoder.extract_setpoints(data)
# {'heating_setpoint': 38.0, 'cooling_setpoint': 12.0}

# Mit Herkunft und Plausibilitätsbewertung
TemperatureDecoder.validate_setpoints(data)
# {'ok': True, 'issues': [], 'setpoints': {...}}
```

Das Modul ist abhängigkeitsfrei und arbeitet direkt auf `bytes`. Ein
`FTC4Analyzer`-Puffer lässt sich unverändert durchreichen:

```python
TemperatureDecoder.extract_setpoints(analyzer.files["HT_CL.DAT"])
```

## CLI

```bash
python3 -m src.temperature_decoder data/HT_CL.DAT
```

Validiert zuerst die Dekodierung gegen die bekannten Gerätewerte, gibt dann
die Sollwerte aus und listet alle temperaturverdächtigen Offsets — das
Werkzeug, um die Offset-Hypothesen zu prüfen.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Die drei Tests in `TestAgainstRealDeviceData` werden übersprungen, solange
`data/HT_CL.DAT` fehlt. Wer die Dateien von der SD-Karte nach `data/` kopiert,
bekommt die Validierung gegen echte Gerätedaten automatisch mit. Die DAT-Dateien
selbst sind über `.gitignore` vom Repository ausgeschlossen.

## Status der Hypothesen

| Aussage | Status |
|---------|--------|
| `celsius = raw * 0.5` | bestätigt für 0x4c/0x64/0x5a |
| Heiz-Sollwert auf Offset `0x02` | **Hypothese** |
| Kühl-Sollwert auf Offset `0x04` | **Hypothese** |
| Byte-Breite `u8` vs. `u16le` | **offen** |
| Prüfsumme über die Datei | nicht untersucht |

Details und das Verifikationsverfahren am Gerät: `docs/TEMPERATURE_DECODING.md`.
