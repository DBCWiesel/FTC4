# Werte aus Home Assistant holen und Logfelder zuordnen

Die FTC4-Logs sagen, welche Zahl auf welchem Byte steht — aber nicht, was sie
bedeutet. Ein ESPHome-Modul an der Wärmepumpe liefert genau die fehlende
Referenz: benannte Sensoren mit Zeitstempel. Beide Reihen übereinandergelegt
ergeben die Zuordnung.

Wichtig: **derselbe Zeitraum**. Der vorliegende Abzug deckt 09.09.2026 22:36
bis 10.09.2026 06:26 ab. Der HA-Recorder hält standardmäßig 10 Tage vor, das
Fenster ist also noch abrufbar.

## Schritt 1 — Welche Sensoren hast du überhaupt?

**Entwicklerwerkzeuge → Vorlagen** (Developer Tools → Template), das hier
einfügen:

```jinja
{% for s in states.sensor
   if s.entity_id is search('(?i)(ecodan|ftc|waermepumpe|warmepumpe|heatpump|wp_)')
   and s.state not in ['unknown', 'unavailable'] %}
{{ s.entity_id }} = {{ s.state }} {{ s.attributes.unit_of_measurement | default('') }}
{% endfor %}
```

Das Suchmuster musst du an deine Namen anpassen. Wenn du den ESPHome-Gerätenamen
kennst, ist es einfacher — alle Entitäten eines Geräts:

```jinja
{% for e in device_entities('DEINE_GERAETE_ID') %}
{{ e }} = {{ states(e) }}
{% endfor %}
```

Die Geräte-ID steht in **Einstellungen → Geräte → dein ESPHome-Gerät** in der
URL. Interessant sind alle Temperatursensoren: Vorlauf, Rücklauf, Außen,
Speicher/WW, Raum, sowie die Soll-Werte.

## Schritt 2 — Zugriffstoken

Klick auf deinen Benutzernamen unten links → Reiter **Sicherheit** → ganz unten
**Langlebige Zugriffstoken** → *Token erstellen*. Der Token wird genau einmal
angezeigt.

## Schritt 3 — Verlauf abrufen

Von einem Rechner im selben Netz. Zeiten in **UTC** — deutsche Sommerzeit ist
UTC+2, also 22:36 Ortszeit = 20:36 UTC:

```bash
HA=http://homeassistant.local:8123
TOKEN=eyJ...

curl -sS -H "Authorization: Bearer $TOKEN" \
  "$HA/api/history/period/2026-09-09T20:36:00+00:00?end_time=2026-09-10T04:26:00+00:00&minimal_response&no_attributes" \
  -o ha.json
```

Das holt **alle** Entitäten. Wenn das zu viel wird, gezielt filtern:

```bash
ENTITIES="sensor.wp_vorlauf,sensor.wp_ruecklauf,sensor.wp_aussentemperatur,sensor.wp_speicher"

curl -sS -H "Authorization: Bearer $TOKEN" \
  "$HA/api/history/period/2026-09-09T20:36:00+00:00?end_time=2026-09-10T04:26:00+00:00&filter_entity_id=$ENTITIES&minimal_response&no_attributes" \
  -o ha.json
```

`minimal_response` wird unterstützt — der Parser zieht die dort weggelassene
`entity_id` selbst nach.

### Alternative ohne Kommandozeile

**Verlauf** im Seitenmenü → Zeitraum und Entitäten wählen → Menü rechts oben →
*Daten herunterladen*. Ergibt eine CSV je Entität. Die musst du dann selbst in
das JSON-Format bringen — der `curl`-Weg ist weniger Arbeit.

### Alternative direkt auf dem HAOS

Über das Add-on **Terminal & SSH**, gegen die Recorder-Datenbank:

```bash
sqlite3 /config/home-assistant_v2.db <<'SQL'
.mode csv
.headers on
SELECT sm.entity_id,
       datetime(s.last_updated_ts, 'unixepoch') AS zeit_utc,
       s.state
FROM states s
JOIN states_meta sm ON sm.metadata_id = s.metadata_id
WHERE sm.entity_id LIKE 'sensor.wp_%'
  AND s.last_updated_ts BETWEEN strftime('%s','2026-09-09 20:36:00')
                            AND strftime('%s','2026-09-10 04:26:00')
ORDER BY sm.entity_id, s.last_updated_ts;
SQL
```

Das Schema mit `states_meta` gilt für neuere HA-Versionen; ältere haben
`entity_id` direkt in `states`.

## Schritt 4 — Zuordnen lassen

```bash
python3 -m src.ha_correlate ha.json data/logs --namen namen.json
```

Der Zeitversatz zwischen HA (UTC) und FTC4 (Ortszeit) wird automatisch
bestimmt — er probiert alle vollen Stunden durch und nimmt die mit den meisten
Treffern. Falls du ihn kennst: `--utc-offset 2`.

Die Ausgabe hat drei Teile:

**Zugeordnet** — Sensor und Logfeld laufen deckungsgleich. Die Güte `hoch`
heißt: das Feld bewegt sich und der Verlauf passt. `schwach (Reihe konstant)`
heißt: beide standen die ganze Zeit auf demselben Wert — das kann Zufall sein.

**Gleichlauf mit Versatz** — gleicher Verlauf, konstant daneben. Typisch für
Vorlauf gegen Rücklauf oder einen Fühler gegen den zugehörigen Sollwert. Der
angezeigte Versatz sagt dir, welche Größe du vor dir hast.

**Alle Felder mit den besten Kandidaten** — die vollständige Rangliste.

Die erzeugte `namen.json` steckst du direkt in den Log-Decoder:

```bash
python3 -m src.log_decoder data/logs --names namen.json
```

Ab da stehen deine Sensornamen statt `rec03_wert` im Report.

## Wenn nichts passt

- **Zeitversatz.** Häufigste Ursache. `--utc-offset auto` durchlaufen lassen
  und schauen, welchen Wert es meldet.
- **Zeitraum ohne Betrieb.** Ein Feld, das sich nie bewegt, lässt sich nicht
  zuordnen. Am besten ein Fenster wählen, in dem die Anlage arbeitet —
  Warmwasserbereitung oder Heizbetrieb.
- **Recorder-Ausschluss.** Steht der Sensor in `recorder: exclude`, gibt es
  keinen Verlauf. In `configuration.yaml` prüfen.
- **Toleranz.** Rechnet dein ESPHome in ganzen Grad, während das Log 0,01 °C
  auflöst, ist `--toleranz 0.6` realistischer.

## Und die Gegenrichtung

Wenn die Zuordnung steht, kannst du die Logs auch als Datenquelle nutzen: die
CSV aus `--csv` in ein Tabellenblatt oder über einen `command_line`-Sensor
zurück nach Home Assistant. Der Reiz daran ist, dass die FTC4 im Log Werte
mitschreibt, die über die ESPHome-Schnittstelle nicht unbedingt herauskommen.
