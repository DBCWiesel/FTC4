# FTC4-Konfigurationsdateien (*.DAT)

Nachgewiesen an einem vollständigen SD-Karten-Abzug: **11 von 11 Dateien**
(`DHW`, `HOL`, `HT&CL`, `INIT`, `SCH_1`–`SCH_5`, `SER_1`, `SER_2`).

Der Dateiname ist übrigens `HT&CL.DAT`, nicht `HT_CL.DAT` — die
Projektunterlagen schreiben ihn falsch.

## Aufbau

512 Byte fest, wie die Logdateien.

| Bereich | Inhalt | Status |
|---------|--------|--------|
| `0x000` … | Folge von 3-Byte-Records `[TYP][HI][LO]` | **bestätigt, 11/11** |
| Rest | `0x00`-Padding | |
| `0x1ff` | Prüfsumme | **bestätigt, 11/11** |

### Prüfsumme

Dieselbe Regel wie bei den Logs:

```
sum(alle 512 Bytes) mod 256 == 0
```

### Record-Struktur

Die belegte Länge ist bei **jeder** der elf Dateien ein exaktes Vielfaches
von 3. Das ist der Beleg für die Record-Struktur:

| Datei | belegt | Records | Typen |
|-------|--------|---------|-------|
| DHW.DAT | 33 B | 11 | `01`, `03`×7, `04`, `0f`×2 |
| HOL.DAT | 27 B | 9 | `01`, `03`×6, `05`×2 |
| HT&CL.DAT | 48 B | 16 | `0f`×10, `02`×6 |
| INIT.DAT | 63 B | 21 | `01`×11, `08`×10 |
| SCH_1/2.DAT | 189 B | 63 | `06`×35, `07`×28 |
| SCH_3/4.DAT | 105 B | 35 | `06`×35 |
| SCH_5.DAT | 108 B | 36 | `06`×35, `02` |
| SER_1.DAT | 228 B | 76 | `0f`×49, `09`×13, `02`×2 |
| SER_2.DAT | 339 B | 113 | `08`×105, `03`×7, `0f` |

Damit lösen sich die „Byte-Patterns" aus den Projektunterlagen auf: `06 00 38`,
`06 78 c0`, `06 ff ff`, `0f 00 xx`, `bb bb` sind schlicht Records bzw. deren
Datenbytes.

## Gedeutete Typen

### `ff ff` als Daten = nicht belegter Eintrag

In allen Dateien konsistent. Der „Fehler-Marker `bb bb`" aus den Unterlagen ist
analog dazu einfach das Datenfeld eines Typ-`01`-Records in INIT.DAT.

### Typ `0x0f` = Einzelwert-Parameter

Die Nutzlast steckt allein im LO-Byte (HI ist 0).

**In HT&CL.DAT sind diese Werte Temperaturen in 0,5-°C-Schritten** — dort
stehen die drei am Gerät abgelesenen Referenzwerte:

| Record | Offset | Roh | Wert |
|--------|--------|-----|------|
| 2 | `0x006` | `0f 00 4c` | **38,0 °C** |
| 3 | `0x009` | `0f 00 64` | **50,0 °C** |
| 4 | `0x00c` | `0f 00 5a` | **45,0 °C** |
| 5 | `0x00f` | `0f 00 5a` | 45,0 °C |
| 6 | `0x012` | `0f 00 46` | 35,0 °C |
| 10 | `0x01e` | `0f 00 50` | 40,0 °C |
| 11 | `0x021` | `0f 00 6e` | 55,0 °C |
| 12 | `0x024` | `0f 00 6e` | 55,0 °C |

Damit ist die 0,5-°C-Hypothese aus den Projektunterlagen **an echten Daten
bestätigt** — und zugleich klar, wo die Werte wirklich stehen.

