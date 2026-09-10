#!/usr/bin/env bash
#
# Baut aus dem Hersteller-Archiv des Ecodan SD_TOOL eine portable Fassung.
#
#   ./build.sh SDCardToolv21.zip [Zielordner]
#
# Ergebnis: <Zielordner>/SDCardTool-Portable/ und ein ZIP daneben.
#
# Die Herstellerdateien bleiben unveraendert; dazu kommen nur der Starter,
# ATW.exe.config und die Ordnerstruktur. Sie gehoeren nicht ins Repository
# und werden deshalb hier zur Bauzeit aus dem Archiv geholt.

set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="SDCardTool-Portable"

fehler() { printf 'Fehler: %s\n' "$1" >&2; exit 1; }

[ $# -ge 1 ] || fehler "Aufruf: $0 <Hersteller-ZIP oder -Ordner> [Zielordner]"

QUELLE="$1"
ZIEL="${2:-$PWD}"
[ -e "$QUELLE" ] || fehler "\"$QUELLE\" gibt es nicht."

command -v zip >/dev/null || fehler "\"zip\" fehlt (apt install zip)."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Herstellerdateien bereitstellen ---------------------------------
if [ -d "$QUELLE" ]; then
    cp -a "$QUELLE" "$TMP/entpackt"
else
    command -v unzip >/dev/null || fehler "\"unzip\" fehlt (apt install unzip)."
    mkdir -p "$TMP/entpackt"
    unzip -q -o "$QUELLE" -d "$TMP/entpackt"
fi

# ATW.exe suchen - das Archiv bringt einen Unterordner mit Klammern im
# Namen mit, dessen genaue Schreibweise sich zwischen Fassungen aendert.
EXE="$(find "$TMP/entpackt" -iname 'ATW.exe' -type f -print -quit)"
[ -n "$EXE" ] || fehler "ATW.exe steckt nicht in \"$QUELLE\"."
HERSTELLER="$(dirname "$EXE")"

[ -d "$HERSTELLER/Teigi" ] || fehler "Der Ordner \"Teigi\" fehlt neben ATW.exe."

# --- Paket zusammenstellen -------------------------------------------
PAKET="$TMP/$NAME"
mkdir -p "$PAKET/App" "$PAKET/Data/Config" "$PAKET/Data/Log"

cp -a "$HERSTELLER/." "$PAKET/App/"

# Alle Textdateien mit CRLF - eine .cmd mit blossen Zeilenvorschueben
# stolpert unter cmd.exe ueber ihre eigenen Sprungmarken.
crlf() { sed 's/$/\r/' "$1" > "$2"; }
crlf "$HIER/SD-Card-Tool.cmd"           "$PAKET/SD-Card-Tool.cmd"
crlf "$HIER/LIESMICH.txt"               "$PAKET/LIESMICH.txt"
crlf "$HIER/portable-sd-card-tool.txt"  "$PAKET/Data/portable-sd-card-tool.txt"

# Der Hersteller liefert keine ATW.exe.config mit; sollte sich das je
# aendern, gewinnt unsere - ohne sie bleibt das Werkzeug auf Windows 10
# und 11 mit der Bitte um .NET 3.5 stehen.
crlf "$HIER/ATW.exe.config"             "$PAKET/App/ATW.exe.config"

# LIESMICH mit BOM, damit der Windows-Editor die Umlaute richtig zeigt.
printf '\xef\xbb\xbf' | cat - "$PAKET/LIESMICH.txt" > "$PAKET/LIESMICH.tmp"
mv "$PAKET/LIESMICH.tmp" "$PAKET/LIESMICH.txt"

# Data\ muss auch im ZIP als Ordner ankommen, sonst legt der Starter sie
# zwar an, das Paket sieht aber unfertig aus.
: > "$PAKET/Data/Config/.ordner-behalten"
: > "$PAKET/Data/Log/.ordner-behalten"

# --- gegenpruefen -----------------------------------------------------
for pflicht in App/ATW.exe App/ATW.exe.config App/Teigi SD-Card-Tool.cmd \
               LIESMICH.txt Data/portable-sd-card-tool.txt; do
    [ -e "$PAKET/$pflicht" ] || fehler "Im Paket fehlt \"$pflicht\"."
done

if ! cmp -s "$EXE" "$PAKET/App/ATW.exe"; then
    fehler "ATW.exe im Paket weicht vom Original ab."
fi

# --- ausliefern -------------------------------------------------------
mkdir -p "$ZIEL"
rm -rf "${ZIEL:?}/$NAME"
cp -a "$PAKET" "$ZIEL/$NAME"
( cd "$TMP" && zip -q -r -X "$ZIEL/$NAME.zip" "$NAME" )

printf 'Fertig:\n  %s\n  %s\n' "$ZIEL/$NAME" "$ZIEL/$NAME.zip"
printf 'Herstellerdateien aus: %s\n' "$HERSTELLER"
