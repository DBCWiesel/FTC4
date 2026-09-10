"""Dekodierung der FTC4-Konfigurationsdateien (*.DAT) von der SD-Karte.

Alle elf DAT-Dateien folgen derselben Struktur -- an einem kompletten
SD-Karten-Abzug nachgewiesen (11 von 11 Dateien):

* 512 Byte fest.
* Byte 0x1ff ist eine Pruefsumme: die Summe aller 512 Bytes ist 0 modulo 256.
  Dieselbe Regel wie bei den Logdateien.
* Der belegte Bereich davor besteht aus **3-Byte-Records**::

      [TYP] [HI] [LO]

  Die belegte Laenge ist bei jeder der elf Dateien ein exaktes Vielfaches
  von 3.

Beobachtete Typbytes: 0x01..0x09 und 0x0f. Sicher gedeutet ist bisher:

``0x0f`` mit ``HI == 0``
    Ein Einzelwert-Parameter: die Nutzlast steckt allein im LO-Byte.
    In HT&CL.DAT sind diese Werte Temperaturen in 0.5-Grad-Schritten -- dort
    stehen die drei am Geraet abgelesenen Referenzwerte als Records:
    ``0f 00 4c`` = 38.0 C, ``0f 00 64`` = 50.0 C, ``0f 00 5a`` = 45.0 C.
    Der Typ ist aber **nicht** auf Temperaturen festgelegt: SER_1.DAT enthaelt
    Typ-0x0f-Records mit LO bis 230, was als Temperatur (115 C) keinen Sinn
    ergibt. Der Decoder gibt deshalb immer den Rohwert aus und die
    Temperaturlesart nur als gekennzeichneten Zusatz.

``0x03``
    Eine Zahl in BCD mit einer Nachkommastelle: ``03 01 50`` sind die Ziffern
    0,1,5,0 und damit 15.0. Gegen die Werksvorgaben der Herstellertabelle
    geprueft -- acht von acht unveraenderten Werten kommen exakt heraus.

``0x04``
    Eine Uhrzeit in BCD: ``04 13 00`` ist 13:00.

``HI == 0xff und LO == 0xff``
    Nicht belegter Eintrag. In allen Dateien konsistent.

Die Bedeutung der uebrigen Typen und der einzelnen Record-Positionen ist
**nicht** nachgewiesen. Der Decoder gibt sie roh aus, mit den plausiblen
Lesarten daneben -- er behauptet nichts, was nicht belegt ist.

Die Zeitplandateien SCH_1..SCH_5 enthalten je 35 Records vom Typ 0x06, also
7 Tage zu 5 Zeitfenstern; SCH_1 und SCH_2 zusaetzlich 28 Records vom Typ 0x07
(7 Tage zu 4). Die Zuordnung der Tagesindizes zu Mo..So ist ungeprueft.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "DAT_SIZE",
    "DatDecodeError",
    "DatRecord",
    "FTC4DatFile",
    "render_text",
    "main",
]

#: Groesse einer FTC4-Konfigurationsdatei in Bytes.
DAT_SIZE = 512

#: Laenge eines Records in Bytes.
RECORD_SIZE = 3

#: Typbyte fuer Records, deren Nutzlast allein im LO-Byte steckt.
TYPE_SINGLE_VALUE = 0x0F

#: Alter Name von :data:`TYPE_SINGLE_VALUE`.
TYPE_TEMPERATURE = TYPE_SINGLE_VALUE

#: Fenster, in dem die 0.5-Grad-Lesart eines Einzelwerts physikalisch Sinn ergibt.
PLAUSIBLE_CELSIUS = (0.0, 90.0)

#: Typbyte der Zeitfenster in den SCH-Dateien.
TYPE_SCHEDULE = 0x06

#: Typbyte fuer BCD-Zahlen mit einer Nachkommastelle.
TYPE_BCD_NUMBER = 0x03

#: Typbyte fuer BCD-Uhrzeiten (HHMM).
TYPE_BCD_TIME = 0x04

#: Datenbytes eines nicht belegten Eintrags.
UNSET = (0xFF, 0xFF)

#: Kurzbeschreibung der Dateien, aus den Projektunterlagen uebernommen.
FILE_PURPOSE: Dict[str, str] = {
    "DHW.DAT": "Warmwasserbereitung",
    "HOL.DAT": "Ferienmodus",
    "HT&CL.DAT": "Heizen und Kuehlen",
    "HT_CL.DAT": "Heizen und Kuehlen",
    "INIT.DAT": "Initialisierung",
    "SCH_1.DAT": "Zeitprogramm 1",
    "SCH_2.DAT": "Zeitprogramm 2",
    "SCH_3.DAT": "Zeitprogramm 3",
    "SCH_4.DAT": "Zeitprogramm 4",
    "SCH_5.DAT": "Zeitprogramm 5",
    "SER_1.DAT": "Service / Parameter 1",
    "SER_2.DAT": "Service / Parameter 2",
}


class DatDecodeError(ValueError):
    """Fehler beim Einlesen oder Dekodieren einer DAT-Datei."""


def bcd_digits(hi: int, lo: int) -> Optional[Tuple[int, int, int, int]]:
    """Die vier BCD-Ziffern von ``HI:LO``; ``None``, wenn keine gueltige BCD.

    Ein Halbbyte groesser als 9 ist keine BCD-Ziffer -- daran erkennt man, dass
    ein Record diese Kodierung nicht verwendet.
    """
    digits = (hi >> 4, hi & 0x0F, lo >> 4, lo & 0x0F)
    return None if any(d > 9 for d in digits) else digits


@dataclass(frozen=True)
class DatRecord:
    """Ein 3-Byte-Record ``[TYP][HI][LO]`` aus einer DAT-Datei."""

    index: int
    offset: int
    type: int
    hi: int
    lo: int

    @property
    def is_unset(self) -> bool:
        """``ff ff`` -- ein nicht belegter Eintrag."""
        return (self.hi, self.lo) == UNSET

    @property
    def is_single_value(self) -> bool:
        """Typ 0x0f mit HI == 0: die Nutzlast steckt allein im LO-Byte."""
        return self.type == TYPE_SINGLE_VALUE and self.hi == 0 and not self.is_unset

    @property
    def celsius(self) -> Optional[float]:
        """Der Einzelwert als Temperatur gelesen: ``LO * 0.5`` Grad.

        Nur eine Lesart, keine Zusicherung. In HT&CL.DAT stimmt sie gegen die
        am Geraet abgelesenen Werte; in SER_1.DAT stehen unter demselben Typ
        Parameter, die als Temperatur unsinnig sind. Deshalb immer zusammen
        mit :attr:`celsius_plausible` verwenden.
        """
        return self.lo * 0.5 if self.is_single_value else None

    @property
    def celsius_plausible(self) -> bool:
        """True, wenn die 0.5-Grad-Lesart im physikalisch sinnvollen Fenster liegt."""
        value = self.celsius
        return value is not None and PLAUSIBLE_CELSIUS[0] <= value <= PLAUSIBLE_CELSIUS[1]

    @property
    def is_temperature(self) -> bool:
        """Einzelwert, dessen 0.5-Grad-Lesart plausibel ist."""
        return self.celsius_plausible

    @property
    def bcd_value(self) -> Optional[float]:
        """Typ 0x03: vierstellige BCD-Zahl mit einer Nachkommastelle.

        ``03 01 50`` sind die Ziffern 0,1,5,0 und damit 15.0. An den
        Werksvorgaben aus der Herstellertabelle geprueft: acht von acht
        unveraenderten Werten in DHW.DAT und HOL.DAT kommen exakt heraus.
        """
        if self.type != TYPE_BCD_NUMBER or self.is_unset:
            return None
        digits = bcd_digits(self.hi, self.lo)
        if digits is None:
            return None
        return (digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3]) / 10

    @property
    def bcd_time(self) -> Optional[str]:
        """Typ 0x04: Uhrzeit als BCD ``HHMM``.

        ``04 13 00`` ist 13:00. ``None``, wenn die Ziffern keine gueltige
        Uhrzeit ergeben.
        """
        if self.type != TYPE_BCD_TIME or self.is_unset:
            return None
        digits = bcd_digits(self.hi, self.lo)
        if digits is None:
            return None
        hour = digits[0] * 10 + digits[1]
        minute = digits[2] * 10 + digits[3]
        if hour > 23 or minute > 59:
            return None
        return f"{hour:02d}:{minute:02d}"

    @property
    def be16(self) -> int:
        """Die beiden Datenbytes als 16-Bit-Wert, HI zuerst."""
        return self.hi * 256 + self.lo

    @property
    def hex(self) -> str:
        return f"{self.type:02x} {self.hi:02x} {self.lo:02x}"

    def describe(self) -> str:
        """Beste belegbare Deutung des Records als kurzer Text."""
        if self.is_unset:
            return "nicht belegt"
        if self.bcd_time is not None:
            return f"{self.bcd_time} Uhr"
        if self.bcd_value is not None:
            return f"{self.bcd_value:g}  (BCD)"
        if self.is_single_value:
            reading = f"Wert {self.lo:3d}"
            if self.celsius_plausible:
                return f"{reading}   als Temperatur: {self.celsius:.1f} C"
            return f"{reading}   (als Temperatur {self.celsius:.1f} C -- unplausibel)"
        if self.hi == 0:
            return f"Wert {self.lo}"
        return f"HI {self.hi} / LO {self.lo}  (16 Bit: {self.be16})"

    def as_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "offset": f"0x{self.offset:03x}",
            "type": f"0x{self.type:02x}",
            "hi": self.hi,
            "lo": self.lo,
            "hex": self.hex,
            "unset": self.is_unset,
            "single_value": self.is_single_value,
            "celsius": self.celsius,
            "celsius_plausible": self.celsius_plausible,
            "bcd_value": self.bcd_value,
            "bcd_time": self.bcd_time,
            "description": self.describe(),
        }


@dataclass
class FTC4DatFile:
    """Eine eingelesene FTC4-Konfigurationsdatei."""

    data: bytes
    filename: str = ""

    @classmethod
    def from_path(cls, path: "str | Path") -> "FTC4DatFile":
        """Liest eine .DAT-Datei ein.

        Raises:
            DatDecodeError: wenn die Datei nicht 512 Byte gross ist.
        """
        file_path = Path(path)
        data = file_path.read_bytes()
        if len(data) != DAT_SIZE:
            raise DatDecodeError(
                f"{file_path.name}: {len(data)} Byte, erwartet werden {DAT_SIZE}."
            )
        return cls(data=data, filename=file_path.name)

    @property
    def purpose(self) -> str:
        return FILE_PURPOSE.get(self.filename.upper(), "unbekannt")

    @property
    def checksum_ok(self) -> bool:
        """True, wenn die Summe aller 512 Bytes 0 modulo 256 ergibt."""
        return sum(self.data) % 256 == 0

    @property
    def checksum_byte(self) -> int:
        return self.data[DAT_SIZE - 1]

    def expected_checksum(self) -> int:
        """Pruefsummenbyte, das zu den ersten 511 Bytes passen wuerde."""
        return (-sum(self.data[: DAT_SIZE - 1])) % 256

    @property
    def active_length(self) -> int:
        """Laenge des belegten Bereichs vor der Pruefsumme, in Bytes.

        Gemessen bis zum letzten Byte ungleich 0x00 und auf ein Vielfaches von
        drei aufgerundet, damit ein Record, dessen letztes Byte 0x00 ist, nicht
        abgeschnitten wird.
        """
        payload = self.data[: DAT_SIZE - 1]
        last = max((i for i, byte in enumerate(payload) if byte), default=-1)
        if last < 0:
            return 0
        length = last + 1
        return length + (-length % RECORD_SIZE)

    @property
    def record_aligned(self) -> bool:
        """True, wenn der belegte Bereich glatt in 3-Byte-Records aufgeht.

        Das ist die Strukturprobe: bei allen elf Dateien des Referenzabzugs
        trifft sie zu. Ein ``False`` heisst, dass die Datei anders aufgebaut ist
        als angenommen.
        """
        payload_end = self.active_length
        return payload_end % RECORD_SIZE == 0 and payload_end <= DAT_SIZE - 1

    def records(self) -> List[DatRecord]:
        """Alle Records des belegten Bereichs."""
        return [
            DatRecord(
                index=offset // RECORD_SIZE,
                offset=offset,
                type=self.data[offset],
                hi=self.data[offset + 1],
                lo=self.data[offset + 2],
            )
            for offset in range(0, self.active_length, RECORD_SIZE)
        ]

    def records_by_type(self, type_byte: int) -> List[DatRecord]:
        return [r for r in self.records() if r.type == type_byte]

    def single_values(self) -> List[DatRecord]:
        """Alle Einzelwert-Records (Typ 0x0f mit HI = 0)."""
        return [r for r in self.records() if r.is_single_value]

    def temperatures(self) -> List[DatRecord]:
        """Einzelwerte, deren 0.5-Grad-Lesart plausibel ist."""
        return [r for r in self.records() if r.celsius_plausible]

    def schedule_days(self, slots_per_day: int = 5) -> List[List[DatRecord]]:
        """Die Typ-0x06-Records in Tagesgruppen zerlegt.

        In den SCH-Dateien stehen 35 solcher Records = 7 Tage zu 5 Zeitfenstern.
        Welcher Tagesindex Montag ist, ist ungeprueft.
        """
        entries = self.records_by_type(TYPE_SCHEDULE)
        return [entries[i : i + slots_per_day] for i in range(0, len(entries), slots_per_day)]

    def as_dict(self) -> Dict[str, object]:
        """JSON-taugliche Gesamtdarstellung."""
        return {
            "filename": self.filename,
            "purpose": self.purpose,
            "checksum_ok": self.checksum_ok,
            "active_length": self.active_length,
            "record_aligned": self.record_aligned,
            "record_count": len(self.records()),
            "records": [r.as_dict() for r in self.records()],
        }


# ----------------------------------------------------------------------
# Klartext-Ausgabe
# ----------------------------------------------------------------------

def _render_schedule(dat: FTC4DatFile, out: List[str]) -> None:
    """Zeitfenster der SCH-Dateien tageweise ausgeben."""
    days = dat.schedule_days()
    if not days:
        return
    out.append("")
    out.append(f"  Zeitfenster (Typ 0x06): {len(days)} Gruppen zu je "
               f"{max(len(d) for d in days)} Eintraegen")
    out.append("  Die Zuordnung der Gruppen zu Wochentagen ist ungeprueft.")
    for number, day in enumerate(days, start=1):
        used = [r for r in day if not r.is_unset]
        out.append(f"    Gruppe {number}: {len(used)} von {len(day)} Eintraegen belegt")
        for record in day:
            state = "nicht belegt" if record.is_unset else f"HI {record.hi:3d}  LO {record.lo:3d}"
            out.append(f"      0x{record.offset:03x}  {record.hex}   {state}")


def render_text(dat: FTC4DatFile, max_records: Optional[int] = None) -> str:
    """Erzeugt den lesbaren Klartext-Report einer DAT-Datei."""
    out: List[str] = []
    add = out.append

    add("=" * 78)
    add(f"FTC4-KONFIGURATION  {dat.filename or '(ohne Namen)'}   -- {dat.purpose}")
    add("=" * 78)

    if dat.checksum_ok:
        add(f"Pruefsumme      : OK (0x{dat.checksum_byte:02x})")
    else:
        add(f"Pruefsumme      : FEHLERHAFT -- Datei ist 0x{dat.checksum_byte:02x}, "
            f"erwartet 0x{dat.expected_checksum():02x}")
        add("                  Die Datei ist beschaedigt oder unvollstaendig kopiert.")

    records = dat.records()
    add(f"Belegter Teil   : {dat.active_length} Byte = {len(records)} Records a 3 Byte")
    if not dat.record_aligned:
        add("                  ACHTUNG: geht nicht glatt in 3-Byte-Records auf -- "
            "diese Datei ist anders aufgebaut als die Referenzdateien.")

    singles = dat.single_values()
    if singles:
        add("")
        add("-" * 78)
        add("EINZELWERT-PARAMETER  (Typ 0x0f: Nutzlast im LO-Byte)")
        add("-" * 78)
        add("  Die Temperaturspalte ist die 0.5-Grad-Lesart. Sie ist fuer HT&CL.DAT")
        add("  gegen Geraetewerte belegt, gilt aber nicht fuer jeden Typ-0x0f-Record.")
        add(f"  {'Record':>7}  {'Offset':>6}  {'Roh':<9}  {'Wert':>5}   Temperaturlesart")
        for record in singles:
            note = f"{record.celsius:6.1f} C" if record.celsius_plausible \
                else f"{record.celsius:6.1f} C  unplausibel"
            add(f"  {record.index:7d}  0x{record.offset:03x}  {record.hex:<9}  "
                f"{record.lo:5d}   {note}")

    if dat.records_by_type(TYPE_SCHEDULE):
        add("")
        add("-" * 78)
        add("ZEITPROGRAMM")
        add("-" * 78)
        _render_schedule(dat, out)

    add("")
    add("-" * 78)
    add("ALLE RECORDS")
    add("-" * 78)
    add(f"  {'Nr':>4}  {'Offset':>6}  {'Roh':<9}  Deutung")
    shown = records if max_records is None else records[:max_records]
    for record in shown:
        add(f"  {record.index:4d}  0x{record.offset:03x}  {record.hex:<9}  {record.describe()}")
    if len(shown) < len(records):
        add(f"  ... {len(records) - len(shown)} weitere Records "
            f"(vollstaendig mit --alle)")

    add("")
    add("-" * 78)
    add("HINWEIS")
    add("-" * 78)
    add("  Belegt sind: Pruefsumme, die 3-Byte-Record-Struktur, Typ 0x0f mit")
    add("  HI=0 als Einzelwert und ff ff als nicht belegter Eintrag. Die")
    add("  0.5-Grad-Lesart ist fuer HT&CL.DAT gegen Geraetewerte belegt.")
    add("  Was die einzelnen Record-Positionen bedeuten, ist NICHT")
    add("  nachgewiesen -- siehe docs/DAT_FORMAT.md.")
    return "\n".join(out)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def collect_dat_files(paths: Iterable[str]) -> Tuple[List[FTC4DatFile], List[str]]:
    """Liest DAT-Dateien ein und sammelt Meldungen zu uebersprungenen Dateien."""
    files: List[FTC4DatFile] = []
    notes: List[str] = []
    for entry in paths:
        path = Path(entry)
        candidates = (
            sorted(path.glob("*.DAT")) + sorted(path.glob("*.dat"))
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            try:
                files.append(FTC4DatFile.from_path(candidate))
            except DatDecodeError as exc:
                notes.append(f"uebersprungen: {exc}")
            except OSError as exc:
                notes.append(f"nicht lesbar: {candidate}: {exc}")
    return files, notes


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``python -m src.dat_decoder <dateien|verzeichnis>``"""
    parser = argparse.ArgumentParser(
        prog="python -m src.dat_decoder",
        description="Dekodiert FTC4-Konfigurationsdateien (*.DAT) in lesbaren Klartext.",
    )
    parser.add_argument("paths", nargs="+", metavar="PFAD",
                        help="DAT-Dateien oder ein Verzeichnis")
    parser.add_argument("--alle", action="store_true",
                        help="alle Records ausgeben statt der ersten 40")
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    files, notes = collect_dat_files(args.paths)
    for note in notes:
        print(note, file=sys.stderr)
    if not files:
        print("Keine verwertbare DAT-Datei gefunden.", file=sys.stderr)
        return 1

    if args.json:
        import json
        print(json.dumps([f.as_dict() for f in files], indent=2, ensure_ascii=False))
        return 0

    for index, dat in enumerate(files):
        if index:
            print()
        print(render_text(dat, max_records=None if args.alle else 40))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