**Aber:** Typ `0x0f` ist nicht auf Temperaturen festgelegt. `SER_1.DAT` enthält
unter demselben Typ Werte bis 230, die als Temperatur (115 °C) unsinnig wären.
Der Decoder gibt deshalb immer den Rohwert aus und die Temperaturlesart nur
als gekennzeichneten Zusatz, mit Plausibilitätsmarkierung.

### Typ `0x03` = BCD-Zahl, Typ `0x04` = BCD-Uhrzeit

Eine vierstellige BCD-Zahl mit einer Nachkommastelle: `03 01 50` sind die
Ziffern 0,1,5,0 und damit 15,0. Typ `04` ist dasselbe als Uhrzeit — `04 13 00`
ist 13:00.

Gegen die Werksvorgaben der Herstellertabelle geprüft: **acht unveränderte
Werte** in `DHW.DAT` und `HOL.DAT` kommen exakt heraus, zwölf von dreizehn
liegen im dokumentierten Bereich.

Ein Halbbyte über 9 ist keine gültige BCD-Ziffer — daran erkennt der Decoder,
dass ein Record diese Kodierung nicht verwendet.

### Typ `0x06` = Zeitfenster

Jede SCH-Datei enthält genau **35** Records dieses Typs = 7 Tage × 5 Fenster.
In `SCH_1.DAT` ist das Muster für alle sieben Tage identisch:

```
06 00 38    belegt
06 78 c0    belegt
06 ff ff    frei
06 ff ff    frei
06 ff ff    frei
```

Also zwei belegte Fenster pro Tag, jeden Tag gleich. Wie die zwei Datenbytes
Uhrzeit und Sollwert kodieren, ist **nicht** geklärt; ebenso wenig, welcher
Gruppenindex Montag ist.

## Bestätigung durch den Hersteller

`Teigi/RCSetting_EN.xls` aus dem SD_TOOL führt zu jeder Einstellung die
Byte-Nummer in der SD-Datei. **Jede dieser Nummern ist ein Vielfaches von
drei** — die 3-Byte-Recordstruktur ist damit unabhängig von der eigenen
Analyse belegt. Details und Verwendung: `docs/SD_TOOL.md`.

## Widerlegte Annahme

Die Projektunterlagen nahmen die Sollwerte in HT&CL.DAT auf **Offset `0x02`
und `0x04`** an. Das ist an den echten Daten widerlegt: dort stehen die
Nullbytes der Records 0 und 1.

```
Bytes 0x00-0x08:  0f 00 00 | 0f 00 02 | 0f 00 4c
                        ^^        ^^
                      0x02      0x05
```

Die Plausibilitätsprüfung des Decoders hat das selbst gemeldet (beide Werte
0,0 °C, außerhalb des erwarteten Fensters). `TemperatureDecoder.HT_CL_SETPOINTS`
zeigt jetzt auf die Record-Positionen `3 × Recordnummer + 2`.

## Was NICHT nachgewiesen ist

- **Welcher Record welche Einstellung ist.** Dass Record 3 in HT&CL.DAT 50 °C
  trägt, ist sicher; ob das der Warmwasser-Sollwert oder eine
  Vorlauf-Obergrenze ist, nicht.
- Die Bedeutung der Typen `01`, `02`, `03`, `04`, `05`, `07`, `08`, `09`.
- Das Kodierschema der Typ-`06`-Zeitfenster.
- Ob andere Firmware-Stände dasselbe Layout schreiben. Die Prüfsummen- und
  Ausrichtungsprüfung schlägt in dem Fall an.

## Felder zuordnen

Wie bei den Logs: am FTC4 einen Wert gezielt verstellen, Konfiguration neu auf
SD-Karte sichern, beide Stände vergleichen.

```bash
python3 -m src.dat_decoder data/HT\&CL.DAT --alle > vorher.txt
# Wert am Gerät ändern, neu sichern
python3 -m src.dat_decoder neu/HT\&CL.DAT --alle > nachher.txt
diff vorher.txt nachher.txt
```

Der Record, der sich geändert hat, ist der gesuchte.
