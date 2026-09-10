# FTC4-Logdateien (*.LOG)

Nachgewiesen an **473 echten Logs** eines vollständigen SD-Karten-Abzugs
(09.09.2026 22:36 bis 10.09.2026 06:26, ein Log je Minute) sowie an `TEST.LOG`.

Bei allen 473 Dateien stimmt die Prüfsumme und deckt sich der Header-Zeitstempel
auf die Minute mit dem Dateinamen. Von 512 Byte-Offsets verändern sich über den
gesamten Zeitraum genau 22 — und **jeder davon liegt innerhalb des Feldlayouts**;
kein belegtes Byte bleibt unzugeordnet.

## TEST.LOG

16 Byte mit dem Inhalt `00 01 02 … 0f`. Das ist der **Schreibtest**, den die
FTC4 auf die SD-Karte legt — keine Nutzdaten. Der Decoder erkennt und
überspringt die Datei.

## Aufbau einer Logdatei

512 Byte fest, wie die DAT-Dateien.

| Offset        | Inhalt | Status |
|---------------|--------|--------|
| `0x00`–`0x04` | Zeitstempel, je ein Byte binär: `YY MM DD HH MM` | **bestätigt** |
| `0x05`        | konstant `0x12` in beiden Dateien | Bedeutung unbekannt |
| `0x06`–`0x4d` | Konfigurationsflags, überwiegend `0x00`, in beiden Dateien identisch | uninterpretiert |
| `0x4e`–`0x5f` | 9 Werte `LE16 / 100` → °C | Kodierung bestätigt |
| `0x60`–`0x64` | Einzelwerte, gemischte Kodierung | Kodierung bestätigt |
| `0x65`–`0x7f` | 9 Records à 3 Byte: `LE16/100` + 1 Statusbyte | Kodierung bestätigt |
| `0x80`–`0x1fe`| Einzelparameter, überwiegend `0x00` | uninterpretiert |
| `0x1ff`       | Prüfsumme | **bestätigt** |

### Zeitstempel

Plain binär, nicht BCD: `1a 09 09 16 24` → `26, 9, 9, 22, 36` → 09.09.2026 22:36.
Sekunden stehen nicht im Header — die liefert der Dateiname: `223619.LOG` =
22:36:19. Der Header ist also minutengenau, der Dateiname sekundengenau.

**Mitternacht schreibt die FTC4 als Stunde 24 des Vortags.** `240003.LOG`
trägt `1a 09 09 18 00` = 09.09.2026 24:00, gemeint ist 10.09.2026 00:00. Ein
naiver `datetime`-Aufruf scheitert daran; der Decoder normalisiert es.

### Prüfsumme

```
sum(alle 512 Bytes) mod 256 == 0
```

Also ist `Byte[0x1ff] = (-sum(Byte[0..0x1fe])) mod 256`. An beiden Dateien
verifiziert. Der Decoder prüft das bei jedem Einlesen; ein Fehlschlag bedeutet
beschädigte oder unvollständig kopierte Datei.

## Temperaturkodierungen

Die Logs verwenden **zwei Kodierungen — beide anders als die DAT-Dateien**:

| Name | Formel | Auflösung | Bereich |
|------|--------|-----------|---------|
| `temp_centi`  | `LE16 / 100`   | 0,01 °C | 0 … 655,35 °C |
| `temp_half40` | `Byte / 2 − 40`| 0,5 °C  | −40,0 … +87,5 °C |

`temp_half40` mit Nullpunkt −40 ist die klassische Wärmepumpen-Kodierung, weil
sie Minusgrade (Außentemperatur) in ein Byte bekommt.

**Wichtig:** Die 0,5-°C-Hypothese aus den DAT-Dateien (`byte * 0.5`) gilt hier
**nicht**. Verschiedene Dateitypen, verschiedene Kodierungen. Beides kann
nebeneinander stimmen.

### Beleg für `temp_half40`

In den Records folgt auf jeden LE16-Wert ein Byte. Zwischen den beiden Logs
bewegen sich beide exakt im Verhältnis 1 Zählschritt = 0,5 °C:

