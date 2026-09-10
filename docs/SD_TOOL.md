# Was das Hersteller-Werkzeug preisgibt

Das offizielle **SD_TOOL** (Ecodan SD tool, Ver. 8.02) bringt unter `Teigi/`
seine eigenen Definitionstabellen mit. Sie beantworten zwei Fragen, die aus
den Binärdateien allein nicht zu klären waren.

Die Dateien liegen **nicht** im Repository — sie gehören zum Werkzeug. Die
Module lesen sie zur Laufzeit aus der eigenen Installation.

## 1. Die Spaltenbelegung der Logdateien

`Teigi/ItemSetting_EN.xls` benennt die Spalten des CSV-Exports, `Graph_Info1.txt`
ordnet den Temperaturkurven dieselben Spaltennummern zu, und `OPELOG_sample.csv`
zeigt einen fertig dekodierten Tag.

Die Verankerung auf die Byte-Offsets ergibt sich aus drei Fühlern, die schon
über den Home-Assistant-Abgleich feststanden (Außen, Vorlauf, Rücklauf).
Danach passt der ganze Block lückenlos:

| Offset | Spalte | Bezeichnung |
|--------|--------|-------------|
| `0x05a` | Data78 | Raumtemperatur (TH1a) |
| `0x05c` | Data79 | Raumtemperatur Zone 2 (TH1b) |
| `0x061` | Data82 | Kältemittel flüssig (TH2) |
| `0x064` | Data84 | Außentemperatur (TH7) |
| `0x065` | Data85 | Vorlauftemperatur (THW1) |
| `0x068` | Data87 | Rücklauftemperatur (THW2) |
| `0x06b` | Data89 | Warmwasser-Speicher (THW5) |
| `0x06e` | Data91 | Vorlauf Zone 1 (THW6) |
| `0x071` | Data93 | Rücklauf Zone 1 (THW7) |
| `0x074` | Data95 | Vorlauf Zone 2 (THW8) |
| `0x077` | Data97 | Rücklauf Zone 2 (THW9) |
| `0x07a` | Data99 | Vorlauf Kessel (THWB1) |
| `0x07d` | Data101 | Rücklauf Kessel (THWB2) |
| `0x080`–`0x086` | Data103–109 | Digitaleingänge IN1, IN6, IN2, IN3, IN7, IN4, IN5 |

Ein starkes Indiz für die Verankerung: im Beispiel-Log des Herstellers stehen
die sechs Records Zone1/Zone2/Kessel auf `25, 11, 25, 11, 25, 11, 25, 11,
25, 2, 25, 2` — exakt dasselbe Muster wie im hiesigen Abzug.

**Wichtig:** Der Hersteller benennt längst nicht alle Spalten. `Data72`–`Data77`,
`Data80`, `Data81`, `Data83`, `Data86`, `Data88`, `Data90` und die Folgebytes
der Records führt er selbst nur als `DataNN`. Diese Felder tragen deshalb auch
hier keinen Namen.

## 2. Die Byte-Nummern der Einstellungsdateien

`Teigi/RCSetting_EN.xls` (und die Sprachvarianten) beschreiben jede Einstellung
mit Titel, Einheit, Wertebereich, Werksvorgabe — und in der letzten Spalte
`SD設定ﾌｧｲﾙBYTENo` die **Byte-Nummer in der SD-Einstellungsdatei**.

**Jede dieser Byte-Nummern ist ein Vielfaches von drei.** Das bestätigt die
3-Byte-Recordstruktur unabhängig von der eigenen Analyse. Ein Test hält das
fest (`TestAgainstRealTable`).

Die Registerkarten entsprechen den Dateien:

| Tab | Datei |
|-----|-------|
| 1 DHW | `DHW.DAT` |
| 2 Heating / Cooling | `HT&CL.DAT` |
| 3 Holiday | `HOL.DAT` |
| 4 Schedule timer | `SCH_*.DAT` |
| 5 Initial settings | `INIT.DAT` |
| 6 Service menu | `SER_1.DAT`, `SER_2.DAT` |

### Was die Tabelle nicht sagt

Die **Kodierung** der Werte fehlt. Sie lässt sich aber aus dem dokumentierten
Wertebereich erschließen: von den möglichen Lesarten bleiben die übrig, die im
erlaubten Bereich landen. Genau das macht `src/rc_settings.py`.

So kam die BCD-Kodierung heraus: Typ-`03`-Records sind vierstellige BCD-Zahlen
mit einer Nachkommastelle. `03 01 50` sind die Ziffern 0,1,5,0 und damit 15,0.
Gegen die Werksvorgaben geprüft — acht unveränderte Werte in `DHW.DAT` und
`HOL.DAT` kommen exakt heraus. Typ `04` ist dasselbe als Uhrzeit: `04 13 00`
ist 13:00.

## Verwendung

```bash
python3 -m src.rc_settings /pfad/SD_TOOL/Teigi/RCSetting_EN.xls data
```

Braucht `xlrd` (`pip install xlrd`). Die Ausgabe nennt je Eintrag den Rohwert,
den erschlossenen Wert und den dokumentierten Bereich. Wo mehrere Lesarten
passen, führt sie alle auf, statt eine zu behaupten.

## Gegenprobe mit dem echten Export

Wer denselben Kartenabzug durch das SD_TOOL laufen lässt, bekommt zwei Dateien,
die als Referenz taugen:

**`OPELOG_*.csv`** — der dekodierte Betriebslog, 226 Spalten. Über 471
gemeinsame Zeitpunkte stimmen **alle 20 benannten Spalten exakt** mit dem
eigenen Decoder überein. Die fünf übrigen Spalten (`Daten81`, `83`, `86`, `88`,
`90`) gibt das Werkzeug **roh** aus — der Decoder tut das jetzt auch und führt
die Temperaturlesart nur als Anmerkung. `tests/test_vendor_reference.py` prüft
das automatisch, sobald die CSV unter `data/reference/opelog.csv` liegt.

Achtung beim Einlesen: der Export nutzt deutsche Dezimalkommas **und** Komma
als Trennzeichen. Jede Kommazahl wird dadurch in zwei Felder zerrissen —
`22,5` wird zu `22` und `5`, und die Firmware-Version `12,01` gleich mit.
`repair_row()` setzt das zusammen.

**`AtwOutput_*.xls`** — die Einstellungen mit Werkseinstellung *und*
Anlageneinstellung. Das ist die Quelle, an der die Kodierungen oben
festgemacht wurden.

## Offene Punkte

- Die Kodierung der Typ-`02`-Records (Heizkurven-Stützpunkte) ist nicht
  geklärt. Ein Record trägt dort Außen- und Vorlauftemperatur zugleich, aber
  keine der geprüften Lesarten trifft die Werte, die das Werkzeug ausgibt.
- Mehrere Einträge aus Tab 6 zeigen auf Offsets jenseits von `SER_1.DAT` —
  die Service-Einstellungen verteilen sich vermutlich über `SER_1` und `SER_2`.
- Mehrere `SER_1`-Einträge liefern unter keiner Lesart den Wert, den das
  Werkzeug ausgibt. Die Byte-Nummern für Tab 6 scheinen sich zwischen
  Firmware-Ständen verschoben zu haben.
