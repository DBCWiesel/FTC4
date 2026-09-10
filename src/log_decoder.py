"""Dekodierung der FTC4-Logdateien (*.LOG) von der SD-Karte in lesbaren Klartext.

Die FTC4 schreibt beim Sichern auf SD-Karte 512-Byte-Logs, deren Dateiname die
Uhrzeit traegt (``223619.LOG`` = 22:36:19). Struktur, soweit an echten Dateien
nachgewiesen:

===============  ===========================================================
Offset           Inhalt
===============  ===========================================================
0x00 - 0x04      Zeitstempel, je ein Byte binaer: YY MM DD HH MM
0x05             konstant 0x12 (Bedeutung unbekannt)
0x06 - 0x4d      Konfigurationsflags, ueberwiegend 0x00
0x4e - 0x5f      9 Werte, LE16 / 100 -> Grad Celsius  (Sollwerte)
0x60 - 0x64      Einzelwerte, gemischte Kodierung
0x65 - 0x7f      9 Records a 3 Byte: LE16/100 Grad + 1 Statusbyte
0x80 - 0x1fe     Einzelparameter, ueberwiegend 0x00
0x1ff            Pruefsumme: Summe aller 512 Bytes = 0 (mod 256)
===============  ===========================================================

Zwei Temperaturkodierungen kommen vor -- **beide anders als in den DAT-Dateien**:

``temp_centi``   LE16 / 100      -> 0.01 Grad Aufloesung, z.B. 0x0b22 = 28.50 C
``temp_half40``  Byte / 2 - 40   -> 0.5 Grad, Bereich -40.0 .. +87.5 C

Nachgewiesen ist die Kodierung, **nicht die Bedeutung der einzelnen Felder**.
Welcher Offset welcher Sensor ist, laesst sich nur durch Korrelation mit dem
FTC4-Display ermitteln -- siehe docs/LOG_FORMAT.md und ``--diff``/``--csv``.
Eigene Zuordnungen kommen ueber eine Namensdatei dazu (``--names``), ohne dass
am Code etwas geaendert werden muss.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.temperature_decoder import TemperatureDecodeError, TemperatureDecoder

__all__ = [
    "LOG_SIZE",
    "LogDecodeError",
    "Encoding",
    "FieldSpec",
    "DecodedField",
    "FTC4Log",
    "LOG_LAYOUT",
]

#: Groesse einer FTC4-Logdatei in Bytes.
LOG_SIZE = 512

#: Inhalt der von der FTC4 geschriebenen Kartentestdatei TEST.LOG.
TEST_LOG_PATTERN = bytes(range(16))


class LogDecodeError(ValueError):
    """Fehler beim Einlesen oder Dekodieren einer Logdatei."""


@dataclass(frozen=True)
class Encoding:
    """Wie ein Rohwert an einem Offset in einen physikalischen Wert umgerechnet wird."""

    name: str
    raw_encoding: str  # "u8" / "u16le" / ... wie in TemperatureDecoder
    scale: float = 1.0
    bias: float = 0.0
    unit: str = ""
    #: Fenster, in dem der dekodierte Wert physikalisch sinnvoll ist.
    plausible_range: Optional[Tuple[float, float]] = None

    @property
    def width(self) -> int:
        return TemperatureDecoder._encoding_width(self.raw_encoding)


#: LE16 in Hundertstel Grad -- die Hauptkodierung der Logdateien.
TEMP_CENTI = Encoding("temp_centi", "u16le", 0.01, 0.0, "C", (-40.0, 120.0))
#: Einzelbyte, 0.5-Grad-Raster mit Nullpunkt -40 C. Deckt Minusgrade ab.
TEMP_HALF40 = Encoding("temp_half40", "u8", 0.5, -40.0, "C", (-40.0, 87.5))
#: Rohbyte ohne Umrechnung -- fuer Flags, Modi und Zaehler.
RAW_U8 = Encoding("u8", "u8", 1.0, 0.0, "")
#: Roher 16-Bit-Wert ohne Umrechnung.
RAW_U16 = Encoding("u16le", "u16le", 1.0, 0.0, "")

ENCODINGS: Mapping[str, Encoding] = {
    enc.name: enc for enc in (TEMP_CENTI, TEMP_HALF40, RAW_U8, RAW_U16)
}


@dataclass(frozen=True)
class FieldSpec:
    """Ein Feld im Log: wo es steht, wie es kodiert ist, wie es heisst."""

    key: str
    offset: int
    encoding: Encoding
    group: str = "sonstige"
    label: str = ""
    note: str = ""

    @property
    def width(self) -> int:
        return self.encoding.width

    def display_label(self, names: Optional[Mapping[str, str]] = None) -> str:
        """Benutzerdefinierter Name, sonst der eingebaute, sonst der Schluessel."""
        if names and self.key in names:
            return names[self.key]
        return self.label or self.key


@dataclass(frozen=True)
class DecodedField:
    """Ein dekodiertes Feld mit Rohwert, physikalischem Wert und Nebenlesarten."""

    spec: FieldSpec
    raw: int
    value: float
    hex: str
    plausible: bool
    #: Der gleiche Rohwert unter der jeweils anderen Temperaturkodierung.
    alternatives: Mapping[str, float] = field(default_factory=dict)

    @property
    def unit(self) -> str:
        return self.spec.encoding.unit

    def format_value(self) -> str:
        if self.unit == "C":
            return f"{self.value:.2f} C" if self.spec.encoding is TEMP_CENTI else f"{self.value:.1f} C"
        return f"{self.value:g}"

    def as_dict(self) -> Dict[str, object]:
        return {
            "key": self.spec.key,
            "label": self.spec.label,
            "group": self.spec.group,
            "offset": f"0x{self.spec.offset:03x}",
            "encoding": self.spec.encoding.name,
            "raw": self.raw,
            "hex": self.hex,
            "value": self.value,
            "unit": self.unit,
            "plausible": self.plausible,
        }


def _sensor_records() -> List[FieldSpec]:
    """Die 9 Records ab 0x65: je LE16-Temperatur plus ein Statusbyte.

    Bei den ersten Records bewegt sich das Statusbyte nachweislich im
    0.5-Grad-Raster zum LE16-Wert; bei den hinteren steht dort ein kleiner
    konstanter Code (2 bzw. 11), der als Temperatur keinen Sinn ergibt. Das
    Byte wird deshalb roh ausgegeben und zusaetzlich als Temperatur angeboten.
    """
    specs: List[FieldSpec] = []
    for index, offset in enumerate(range(0x65, 0x80, 3), start=1):
        specs.append(
            FieldSpec(
                key=f"rec{index:02d}_wert",
                offset=offset,
                encoding=TEMP_CENTI,
                group="messwerte",
                label=f"Record {index:02d} Wert",
            )
        )
        specs.append(
            FieldSpec(
                key=f"rec{index:02d}_status",
                offset=offset + 2,
                encoding=RAW_U8,
                group="messwerte",
                label=f"Record {index:02d} Statusbyte",
                note="als Temperatur: Byte/2-40",
            )
        )
    return specs


def _build_layout() -> Tuple[FieldSpec, ...]:
    """Das nachgewiesene Feldlayout einer FTC4-Logdatei.

    Die Schluessel sind bewusst neutral (``soll01``, ``rec03_wert``): welcher
    Offset welcher Sensor ist, ist nicht nachgewiesen und wird hier nicht
    behauptet. Eigene Namen kommen ueber ``--names`` dazu.
    """
    specs: List[FieldSpec] = []

    # 0x4e-0x5f: neun LE16-Werte, in den vorliegenden Dateien ueberwiegend konstant.
    for index, offset in enumerate(range(0x4E, 0x60, 2), start=1):
        specs.append(
            FieldSpec(
                key=f"soll{index:02d}",
                offset=offset,
                encoding=TEMP_CENTI,
                group="sollwerte",
                label=f"Sollwert {index:02d}",
            )
        )

    # 0x60-0x64: gemischte Einzelwerte vor dem Recordblock.
    specs += [
        FieldSpec("vor01", 0x60, TEMP_HALF40, "messwerte", "Einzelwert 0x60"),
        FieldSpec("vor02_wert", 0x61, TEMP_CENTI, "messwerte", "Einzelwert 0x61"),
        FieldSpec("vor02_status", 0x63, RAW_U8, "messwerte", "Statusbyte 0x63",
                  note="als Temperatur: Byte/2-40"),
        FieldSpec("vor03", 0x64, TEMP_HALF40, "messwerte", "Einzelwert 0x64",
                  note="in beiden Referenzdateien konstant 0x70"),
    ]

    specs += _sensor_records()

    # Einzelbytes ausserhalb der Bloecke, die in den Referenzdateien belegt sind.
    for offset in (0x06, 0x0A, 0x0C, 0x0E, 0x13, 0x14, 0x1A, 0x1C, 0x3D,
                   0x87, 0x88, 0x8E, 0x8F, 0xA3, 0xA4, 0xAC):
        specs.append(
            FieldSpec(
                key=f"par{offset:03x}",
                offset=offset,
                encoding=RAW_U8,
                group="parameter",
                label=f"Parameter 0x{offset:03x}",
            )
        )

    return tuple(specs)


#: Feldlayout einer FTC4-Logdatei.
LOG_LAYOUT: Tuple[FieldSpec, ...] = _build_layout()

#: Offsets, die von :data:`LOG_LAYOUT`, Header oder Pruefsumme abgedeckt sind.
_MAPPED_OFFSETS = (
    {0, 1, 2, 3, 4, 5, LOG_SIZE - 1}
    | {o for spec in LOG_LAYOUT for o in range(spec.offset, spec.offset + spec.width)}
)


@dataclass
class FTC4Log:
    """Eine eingelesene FTC4-Logdatei."""

    data: bytes
    filename: str = ""

    # -- Konstruktion ---------------------------------------------------

    @classmethod
    def from_path(cls, path: "str | Path") -> "FTC4Log":
        """Liest eine .LOG-Datei ein.

        Raises:
            LogDecodeError: wenn die Datei nicht 512 Byte gross ist.
        """
        file_path = Path(path)
        data = file_path.read_bytes()
        if len(data) != LOG_SIZE:
            if data == TEST_LOG_PATTERN:
                raise LogDecodeError(
                    f"{file_path.name} ist die 16-Byte-Kartentestdatei der FTC4 "
                    f"(Byte-Muster 00..0f), keine Logdatei."
                )
            raise LogDecodeError(
                f"{file_path.name}: {len(data)} Byte, erwartet werden {LOG_SIZE}."
            )
        return cls(data=data, filename=file_path.name)

    @staticmethod
    def is_card_test_file(path: "str | Path") -> bool:
        """True fuer TEST.LOG -- den Schreibtest der FTC4, ohne Nutzdaten."""
        try:
            return Path(path).read_bytes() == TEST_LOG_PATTERN
        except OSError:
            return False

    # -- Kopfdaten ------------------------------------------------------

    @property
    def timestamp(self) -> Optional[dt.datetime]:
        """Zeitstempel aus Offset 0x00-0x04 (YY MM DD HH MM, binaer).

        ``None``, wenn die Bytes kein gueltiges Datum ergeben. Sekunden stehen
        nicht im Header -- die liefert der Dateiname (``223619.LOG``).
        """
        year, month, day, hour, minute = self.data[0:5]
        try:
            return dt.datetime(2000 + year, month, day, hour, minute)
        except ValueError:
            return None

    @property
    def timestamp_from_filename(self) -> Optional[dt.time]:
        """Uhrzeit aus dem Dateinamen, z.B. ``223619.LOG`` -> 22:36:19."""
        stem = Path(self.filename).stem
        if len(stem) != 6 or not stem.isdigit():
            return None
        try:
            return dt.time(int(stem[0:2]), int(stem[2:4]), int(stem[4:6]))
        except ValueError:
            return None

    @property
    def checksum_ok(self) -> bool:
        """True, wenn die Summe aller 512 Bytes 0 modulo 256 ergibt.

        An beiden Referenzdateien nachgewiesen. Schlaegt die Pruefung fehl, ist
        die Datei beschaedigt -- oder die Kopie unvollstaendig.
        """
        return sum(self.data) % 256 == 0

    @property
    def checksum_byte(self) -> int:
        return self.data[LOG_SIZE - 1]

    def expected_checksum(self) -> int:
        """Pruefsummenbyte, das zu den ersten 511 Bytes passen wuerde."""
        return (-sum(self.data[: LOG_SIZE - 1])) % 256

    # -- Felder ---------------------------------------------------------

    def decode_field(self, spec: FieldSpec) -> DecodedField:
        """Dekodiert ein einzelnes Feld."""
        enc = spec.encoding
        raw = TemperatureDecoder.read_raw(self.data, spec.offset, enc.raw_encoding)
        value = raw * enc.scale + enc.bias
        chunk = self.data[spec.offset : spec.offset + enc.width]

        plausible = True
        if enc.plausible_range is not None:
            low, high = enc.plausible_range
            plausible = low <= value <= high

        alternatives: Dict[str, float] = {}
        if enc is RAW_U8:
            alternatives["temp_half40"] = raw * TEMP_HALF40.scale + TEMP_HALF40.bias
        elif enc is TEMP_HALF40:
            alternatives["u8"] = float(raw)

        return DecodedField(
            spec=spec,
            raw=raw,
            value=value,
            hex=TemperatureDecoder.format_hex(chunk),
            plausible=plausible,
            alternatives=alternatives,
        )

    def decode_all(self, layout: Sequence[FieldSpec] = LOG_LAYOUT) -> List[DecodedField]:
        """Dekodiert alle Felder des Layouts."""
        return [self.decode_field(spec) for spec in layout]

    def unmapped_bytes(self) -> List[Tuple[int, int]]:
        """Belegte Bytes, die kein Feld des Layouts abdeckt.

        Diese Liste ist die Ehrlichkeitskontrolle des Decoders: was hier
        auftaucht, wird im Report nicht interpretiert, geht aber auch nicht
        verloren.
        """
        return [
            (offset, self.data[offset])
            for offset in range(LOG_SIZE)
            if self.data[offset] != 0 and offset not in _MAPPED_OFFSETS
        ]

    def as_dict(self, layout: Sequence[FieldSpec] = LOG_LAYOUT) -> Dict[str, object]:
        """JSON-taugliche Gesamtdarstellung."""
        stamp = self.timestamp
        return {
            "filename": self.filename,
            "timestamp": stamp.isoformat(sep=" ") if stamp else None,
            "time_from_filename": (
                self.timestamp_from_filename.isoformat()
                if self.timestamp_from_filename
                else None
            ),
            "checksum_ok": self.checksum_ok,
            "fields": [f.as_dict() for f in self.decode_all(layout)],
            "unmapped": [{"offset": f"0x{o:03x}", "value": v} for o, v in self.unmapped_bytes()],
        }


# ----------------------------------------------------------------------
# Klartext-Ausgabe
# ----------------------------------------------------------------------

_GROUP_TITLES = {
    "sollwerte": "SOLLWERTE / EINSTELLUNGEN  (0x4e-0x5f, LE16/100)",
    "messwerte": "MESSWERTE  (0x60-0x7f)",
    "parameter": "WEITERE PARAMETER  (Einzelbytes, roh)",
    "sonstige": "SONSTIGE FELDER",
}


def render_text(
    log: FTC4Log,
    layout: Sequence[FieldSpec] = LOG_LAYOUT,
    names: Optional[Mapping[str, str]] = None,
) -> str:
    """Erzeugt den lesbaren Klartext-Report einer Logdatei."""
    out: List[str] = []
    add = out.append

    add("=" * 78)
    add(f"FTC4-LOG  {log.filename or '(ohne Namen)'}")
    add("=" * 78)

    stamp = log.timestamp
    add(f"Zeitstempel (Header 0x00-0x04) : "
        f"{stamp.strftime('%d.%m.%Y %H:%M') if stamp else 'ungueltig'}")
    from_name = log.timestamp_from_filename
    if from_name:
        add(f"Uhrzeit (Dateiname)            : {from_name.strftime('%H:%M:%S')}")
    if log.checksum_ok:
        add(f"Pruefsumme                     : OK (0x{log.checksum_byte:02x})")
    else:
        add(f"Pruefsumme                     : FEHLERHAFT -- Datei ist 0x{log.checksum_byte:02x}, "
            f"erwartet 0x{log.expected_checksum():02x}")
        add("                                 Die Datei ist beschaedigt oder unvollstaendig kopiert.")

    fields = log.decode_all(layout)
    for group in ("sollwerte", "messwerte", "parameter", "sonstige"):
        in_group = [f for f in fields if f.spec.group == group]
        if not in_group:
            continue
        add("")
        add("-" * 78)
        add(_GROUP_TITLES.get(group, group.upper()))
        add("-" * 78)
        for decoded in in_group:
            label = decoded.spec.display_label(names)
            line = (f"  {label:<26} {decoded.format_value():>10}"
                    f"   [0x{decoded.spec.offset:03x} = {decoded.hex:<5} roh {decoded.raw:>5}]")
            if not decoded.plausible:
                line += "  UNPLAUSIBEL"
            add(line)
            # Nebenlesart nur zeigen, wo sie physikalisch Sinn ergibt -- sonst
            # steht unter jedem Flag-Byte eine sinnlose Minusgrad-Zeile.
            if decoded.spec.encoding is RAW_U8 and "temp_half40" in decoded.alternatives:
                alt = decoded.alternatives["temp_half40"]
                if 0.0 <= alt <= 87.5:
                    add(f"  {'':<26} {'':>10}   auch lesbar als "
                        f"{alt:.1f} C (Byte/2-40)")

    unmapped = log.unmapped_bytes()
    add("")
    add("-" * 78)
    add("NICHT ZUGEORDNETE BELEGTE BYTES")
    add("-" * 78)
    if unmapped:
        add("  Diese Bytes sind belegt, ihre Bedeutung ist nicht bekannt:")
        for offset, value in unmapped:
            add(f"    0x{offset:03x} = 0x{value:02x} ({value:3d})")
    else:
        add("  keine -- alle belegten Bytes sind einem Feld zugeordnet.")

    add("")
    add("-" * 78)
    add("HINWEIS")
    add("-" * 78)
    add("  Nachgewiesen sind Zeitstempel, Pruefsumme und die beiden")
    add("  Temperaturkodierungen (LE16/100 und Byte/2-40).")
    add("  Welcher Offset welcher Sensor bzw. welche Einstellung ist, ist NICHT")
    add("  nachgewiesen -- die Feldnamen sind Platzhalter. Zuordnung per")
    add("  --diff / --csv gegen das FTC4-Display, siehe docs/LOG_FORMAT.md.")
    return "\n".join(out)


def render_diff(
    older: FTC4Log,
    newer: FTC4Log,
    layout: Sequence[FieldSpec] = LOG_LAYOUT,
    names: Optional[Mapping[str, str]] = None,
) -> str:
    """Vergleicht zwei Logs und zeigt nur, was sich geaendert hat.

    Das ist der Weg zur Feldzuordnung: zwei Logs zu Zeitpunkten mit bekannten
    Displaywerten vergleichen -- was sich passend mitbewegt hat, ist das
    gesuchte Feld.
    """
    out: List[str] = []
    add = out.append
    add("=" * 78)
    add(f"VERGLEICH  {older.filename}  ->  {newer.filename}")
    add("=" * 78)
    for log in (older, newer):
        stamp = log.timestamp
        add(f"  {log.filename:<24} {stamp.strftime('%d.%m.%Y %H:%M') if stamp else '?':<18}"
            f" Pruefsumme {'OK' if log.checksum_ok else 'FEHLERHAFT'}")

    old_fields = {f.spec.key: f for f in older.decode_all(layout)}
    new_fields = {f.spec.key: f for f in newer.decode_all(layout)}

    add("")
    add(f"{'Feld':<26} {'vorher':>12} {'nachher':>12} {'Aenderung':>12}   Offset")
    add("-" * 78)
    changed = 0
    for key, new in new_fields.items():
        old = old_fields.get(key)
        if old is None or old.raw == new.raw:
            continue
        changed += 1
        delta = new.value - old.value
        add(f"{new.spec.display_label(names):<26} {old.format_value():>12} "
            f"{new.format_value():>12} {delta:>+12.2f}   0x{new.spec.offset:03x}")
    if not changed:
        add("  (keine Feldaenderung)")

    raw_diffs = [i for i in range(LOG_SIZE) if older.data[i] != newer.data[i]]
    unmapped_diffs = [i for i in raw_diffs if i not in _MAPPED_OFFSETS]
    add("")
    add(f"Geaenderte Bytes gesamt: {len(raw_diffs)}   davon nicht zugeordnet: "
        f"{len(unmapped_diffs)}")
    if unmapped_diffs:
        add("  Nicht zugeordnete Aenderungen (hier lohnt das Hinsehen):")
        for offset in unmapped_diffs:
            add(f"    0x{offset:03x}: 0x{older.data[offset]:02x} -> 0x{newer.data[offset]:02x}")
    return "\n".join(out)


def write_csv(
    logs: Sequence[FTC4Log],
    target: "str | Path",
    layout: Sequence[FieldSpec] = LOG_LAYOUT,
    names: Optional[Mapping[str, str]] = None,
) -> Path:
    """Schreibt mehrere Logs als Zeitreihe in eine CSV-Datei.

    Eine Zeile je Logdatei, eine Spalte je Feld -- damit laesst sich in einer
    Tabelle sofort sehen, welches Feld sich wann wie bewegt.
    """
    path = Path(target)
    ordered = sorted(logs, key=lambda l: (l.timestamp or dt.datetime.min, l.filename))
    header = ["datei", "zeitstempel", "pruefsumme_ok"]
    header += [spec.display_label(names) for spec in layout]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(header)
        for log in ordered:
            stamp = log.timestamp
            row: List[object] = [
                log.filename,
                stamp.isoformat(sep=" ") if stamp else "",
                "ja" if log.checksum_ok else "nein",
            ]
            row += [log.decode_field(spec).value for spec in layout]
            writer.writerow(row)
    return path


def render_hexdump(log: FTC4Log, only_active: bool = True) -> str:
    """Hexdump der Logdatei; ueberspringt auf Wunsch die Null-Bereiche."""
    out: List[str] = [f"HEXDUMP  {log.filename}"]
    for base in range(0, LOG_SIZE, 16):
        chunk = log.data[base : base + 16]
        if only_active and not any(chunk):
            continue
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"  0x{base:03x}  {hexpart:<47}  {text}")
    return "\n".join(out)


def load_names(path: "str | Path") -> Dict[str, str]:
    """Laedt eigene Feldnamen aus einer JSON-Datei ``{"soll06": "WW-Solltemp"}``."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LogDecodeError("Namensdatei muss ein JSON-Objekt {\"feld\": \"Name\"} sein")
    known = {spec.key for spec in LOG_LAYOUT}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise LogDecodeError(
            f"Unbekannte Feldschluessel in der Namensdatei: {unknown}. "
            f"Bekannt sind z.B. {sorted(known)[:5]} ..."
        )
    return {key: str(value) for key, value in raw.items()}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _collect_logs(paths: Iterable[str]) -> Tuple[List[FTC4Log], List[str]]:
    """Liest Logs ein und sammelt Meldungen zu uebersprungenen Dateien."""
    logs: List[FTC4Log] = []
    notes: List[str] = []
    for entry in paths:
        path = Path(entry)
        if path.is_dir():
            candidates = sorted(path.glob("*.LOG")) + sorted(path.glob("*.log"))
        else:
            candidates = [path]
        for candidate in candidates:
            try:
                logs.append(FTC4Log.from_path(candidate))
            except LogDecodeError as exc:
                notes.append(f"uebersprungen: {exc}")
            except OSError as exc:
                notes.append(f"nicht lesbar: {candidate}: {exc}")
    return logs, notes


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``python -m src.log_decoder <dateien|verzeichnis> [--diff|--csv|--hex]``"""
    parser = argparse.ArgumentParser(
        prog="python -m src.log_decoder",
        description="Dekodiert FTC4-Logdateien (*.LOG) in lesbaren Klartext.",
    )
    parser.add_argument("paths", nargs="+", metavar="PFAD",
                        help="Logdateien oder ein Verzeichnis mit *.LOG")
    parser.add_argument("--diff", action="store_true",
                        help="zwei Logs vergleichen statt einzeln ausgeben")
    parser.add_argument("--csv", metavar="DATEI",
                        help="alle Logs als Zeitreihe in eine CSV-Datei schreiben")
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    parser.add_argument("--hex", action="store_true", help="zusaetzlich einen Hexdump ausgeben")
    parser.add_argument("--names", metavar="DATEI",
                        help="JSON mit eigenen Feldnamen {\"soll06\": \"WW-Solltemp\"}")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    names: Optional[Dict[str, str]] = None
    if args.names:
        try:
            names = load_names(args.names)
        except (LogDecodeError, OSError, json.JSONDecodeError) as exc:
            print(f"Namensdatei nicht verwendbar: {exc}", file=sys.stderr)
            return 2

    logs, notes = _collect_logs(args.paths)
    for note in notes:
        print(note, file=sys.stderr)
    if not logs:
        print("Keine verwertbare Logdatei gefunden.", file=sys.stderr)
        return 1

    if args.csv:
        target = write_csv(logs, args.csv, names=names)
        print(f"{len(logs)} Log(s) geschrieben nach {target}")
        return 0

    if args.json:
        print(json.dumps([log.as_dict() for log in logs], indent=2, ensure_ascii=False))
        return 0

    if args.diff:
        if len(logs) != 2:
            print(f"--diff braucht genau zwei Logs, bekommen: {len(logs)}", file=sys.stderr)
            return 2
        older, newer = sorted(logs, key=lambda l: (l.timestamp or dt.datetime.min, l.filename))
        print(render_diff(older, newer, names=names))
        return 0

    for index, log in enumerate(logs):
        if index:
            print()
        print(render_text(log, names=names))
        if args.hex:
            print()
            print(render_hexdump(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
