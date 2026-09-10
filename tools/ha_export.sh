#!/usr/bin/env sh
# Exportiert den Sensorverlauf eines Geraets aus Home Assistant.
#
# Gedacht fuer das Add-on "Terminal & SSH" auf Home Assistant OS. Dort ist
# $SUPERVISOR_TOKEN gesetzt und die Kern-API unter http://supervisor/core/api
# erreichbar -- ein eigenes Zugriffstoken ist dann nicht noetig.
#
# Von aussen stattdessen HA_URL und HA_TOKEN setzen:
#     HA_URL=http://homeassistant.local:8123 HA_TOKEN=eyJ... ./ha_export.sh ...
#
# Aufruf:
#     ./ha_export.sh <geraete-id> <start-utc> <ende-utc> [zielverzeichnis]
#
# Beispiel (Zeiten in UTC; deutsche Sommerzeit ist UTC+2):
#     ./ha_export.sh 889ea786b19e212d178f931f90b8048b \
#         2026-09-09T20:36:00+00:00 2026-09-10T04:26:00+00:00 /config/ftc4

set -eu

DEVICE_ID="${1:-}"
START="${2:-}"
END="${3:-}"
OUTDIR="${4:-/config/ftc4}"

if [ -z "$DEVICE_ID" ] || [ -z "$START" ] || [ -z "$END" ]; then
    echo "Aufruf: $0 <geraete-id> <start-utc> <ende-utc> [zielverzeichnis]" >&2
    echo "Beispiel: $0 889ea... 2026-09-09T20:36:00+00:00 2026-09-10T04:26:00+00:00" >&2
    exit 2
fi

# --- Zugang bestimmen -------------------------------------------------------
if [ -n "${HA_TOKEN:-}" ]; then
    API="${HA_URL:-http://homeassistant.local:8123}/api"
    TOKEN="$HA_TOKEN"
    echo "Zugang: HA_TOKEN gegen $API"
elif [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    API="http://supervisor/core/api"
    TOKEN="$SUPERVISOR_TOKEN"
    echo "Zugang: SUPERVISOR_TOKEN gegen $API"
else
    echo "Weder SUPERVISOR_TOKEN noch HA_TOKEN gesetzt." >&2
    echo "Im Add-on 'Terminal & SSH' sollte SUPERVISOR_TOKEN da sein -- sonst" >&2
    echo "ein langlebiges Zugriffstoken anlegen und HA_TOKEN/HA_URL setzen." >&2
    exit 3
fi

# --fail-with-body gibt es erst ab curl 7.76; sonst auf --fail zurueckfallen.
if curl --help all 2>/dev/null | grep -q -- "--fail-with-body"; then
    FAILOPT="--fail-with-body"
else
    FAILOPT="--fail"
fi

api() {
    # $1 = Pfad ab /api, weitere Argumente werden an curl durchgereicht
    path="$1"
    shift
    curl -sS $FAILOPT -H "Authorization: Bearer $TOKEN" \
         -H "Content-Type: application/json" "$API$path" "$@"
}

# wc -l zaehlt die letzte Zeile nicht mit, wenn der Zeilenumbruch fehlt --
# und genau so liefert die Template-API ihre Ausgabe.
zeilen() {
    grep -c '' "$1" 2>/dev/null || echo 0
}

mkdir -p "$OUTDIR"

# --- 1. Erreichbarkeit ------------------------------------------------------
echo "--- Verbindung pruefen ---"
api "/" || { echo "Kern-API nicht erreichbar." >&2; exit 4; }
echo

# --- 2. Entitaeten des Geraets ---------------------------------------------
echo "--- Entitaeten des Geraets $DEVICE_ID ---"
TEMPLATE='{"template": "{% for e in device_entities(\"'"$DEVICE_ID"'\") %}{{ e }}\n{% endfor %}"}'
api "/template" -X POST -d "$TEMPLATE" | sed '/^$/d' > "$OUTDIR/entities.txt"

COUNT=$(zeilen "$OUTDIR/entities.txt")
if [ "$COUNT" -eq 0 ]; then
    echo "Keine Entitaeten gefunden. Stimmt die Geraete-ID?" >&2
    exit 5
fi
echo "$COUNT Entitaeten -> $OUTDIR/entities.txt"

# Nur Sensoren mit Zahlenwert sind fuer die Zuordnung brauchbar.
grep '^sensor\.' "$OUTDIR/entities.txt" > "$OUTDIR/sensors.txt" || true
SENSORS=$(zeilen "$OUTDIR/sensors.txt")
echo "$SENSORS davon sind Sensoren"
echo

# --- 3. Momentaufnahme mit Einheiten ---------------------------------------
echo "--- Aktuelle Werte ---"
SNAP='{"template": "{% for e in device_entities(\"'"$DEVICE_ID"'\") if e.startswith(\"sensor.\") %}{{ e }} = {{ states(e) }} {{ state_attr(e, \"unit_of_measurement\") or \"\" }}\n{% endfor %}"}'
api "/template" -X POST -d "$SNAP" | sed '/^$/d' | tee "$OUTDIR/momentaufnahme.txt"
echo

# --- 4. Verlauf ------------------------------------------------------------
echo "--- Verlauf $START bis $END ---"
FILTER=$(tr '\n' ',' < "$OUTDIR/sensors.txt" | sed 's/,$//')

# -G plus --data-urlencode: end_time enthaelt ein '+', das in einer Query
# sonst als Leerzeichen ankommt.
curl -sS $FAILOPT -G "$API/history/period/$START" \
     -H "Authorization: Bearer $TOKEN" \
     --data-urlencode "end_time=$END" \
     --data-urlencode "filter_entity_id=$FILTER" \
     --data "minimal_response" \
     --data "no_attributes" \
     -o "$OUTDIR/ha_verlauf.json"

SIZE=$(wc -c < "$OUTDIR/ha_verlauf.json" | tr -d ' ')
ENTITAETEN_IM_VERLAUF=$(tr ',' '\n' < "$OUTDIR/ha_verlauf.json" \
    | grep -o '"entity_id": *"[^"]*"' | sort -u | wc -l | tr -d ' ')
echo "Verlauf geschrieben: $OUTDIR/ha_verlauf.json ($SIZE Byte)"
echo "Entitaeten mit Daten im Verlauf: $ENTITAETEN_IM_VERLAUF von $SENSORS"

if [ "$SIZE" -lt 100 ] || [ "$ENTITAETEN_IM_VERLAUF" -eq 0 ]; then
    echo
    echo "ACHTUNG: Die Datei ist fast leer. Moegliche Ursachen:" >&2
    echo "  - Zeitraum liegt ausserhalb der Recorder-Aufbewahrung (Standard 10 Tage)" >&2
    echo "  - Zeiten nicht in UTC angegeben" >&2
    echo "  - Sensoren stehen in recorder: exclude" >&2
fi

echo
echo "Fertig. Diese Datei brauchst du:"
echo "  $OUTDIR/ha_verlauf.json"
