"""FTC4-Einstellungen anhand der Herstellertabelle benennen.

Das Hersteller-Werkzeug (SD_TOOL) bringt unter ``Teigi/`` Tabellen mit, die
jede Einstellung beschreiben: Titel, Einheit, Wertebereich, Werksvorgabe --
und in der letzten Spalte die **Byte-Nummer in der SD-Einstellungsdatei**.
Damit laesst sich jeder Record einer DAT-Datei benennen, ohne zu raten.

Die Tabelle wird zur Laufzeit aus der eigenen Installation gelesen; sie liegt
nicht im Repository::

    python -m src.rc_settings /pfad/Teigi/RCSetting_EN.xls data

Alle Byte-Nummern der Tabelle sind Vielfache von drei -- eine unabhaengige
Bestaetigung der 3-Byte-Recordstruktur aus :mod:`src.dat_decoder`.

Die Kodierung eines Werts steht nicht in der Tabelle. Sie wird deshalb aus dem
dokumentierten Wertebereich erschlossen: von den moeglichen Lesarten (BCD,
Rohwert, Halbgrad, Halbgrad mit Nullpunkt -40) bleiben die uebrig, die im
erlaubten Bereich landen. Bleibt genau eine, ist der Wert eindeutig; bleiben
mehrere, weist der Report sie alle aus.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.dat_decoder import DatDecodeError, DatRecord, FTC4DatFile

__all__ = ["SettingItem", "DecodedSetting", "load_items", "decode_file", "main"]

#: Registerkarte der Herstellertabelle -> DAT-Datei.
TAB_TO_FILE: Dict[int, str] = {
    1: "DHW.DAT",
    2: "HT&CL.DAT",
    3: "HOL.DAT",
    5: "INIT.DAT",
    6: "SER_1.DAT",
}

#: Dateinamen, unter denen die Heizen/Kuehlen-Datei vorkommt.
HT_CL_NAMES = ("HT&CL.DAT", "HT_CL.DAT")


@dataclass(frozen=True)
class SettingItem:
    """Eine Einstellung laut Herstellertabelle."""

    code: int
    tab: int
    title: str
    unit: str
    minimum: Optional[float]
    maximum: Optional[float]
    default: str
    choices: str
    offset: int

    @property
    def filename(self) -> str:
        return TAB_TO_FILE.get(self.tab, "")

    def in_range(self, value: float) -> bool:
        """Liegt ein Wert im dokumentierten Bereich?"""
        if self.minimum is None or self.maximum is None:
            return True
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class DecodedSetting:
    """Eine Einstellung mit dem Wert aus der DAT-Datei."""

    item: SettingItem
    record: Optional[DatRecord]
    readings: Dict[str, float]      # Lesart -> Wert, nur die im Bereich passenden
    choice: Optional[str]           # aufgeloester Auswahltext, wenn vorhanden
    all_readings: Dict[str, float]  # alle Lesarten, auch die unpassenden
    time: Optional[str] = None      # BCD-Uhrzeit, wenn der Record eine traegt

    @property
    def value(self) -> Optional[float]:
        """Der Wert, wenn genau eine Lesart passt."""
        return next(iter(self.readings.values())) if len(self.readings) == 1 else None

    @property
    def status(self) -> str:
        if self.record is None:
            return "kein Record an diesem Offset"
        if self.choice is not None or self.time is not None:
            return "eindeutig"
        if len(self.readings) == 1:
            return "eindeutig"
        if not self.readings:
            return "keine Lesart im erlaubten Bereich"
        return f"{len(self.readings)} Lesarten moeglich"


def _number(cell: object) -> Optional[float]:
    """Zellwert als Zahl, sonst None."""
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return float(cell)
    text = str(cell).strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def load_items(xls_path: "str | Path") -> List[SettingItem]:
    """Liest die Einstellungstabelle des Hersteller-Werkzeugs.

    Raises:
        DatDecodeError: wenn xlrd fehlt oder die Datei nicht lesbar ist.
    """
    try:
        import xlrd
    except ImportError as exc:                                  # pragma: no cover
        raise DatDecodeError(
            "Zum Lesen der Herstellertabelle wird xlrd gebraucht: pip install xlrd"
        ) from exc

    try:
        sheet = xlrd.open_workbook(str(xls_path)).sheet_by_name("Setting")
    except Exception as exc:
        raise DatDecodeError(f"{xls_path} nicht lesbar: {exc}") from exc

    items: List[SettingItem] = []
    tab = 0
    for row in range(1, sheet.nrows):
        cells = [sheet.cell_value(row, col) for col in range(sheet.ncols)]
        tab_cell = _number(cells[1])
        if tab_cell:
            tab = int(tab_cell)
        code = _number(cells[0])
        byte_text = str(cells[14]).strip()
        # Eintraege mit Bereichsangabe ("0000～00BA") sind Zeitprogramme und
        # belegen viele Records; sie werden hier uebersprungen.
        if code is None or not byte_text or "～" in byte_text:
            continue
        try:
            offset = int(byte_text, 16)
        except ValueError:
            continue
        title = " / ".join(str(c).strip() for c in cells[2:6] if str(c).strip())
        items.append(
            SettingItem(
                code=int(code),
                tab=tab,
                title=re.sub(r"\s+", " ", title),
                unit=str(cells[7]).strip(),
                minimum=_number(cells[10]),
                maximum=_number(cells[11]),
                default=str(cells[8]).strip(),
                choices=str(cells[13]).strip(),
                offset=offset,
            )
        )
    return items


def _readings(record: DatRecord) -> Dict[str, float]:
    """Alle Lesarten eines Records, unabhaengig von der Plausibilitaet.

    Bei Records mit zwei belegten Datenbytes -- der Heizkurve etwa, wo ein
    Record Aussentemperatur und Vorlauftemperatur zugleich traegt -- werden
    beide Bytes einzeln angeboten. Welches gemeint ist, entscheidet danach
    der Wertebereich des jeweiligen Eintrags.
    """
    out: Dict[str, float] = {}
    if record.bcd_value is not None:
        out["BCD"] = record.bcd_value
    for prefix, raw in (("", record.lo),) if record.hi == 0 else (
        ("LO ", record.lo), ("HI ", record.hi)
    ):
        out[f"{prefix}Rohwert"] = float(raw)
        out[f"{prefix}Sollwert (/2-20)"] = raw / 2 - 20
        out[f"{prefix}Aussen (/2-40)"] = raw / 2 - 40
    return out


def _resolve_choice(item: SettingItem, record: DatRecord) -> Optional[str]:
    """Loest eine Auswahlliste wie ``0:Normal/1:Eco`` gegen den Rohwert auf."""
    if not item.choices or ":" not in item.choices:
        return None
    for part in item.choices.split("/"):
        key, _, text = part.partition(":")
        if key.strip().isdigit() and int(key) == record.lo:
            return text.strip() or None
    return None


def decode_file(
    items: Sequence[SettingItem], dat: FTC4DatFile
) -> List[DecodedSetting]:
    """Ordnet die Eintraege einer Registerkarte den Records einer Datei zu."""
    by_offset = {record.offset: record for record in dat.records()}
    decoded: List[DecodedSetting] = []
    for item in items:
        record = by_offset.get(item.offset)
        if record is None:
            decoded.append(DecodedSetting(item, None, {}, None, {}))
            continue
        choice = _resolve_choice(item, record)
        every = _readings(record)
        # Eine BCD-Uhrzeit hat keinen sinnvollen Zahlenbereich -- die Tabelle
        # fuehrt dort einen Excel-Zeitwert, der nicht vergleichbar ist.
        time = record.bcd_time
        fitting = {} if time else {
            name: value for name, value in every.items() if item.in_range(value)
        }
        decoded.append(DecodedSetting(item, record, fitting, choice, every, time))
    return decoded


def render_text(decoded: Sequence[DecodedSetting], filename: str) -> str:
    """Klartext-Report der Einstellungen einer Datei."""
    out: List[str] = []
    add = out.append
    add("=" * 78)
    add(f"EINSTELLUNGEN  {filename}")
    add("=" * 78)
    add(f"  {'Byte':>5}  {'Roh':<9}  {'Wert':>12}  {'Bereich':<12}  Einstellung")
    add("-" * 78)
    for entry in decoded:
        item = entry.item
        raw = entry.record.hex if entry.record else "--"
        span = ("" if item.minimum is None
                else f"{item.minimum:g}..{item.maximum:g}")
        if entry.time is not None:
            shown = f"{entry.time} Uhr"
        elif entry.choice is not None:
            shown = entry.choice
        elif entry.value is not None:
            shown = f"{entry.value:g} {item.unit}".strip()
        else:
            shown = "?"
        add(f"  0x{item.offset:03x}  {raw:<9}  {shown:>12}  {span:<12}  {item.title[:34]}")
        if entry.time is None and entry.choice is None and entry.value is None:
            source = entry.readings or entry.all_readings
            variants = ", ".join(f"{k}={v:g}" for k, v in source.items())
            add(f"  {'':>5}  {'':<9}  {entry.status}"
                + (f" -- {variants}" if variants else ""))
    add("")
    add("  Titel, Bereiche und Byte-Nummern stammen aus der Tabelle des")
    add("  Hersteller-Werkzeugs. Die Kodierung steht dort nicht -- sie wird aus")
    add("  dem erlaubten Bereich erschlossen. Wo mehrere Lesarten passen, sind")
    add("  sie alle aufgefuehrt.")
    return "\n".join(out)


def _find_dat(directory: Path, filename: str) -> Optional[Path]:
    """Sucht eine DAT-Datei, auch unter abweichender Schreibweise."""
    names = HT_CL_NAMES if filename in HT_CL_NAMES else (filename,)
    for name in names:
        path = directory / name
        if path.is_file():
            return path
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``python -m src.rc_settings <RCSetting_XX.xls> <datenverzeichnis>``"""
    parser = argparse.ArgumentParser(
        prog="python -m src.rc_settings",
        description="Benennt die FTC4-Einstellungen anhand der Herstellertabelle.",
    )
    parser.add_argument("tabelle", metavar="RCSETTING_XLS",
                        help="RCSetting_XX.xls aus dem Teigi-Ordner des Werkzeugs")
    parser.add_argument("daten", metavar="VERZEICHNIS",
                        help="Verzeichnis mit den DAT-Dateien")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        items = load_items(args.tabelle)
    except DatDecodeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not items:
        print("Keine Eintraege mit Byte-Nummer in der Tabelle gefunden.", file=sys.stderr)
        return 1

    directory = Path(args.daten)
    shown = 0
    for tab in sorted({item.tab for item in items}):
        filename = TAB_TO_FILE.get(tab)
        if not filename:
            continue
        path = _find_dat(directory, filename)
        if path is None:
            continue
        dat = FTC4DatFile.from_path(path)
        entries = decode_file([i for i in items if i.tab == tab], dat)
        if not entries:
            continue
        if shown:
            print()
        print(render_text(entries, path.name))
        shown += 1

    if not shown:
        print(f"Keine passenden DAT-Dateien in {directory} gefunden.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
