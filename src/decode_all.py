"""Kompletten FTC4-SD-Karten-Abzug in eine lesbare Textdatei umwandeln.

Die FTC4 legt auf der Karte zwei Baeume an::

    SETTING/*.DAT              Konfiguration, 11 Dateien
    LOG/<JJJJ>/<MM>/<TT>/*.LOG Betriebslogs, ein 512-Byte-Log je Minute
    LOG/DIR1/DIR2/DIR3/TEST.LOG  Schreibtest der Karte, keine Nutzdaten

Dieses Modul laeuft ueber beides und schreibt einen zusammenhaengenden
Klartext-Report: Konfiguration im Detail, Logs als Zeitreihe mit Kennzahlen je
Feld. Fuer den Vollausdruck einzelner Logs ist :mod:`src.log_decoder`
zustaendig, fuer einzelne Konfigurationsdateien :mod:`src.dat_decoder`.

    python -m src.decode_all /pfad/zum/kartenabzug -o ftc4.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.dat_decoder import FTC4DatFile, DatDecodeError
from src.dat_decoder import render_text as render_dat
from src.log_decoder import (
    LOG_LAYOUT,
    FTC4Log,
    LogDecodeError,
    write_csv,
)
from src.log_decoder import render_text as render_log

__all__ = ["find_files", "build_report", "main"]


def find_files(root: "str | Path") -> Tuple[List[Path], List[Path], List[Path]]:
    """Durchsucht einen Kartenabzug nach DAT-, LOG- und Testdateien.

    Returns:
        ``(dat_pfade, log_pfade, test_pfade)``. Als Testdatei gilt jede .LOG,
        die nicht 512 Byte gross ist -- das ist die Kartentestdatei TEST.LOG.
    """
    base = Path(root)
    dats = sorted(p for p in base.rglob("*.DAT") if p.is_file())
    dats += sorted(p for p in base.rglob("*.dat") if p.is_file())
    logs: List[Path] = []
    tests: List[Path] = []
    for path in sorted(base.rglob("*.LOG")) + sorted(base.rglob("*.log")):
        if not path.is_file():
            continue
        (logs if path.stat().st_size == 512 else tests).append(path)
    return dats, logs, tests


def _log_series_section(logs: Sequence[FTC4Log]) -> List[str]:
    """Kennzahlen je Feld ueber alle Logs -- der Kern der Zeitreihe."""
    out: List[str] = []
    ordered = sorted(logs, key=lambda l: (l.timestamp is None, l.timestamp))
    series: Dict[str, List[float]] = {}
    for spec in LOG_LAYOUT:
        series[spec.key] = [log.decode_field(spec).value for log in ordered]

    first, last = ordered[0], ordered[-1]
    out.append(f"Zeitraum        : {first.timestamp}  bis  {last.timestamp}")
    out.append(f"Anzahl Logs     : {len(ordered)}")
    bad = [l.filename for l in ordered if not l.checksum_ok]
    out.append(f"Pruefsumme      : {len(ordered) - len(bad)} von {len(ordered)} in Ordnung"
               + (f", fehlerhaft: {bad}" if bad else ""))

    moving = [s for s in LOG_LAYOUT if len(set(series[s.key])) > 1]
    constant = [s for s in LOG_LAYOUT if len(set(series[s.key])) == 1]

    out.append("")
    out.append("-" * 78)
    out.append(f"VERAENDERLICHE FELDER ({len(moving)})")
    out.append("-" * 78)
    out.append(f"  {'Feld':<20} {'Offset':>7} {'Beginn':>9} {'Ende':>9} "
               f"{'min':>8} {'max':>8} {'Stufen':>7}")
    for spec in moving:
        values = series[spec.key]
        out.append(f"  {spec.key:<20} 0x{spec.offset:03x}  {values[0]:9.2f} {values[-1]:9.2f} "
                   f"{min(values):8.2f} {max(values):8.2f} {len(set(values)):7d}")

    out.append("")
    out.append("-" * 78)
    out.append(f"KONSTANTE FELDER ({len(constant)}) -- ueber den ganzen Zeitraum unveraendert")
    out.append("-" * 78)
    for spec in constant:
        decoded = ordered[0].decode_field(spec)
        out.append(f"  {spec.key:<20} 0x{spec.offset:03x}  {decoded.format_value():>10}"
                   f"   [{decoded.hex}]")

    # Stundenraster: kompakter Blick auf den Verlauf.
    if moving:
        out.append("")
        out.append("-" * 78)
        out.append("VERLAUF DER VERAENDERLICHEN FELDER (Stundenraster)")
        out.append("-" * 78)
        out.append("  Zeit " + "".join(f"{s.key:>14}" for s in moving))
        seen = set()
        for index, log in enumerate(ordered):
            stamp = log.timestamp
            if stamp is None or stamp.hour in seen:
                continue
            seen.add(stamp.hour)
            out.append(f"  {stamp:%d.%m %H:%M}"
                       + "".join(f"{series[s.key][index]:14.2f}" for s in moving))

    unmapped = sorted({o for log in ordered for o, _ in log.unmapped_bytes()})
    out.append("")
    out.append("-" * 78)
    out.append("NICHT ZUGEORDNETE BELEGTE BYTES")
    out.append("-" * 78)
    out.append("  " + (", ".join(f"0x{o:03x}" for o in unmapped) if unmapped
                       else "keine -- jedes belegte Byte ist einem Feld zugeordnet."))
    return out


def build_report(root: "str | Path", full_records: bool = False) -> str:
    """Baut den Gesamtreport ueber einen Kartenabzug."""
    dats, log_paths, tests = find_files(root)
    out: List[str] = []
    add = out.append

    add("#" * 78)
    add("# FTC4 SD-KARTEN-ABZUG -- KLARTEXT-REPORT")
    add(f"# Quelle: {Path(root)}")
    add("#" * 78)
    add("")
    add(f"Konfigurationsdateien : {len(dats)}")
    add(f"Logdateien            : {len(log_paths)}")
    add(f"Kartentestdateien     : {len(tests)}"
        + (f"  ({', '.join(p.name for p in tests)})" if tests else ""))

    if dats:
        add("")
        add("#" * 78)
        add("# TEIL 1 -- KONFIGURATION (SETTING/*.DAT)")
        add("#" * 78)
        for path in dats:
            try:
                dat = FTC4DatFile.from_path(path)
            except (DatDecodeError, OSError) as exc:
                add("")
                add(f"[uebersprungen] {path.name}: {exc}")
                continue
            add("")
            add(render_dat(dat, max_records=None if full_records else 40))

    if log_paths:
        logs: List[FTC4Log] = []
        skipped: List[str] = []
        for path in log_paths:
            try:
                logs.append(FTC4Log.from_path(path))
            except (LogDecodeError, OSError) as exc:
                skipped.append(f"{path.name}: {exc}")
        add("")
        add("#" * 78)
        add("# TEIL 2 -- BETRIEBSLOGS (LOG/**/*.LOG)")
        add("#" * 78)
        add("")
        if skipped:
            add(f"uebersprungen: {len(skipped)}")
            for note in skipped[:10]:
                add(f"  {note}")
        if logs:
            out.extend(_log_series_section(logs))
            add("")
            add("#" * 78)
            add("# TEIL 3 -- ERSTES UND LETZTES LOG IM DETAIL")
            add("#" * 78)
            ordered = sorted(logs, key=lambda l: (l.timestamp is None, l.timestamp))
            for log in (ordered[0], ordered[-1]) if len(ordered) > 1 else ordered:
                add("")
                add(render_log(log))

    add("")
    add("#" * 78)
    add("# WAS BELEGT IST UND WAS NICHT")
    add("#" * 78)
    add("  Belegt (an echten Geraetedaten nachgewiesen):")
    add("    - Pruefsumme: Summe aller 512 Byte = 0 mod 256, in DAT und LOG")
    add("    - DAT: 3-Byte-Records [TYP][HI][LO]; Typ 0x0f mit HI=0 traegt eine")
    add("      Temperatur als LO * 0.5 Grad; ff ff = nicht belegter Eintrag")
    add("    - LOG: Zeitstempel YY MM DD HH MM ab Offset 0x00, Mitternacht als")
    add("      Stunde 24 des Vortags; Temperaturen als LE16/100 und Byte/2-40")
    add("  NICHT belegt:")
    add("    - welcher Record bzw. welcher Offset welcher Sensor oder welche")
    add("      Einstellung ist. Die Feldnamen sind Platzhalter.")
    add("  Zuordnungsverfahren: docs/LOG_FORMAT.md und docs/DAT_FORMAT.md")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``python -m src.decode_all <kartenabzug> [-o report.txt] [--csv f.csv]``"""
    parser = argparse.ArgumentParser(
        prog="python -m src.decode_all",
        description="Wandelt einen kompletten FTC4-SD-Karten-Abzug in lesbaren Klartext.",
    )
    parser.add_argument("root", metavar="VERZEICHNIS",
                        help="Wurzel des Kartenabzugs (enthaelt SETTING/ und LOG/)")
    parser.add_argument("-o", "--out", metavar="DATEI",
                        help="Report in eine Datei schreiben statt auf die Konsole")
    parser.add_argument("--csv", metavar="DATEI",
                        help="zusaetzlich die Log-Zeitreihe als CSV schreiben")
    parser.add_argument("--alle", action="store_true",
                        help="alle Records der DAT-Dateien ausgeben statt der ersten 40")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    root = Path(args.root)
    if not root.is_dir():
        print(f"Kein Verzeichnis: {root}", file=sys.stderr)
        return 2

    report = build_report(root, full_records=args.alle)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Report geschrieben nach {args.out} ({len(report.splitlines())} Zeilen)")
    else:
        print(report)

    if args.csv:
        _, log_paths, _ = find_files(root)
        logs = []
        for path in log_paths:
            try:
                logs.append(FTC4Log.from_path(path))
            except (LogDecodeError, OSError):
                continue
        if logs:
            write_csv(logs, args.csv)
            print(f"Zeitreihe geschrieben nach {args.csv} ({len(logs)} Logs)")
        else:
            print("Keine Logs fuer die CSV-Ausgabe gefunden.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