| Offset | LE16 vorher → nachher | Δ | Byte vorher → nachher | Δ × 0,5 °C |
|--------|----------------------|---|----------------------|------------|
| `0x61` | 24,50 → 26,00 °C | +1,50 | 125 → 128 | +1,50 |
| `0x65` | 28,00 → 28,50 °C | +0,50 | 133 → 134 | +0,50 |
| `0x68` | 27,00 → 27,50 °C | +0,50 | 131 → 132 | +0,50 |
| `0x6b` | 44,00 → 43,50 °C | −0,50 | 168 → 167 | −0,50 |

Bei `0x6b`/`0x6d` stimmen sogar die Absolutwerte überein: `167/2 − 40 = 43,5`
= genau der LE16-Wert. Das ist derselbe Sensor in zwei Auflösungen.

Bei den hinteren sechs Records steht im Statusbyte konstant `11` bzw. `2` —
als Temperatur ergäbe das −34,5 °C / −39,0 °C. Dort ist es also **kein**
Temperaturbyte, sondern ein Modus- oder Statuscode. Der Decoder gibt das Byte
deshalb roh aus und bietet die Temperaturlesart nur an, wo sie plausibel ist.

## Zugeordnete Felder

Abgeglichen gegen den ESPHome-Verlauf derselben Nacht (50 Sensoren aus Home
Assistant, 473 Logs, Zeitversatz +2 h). Aufgenommen ist nur, was über den
ganzen Zeitraum deckungsgleich lief **und** wo sich beide Reihen bewegt haben
— ein Gleichstand zweier konstanter Reihen ist kein Beweis.

| Offset | Kodierung | Sensor | Median-Abweichung |
|--------|-----------|--------|-------------------|
| `0x052` | LE16/100 | Vorlauftemperatur | 0,00 K |
| `0x05a` | LE16/100 | Zone 1 Raumtemperatur | 0,00 K |
| `0x05c` | LE16/100 | Zone 1 Raumtemperatur | 0,00 K |
| `0x05e` | LE16/100 | Zone 1 Raumtemperatur | 0,00 K |
| `0x061` | LE16/100 | Kältemittel Flüssigkeitstemperatur | 0,00 K |
| `0x064` | Byte/2−40 | Außentemperatur | 0,00 K |
| `0x065` | LE16/100 | Vorlauftemperatur | 0,00 K |
| `0x068` | LE16/100 | Rücklauftemperatur | 0,00 K |
| `0x06a` | Byte/2−40 | Kältemittel Flüssigkeitstemperatur | 0,00 K |
| `0x06b` | LE16/100 | TWW-Speichertemperatur | 0,00 K |
| `0x06d` | Byte/2−40 | TWW-Speichertemperatur | 0,00 K |

Damit ist auch die `Byte/2−40`-Kodierung gegen benannte Sensoren belegt, nicht
nur gegen den LE16-Nachbarwert.

Bestätigt sich dabei die frühere Beobachtung: `0x052` und `0x065` tragen
dieselbe Größe (Vorlauf), ebenso `0x06b`/`0x06d` (Speicher) und
`0x061`/`0x06a` (Kältemittel) — jeweils in beiden Auflösungen.

### Nur im Stillstand übereinstimmend

Plausibel, aber nicht bewiesen. Diese Felder standen die ganze Nacht still,
also passt jeder Sensor mit demselben Wert:

| Offset | Wert | Kandidat |
|--------|------|----------|
| `0x04e` | 18,00 °C | Zone 1 Raum-Sollwert |
| `0x056` | 43,00 °C | TWW-Sollwert |
| `0x058` | 60,00 °C | Legionellenschutz-Temperatur |

Zur Bestätigung braucht es einen Abzug aus einem Zeitraum, in dem sich diese
Werte ändern.

### Bewegt, aber ohne Gegenstück

`0x060` (19,0–20,0 °C), `0x063` (22,5–24,0 °C) und `0x067` (25,5–27,0 °C)
bewegen sich und liefern plausible Temperaturen, haben aber im HA-Export
keinen passenden Sensor. Vermutlich Fühler, die das ESPHome-Modul nicht
ausliest.

## Beobachteter Verlauf über die Nacht

Aus 473 Logs, rein deskriptiv — keine Feldzuordnung, nur was die Zahlen tun:

| Feld | Offset | 22:36 | 06:26 | Verhalten |
|------|--------|-------|-------|-----------|
| `rec03_wert` | `0x06b` | 44,00 °C | 36,50 °C | fällt über die Nacht stetig ab |
| `vor01` | `0x060` | 20,0 °C | 19,0 °C | fällt langsam |
| `vor03` | `0x064` | 16,0 °C | 14,0 °C | schwankt zwischen 14 und 16 |
| `soll07/08/09` | `0x05a/5c/5e` | 22,50 °C | 22,00 °C | alle drei springen gleichzeitig um 01:05 |
| `soll03` = `rec01_wert` | `0x052` / `0x065` | 28,00 °C | 27,00 °C | tragen **immer** denselben Wert |

