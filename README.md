# FTC4-Analyzer

Dekodierung der Mitsubishi-Ecodan-FTC4-Dateien von der SD-Karte:
Konfiguration (`*.DAT`) und Betriebslogs (`*.LOG`), je 512 Byte.

```
src/temperature_decoder.py           DAT-Dateien: 0.5-°C-Schritte, Sollwerte
src/log_decoder.py                   LOG-Dateien: Klartext-Report, Diff, CSV
tests/                               136 Tests
docs/TEMPERATURE_DECODING.md         DAT-Format: Hypothesen und Verifikation
docs/LOG_FORMAT.md                   LOG-Format: Aufbau und Feldzuordnung
```

## Logdateien lesen

```bash
python3 -m src.log_decoder data/logs                      # Klartext-Report
python3 -m src.log_decoder a.LOG b.LOG --diff             # nur die Änderungen
python3 -m src.log_decoder data/logs --csv reihe.csv      # Zeitreihe für Excel/HA
python3 -m src.log_decoder data/logs --json               # maschinenlesbar
python3 -m src.log_decoder a.LOG --names namen.json       # eigene Feldnamen
```

Die 512-Byte-Logs enthalten Zeitstempel, Prüfsumme und Temperaturen in zwei
Kodierungen (`LE16/100` und `Byte/2−40`). `TEST.LOG` ist nur der
SD-Karten-Schreibtest und wird übersprungen. Welcher Offset welcher Sensor
ist, steht **nicht** fest — `--diff` und `--csv` sind die Werkzeuge, um das
gegen das FTC4-Display zu ermitteln, siehe `docs/LOG_FORMAT.md`.

## DAT-Dateien


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

Tests gegen echte Gerätedaten (`TestAgainstRealDeviceData`,
`TestAgainstRealLogs`) laufen automatisch mit, sobald die Dateien lokal unter
`data/` bzw. `data/logs/` liegen; sonst werden sie übersprungen.

## Status der Hypothesen

| Aussage | Status |
|---------|--------|
| LOG: Prüfsumme, Summe aller 512 Byte = 0 mod 256 | bestätigt an 2 Dateien |
| LOG: Zeitstempel `YY MM DD HH MM` auf 0x00 | bestätigt |
| LOG: `LE16/100` und `Byte/2−40` als Temperatur | bestätigt |
| LOG: Bedeutung der einzelnen Felder | **offen** |
| DAT: `celsius = raw * 0.5` | bestätigt für 0x4c/0x64/0x5a |
| Heiz-Sollwert auf Offset `0x02` | **Hypothese** |
| Kühl-Sollwert auf Offset `0x04` | **Hypothese** |
| Byte-Breite `u8` vs. `u16le` | **offen** |
| Prüfsumme über die Datei | nicht untersucht |

Details und Verifikationsverfahren: `docs/TEMPERATURE_DECODING.md` (DAT) und
`docs/LOG_FORMAT.md` (LOG).

Die DAT- und LOG-Dateien selbst sind über `.gitignore` vom Repository
ausgeschlossen — sie gehören lokal nach `data/` bzw. `data/logs/`.
