# Temperatur-Dekodierung FTC4

Stand der Hypothesen zur Temperaturkodierung in den 512-Byte-DAT-Dateien der
Mitsubishi-Ecodan-FTC4-Steuerung, und wie sie am Geraet verifiziert werden.

## 1. Kodierung: 0.5-Grad-Schritte

```
celsius = raw_value * 0.5
```

Belegt durch drei am Geraet abgelesene Referenzwerte:

| Rohwert (hex) | Rohwert (dez) | Temperatur |
|---------------|---------------|------------|
| `0x4c`        | 76            | 38.0 °C    |
| `0x64`        | 100           | 50.0 °C    |
| `0x5a`        | 90            | 45.0 °C    |

Diese Werte stehen in `TemperatureDecoder.KNOWN_VALUES` und werden bei jedem
Testlauf sowie bei jedem CLI-Aufruf geprüft.

**Status: bestätigt** für die drei Referenzwerte. Der darstellbare Bereich
eines vorzeichenlosen Bytes ist damit 0.0 – 127.5 °C, die Auflösung 0.5 °C.

Offen bleibt, ob einzelne Felder stattdessen 1 °C je Rohwert verwenden
(`decode_direct_celsius`) — für Werte ≥ 64 lassen sich beide Hypothesen an
einem einzelnen Messpunkt nicht unterscheiden, wenn der Anzeigewert doppelt so
groß wie plausibel ist.

## 2. Sollwert-Offsets in HT_CL.DAT

**Status: unbestätigte Hypothese.** Aus `docs/FORMAT.md` übernommen:

| Schlüssel          | Offset | Kodierung | Plausibilitätsfenster |
|--------------------|--------|-----------|-----------------------|
| `heating_setpoint` | `0x02` | `u8`      | 20 – 60 °C            |
| `cooling_setpoint` | `0x04` | `u8`      | 5 – 30 °C             |

Auch die Byte-Breite ist offen: die Formatanalyse liest an `0x02` teilweise
16 Bit little-endian (`data[2:4]`). Solange das High-Byte `0x00` ist, liefern
`u8` und `u16le` identische Werte — der Unterschied fällt erst auf, wenn das
Folgebyte belegt ist. `extract_setpoints_detailed()` gibt deshalb zu jedem
Sollwert unter `alternatives` die anderen Lesarten mit aus.

Jeder dekodierte Wert trägt ein `plausible`-Flag. Ein `False` bedeutet in
aller Regel: Offset oder Byte-Breite der Hypothese stimmen nicht.

## 3. Verifikation am Gerät

Verfahren, um einen Offset von "Hypothese" auf "bestätigt" zu heben:

1. Am FTC4-Bedienteil einen **markanten** Sollwert einstellen — einen, der im
   Rest der Datei unwahrscheinlich ist, z. B. 41.5 °C (Rohwert `0x53`, 83).
2. Konfiguration auf SD-Karte sichern, `HT_CL.DAT` nach `data/` kopieren.
3. Scannen:

   ```bash
   python3 -m src.temperature_decoder data/HT_CL.DAT
   ```

   oder gezielt:

   ```python
   from src.temperature_decoder import TemperatureDecoder

   data = Path("data/HT_CL.DAT").read_bytes()
   for c in TemperatureDecoder.find_temperature_candidates(data, valid_range=(41.0, 42.0)):
       print(c.as_dict())
   ```

4. Den Sollwert um einen bekannten Betrag ändern (z. B. auf 42.0 °C), Schritt 2–3
   wiederholen und die Offsets vergleichen. Der Offset, der sich **genau um 1
   Rohwert-Schritt** geändert hat, ist der gesuchte — nur die Kombination aus
   beiden Läufen schließt Zufallstreffer aus.
5. Offset eintragen und Konfidenz hochstufen:

   ```python
   specs = TemperatureDecoder.with_offsets(heating_setpoint=0x06)
   TemperatureDecoder.extract_setpoints(data, specs)
   ```

   Bei dauerhafter Bestätigung `HT_CL_SETPOINTS` im Modul anpassen und
   `confidence="confirmed"` setzen.
6. `data/HT_CL.DAT` liegen lassen — die Tests der Klasse
   `TestAgainstRealDeviceData` laufen dann automatisch mit statt zu skippen.

## 4. Rückschreiben auf das Gerät

`encode_0_5_degree_steps()` und `encode_to_bytes()` erzeugen die Rohbytes zu
einer Temperatur. Der Roundtrip ist für alle 256 Byte-Werte getestet.

Vor dem Zurückschreiben auf die SD-Karte gilt:

- Nur Offsets beschreiben, deren Bedeutung nach Abschnitt 3 **bestätigt** ist.
- Original der DAT-Datei sichern.
- Ob die FTC4 eine Prüfsumme über die Datei bildet, ist **nicht untersucht**.
  Ein geänderter Wert ohne passende Prüfsumme kann von der Steuerung verworfen
  werden — oder Schlimmeres. Erst am Gerät verifizieren, dann automatisieren.

## 5. Offene Fragen

- Prüfsumme/CRC über die 512 Byte? (nicht untersucht)
- Vorzeichenbehaftete Felder für Außentemperatur/Offsets? `s8`/`s16le` sind im
  Decoder vorbereitet, aber in HT_CL.DAT noch nicht nachgewiesen.
- DHW.DAT: Sollwert-Format unklar (evtl. Prozent statt Temperatur).
- Ob 0.5-Grad-Schritte auch außerhalb von HT_CL.DAT gelten, ist offen.