Konstant über den gesamten Zeitraum: `0x04e` = 18,00 °C, `0x050` = 20,00 °C,
`0x054` = 35,00 °C, `0x056` = 43,00 °C, `0x058` = 60,00 °C und die sechs
Records ab `0x06e` auf je 25,00 °C.

Bemerkenswert: `0x052` und `0x065` sind über alle 473 Logs identisch — das ist
dieselbe Größe an zwei Stellen. Und `0x06b`/`0x06d` tragen denselben Wert in
zwei Auflösungen (LE16/100 und Byte/2−40).

## Was NICHT nachgewiesen ist

**Welcher Offset welcher Sensor bzw. welche Einstellung ist.** Zwei Logs mit
84 Minuten Abstand reichen für die Struktur, nicht für die Zuordnung. Die
Feldnamen im Decoder (`soll01`, `rec03_wert`) sind bewusst neutrale
Platzhalter.

Beobachtungen ohne Interpretation:

- `0x58` = 60,00 °C, in beiden Dateien unverändert
- `0x54` = 35,00 °C, `0x56` = 43,00 °C, unverändert
- `0x5a`/`0x5c`/`0x5e` = 22,50 °C, dreimal derselbe Wert
- `0x52` und `0x65` tragen immer **denselben** Wert (28,00 → 28,50 °C)
- Record 04–09 stehen alle auf 25,00 °C
- `0xa4` = `0xff` — als `temp_half40` der Maximalwert 87,5 °C, sieht nach
  „Sensor nicht belegt" aus

## Felder zuordnen — das Verfahren

1. **Logs sammeln.** Mehrere Sicherungen über den Tag, am besten bei stark
   unterschiedlichen Betriebszuständen (WW-Bereitung, Heizbetrieb, Stillstand).

2. **Displaywerte notieren.** Zu mindestens zwei Zeitpunkten am FTC4 die
   Istwerte ablesen und mit Uhrzeit aufschreiben. Ohne diese Referenz geht es
   nicht — der Decoder kann Zahlen lesen, aber nicht raten, was sie bedeuten.

3. **Zeitreihe erzeugen:**

   ```bash
   python3 -m src.log_decoder data/logs --csv zeitreihe.csv
   ```

   In der Tabelle sieht man je Spalte, welches Feld sich wann wie bewegt.
   Die Spalte, die dem abgelesenen Vorlauf folgt, ist der Vorlauf.

4. **Zwei Logs direkt vergleichen:**

   ```bash
   python3 -m src.log_decoder data/logs/223619.LOG data/logs/000047.LOG --diff
   ```

   Zeigt nur die Änderungen — inklusive der Bytes, die **kein** Feld abdeckt.
   Ändert sich dort etwas, fehlt im Layout ein Feld.

5. **Namen vergeben**, sobald ein Feld sicher ist — ohne Codeänderung:

   ```json
   { "soll06": "WW-Solltemp", "rec03_wert": "Vorlauf Ist" }
   ```

   ```bash
   python3 -m src.log_decoder data/logs --names namen.json
   ```

6. Ist ein Feld dauerhaft bestätigt, das `label` in `LOG_LAYOUT`
   (`src/log_decoder.py`) setzen.

## Offene Punkte

- Byte `0x05` = `0x12` (18): Formatversion? Anzahl Datensätze? Unbekannt.
- Der Block `0x06`–`0x4d` ist in beiden Dateien identisch — Einstellungen, die
  sich in den 84 Minuten nicht geändert haben, oder Struktur ohne Nutzdaten.
- Ob Logs mit anderer Firmware dasselbe Layout haben, ist ungeprüft. Die
  Prüfsummen- und Zeitstempelprüfung schlägt in dem Fall an.
- Die Bedeutung der **übrigen** Felder. Elf sind über den HA-Abgleich
  bestätigt (siehe oben), drei sind plausibel, der Rest ist offen. Für die
  konstanten Felder braucht es einen Abzug aus einem Zeitraum, in dem sich der
  jeweilige Wert bewegt.
