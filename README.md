# FTC4-Analyzer

Dekodierung der Mitsubishi-Ecodan-FTC4-Dateien von der SD-Karte:
Konfiguration (`*.DAT`) und Betriebslogs (`*.LOG`), je 512 Byte.

```
src/decode_all.py                    ganzer Kartenabzug -> eine Textdatei
src/ha_correlate.py                  Logfelder <-> Home-Assistant-Sensoren
src/rc_settings.py                   Einstellungen anhand der Herstellertabelle
src/dat_decoder.py                   SETTING/*.DAT: Records, Zeitprogramme
src/log_decoder.py                   LOG/*.LOG: Report, Diff, Zeitreihe
src/temperature_decoder.py           Rechenschicht: 0,5-°C-Kodierung
tests/                               273 Tests
docs/HOME_ASSISTANT.md               Werte aus HA holen und zuordnen
docs/SD_TOOL.md                      was das Hersteller-Werkzeug preisgibt
docs/DAT_FORMAT.md                   DAT-Format: Record-Struktur
docs/LOG_FORMAT.md                   LOG-Format: Aufbau und Feldzuordnung
docs/TEMPERATURE_DECODING.md         Temperaturkodierungen im Überblick
```

## Alles auf einmal

```bash
python3 -m src.decode_all /pfad/zum/kartenabzug -o ftc4.txt --csv reihe.csv
```

Läuft über `SETTING/` und `LOG/` und schreibt einen zusammenhängenden
Klartext-Report: Konfiguration Record für Record, Logs als Zeitreihe mit
veränderlichen und konstanten Feldern, plus erstes und letztes Log im Detail.

## Einstellungen benennen

Mit der Definitionstabelle aus dem Hersteller-Werkzeug:

```bash
python3 -m src.rc_settings /pfad/SD_TOOL/Teigi/RCSetting_EN.xls data
```

Nennt je Eintrag Titel, Rohwert, erschlossenen Wert und dokumentierten Bereich.
Siehe `docs/SD_TOOL.md`.

## Felder benennen

Welcher Offset welcher Sensor ist, steht nicht fest. Wer ein ESPHome-Modul an
der Wärmepumpe hat, bekommt die Zuordnung automatisch:

```bash
python3 -m src.ha_correlate ha.json data/logs --namen namen.json
python3 -m src.log_decoder data/logs --names namen.json
```

Der Zeitversatz zwischen HA (UTC) und FTC4 (Ortszeit) wird selbst bestimmt.
Wie man den Verlauf aus Home Assistant exportiert: `docs/HOME_ASSISTANT.md`.

## Konfigurationsdateien lesen

```bash
python3 -m src.dat_decoder data                 # alle DAT-Dateien
python3 -m src.dat_decoder data/HT\&CL.DAT --alle
python3 -m src.dat_decoder data --json
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
| Prüfsumme: Summe aller 512 Byte = 0 mod 256, in DAT **und** LOG | bestätigt an 484 Dateien |
| DAT: 3-Byte-Records `[TYP][HI][LO]` | bestätigt, 11/11 Dateien |
| DAT: Typ `0f` mit HI=0 = Einzelwert; in HT&CL.DAT °C in 0,5-Schritten | bestätigt gegen Gerätewerte |
| DAT: `ff ff` = nicht belegter Eintrag | bestätigt |
| DAT: 35 Zeitfenster je SCH-Datei = 7 Tage × 5 | bestätigt |
| LOG: Zeitstempel `YY MM DD HH MM`, Mitternacht als Stunde 24 | bestätigt an 473 Logs |
| LOG: `LE16/100` und `Byte/2−40` als Temperatur | bestätigt gegen HA-Sensoren |
| LOG: 13 Temperaturspalten + 7 Digitaleingänge benannt | Herstellertabelle, 11 davon gegengeprüft |
| DAT: Typ `03`/`04` = BCD-Zahl bzw. BCD-Uhrzeit | bestätigt an 8 Werksvorgaben |
| DAT: Byte-Nummern der Herstellertabelle sind Vielfache von 3 | bestätigt |
| Sollwerte in HT&CL.DAT auf Offset `0x02`/`0x04` | **widerlegt** |
| Bedeutung der übrigen Log-Felder und aller DAT-Records | **offen** |
| Kodierung der Typ-`06`-Zeitfenster | **offen** |

Details und Verifikationsverfahren: `docs/DAT_FORMAT.md` und `docs/LOG_FORMAT.md`.

Die DAT- und LOG-Dateien selbst sind über `.gitignore` vom Repository
ausgeschlossen — sie gehören lokal nach `data/` bzw. `data/logs/`.
