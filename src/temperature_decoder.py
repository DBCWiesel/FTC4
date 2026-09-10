"""Dekodierung von Temperaturwerten aus FTC4-Konfigurationsdateien.

Die Mitsubishi-Ecodan-FTC4-Steuerung legt ihre Konfiguration in 512-Byte
DAT-Dateien ab. Fuer HT_CL.DAT (Heizen/Kuehlen) gilt die Arbeitshypothese,
dass Temperaturen in 0.5-Grad-Schritten kodiert sind::

    celsius = raw_value * 0.5

Belegt durch die vom Geraet abgelesenen Werte:

    0x4c (76)  ->  38.0 C
    0x64 (100) ->  50.0 C
    0x5a (90)  ->  45.0 C

Das Modul ist bewusst abhaengigkeitsfrei: es arbeitet direkt auf ``bytes``
und benoetigt weder ``FTC4Analyzer`` noch externe Pakete. Ein Analyzer-Puffer
kann einfach durchgereicht werden::

    from src.temperature_decoder import TemperatureDecoder

    data = analyzer.files["HT_CL.DAT"]
    TemperatureDecoder.extract_setpoints(data)

Die DAT-Dateien sind aus 3-Byte-Records ``[TYP][HI][LO]`` aufgebaut; die
Temperaturen stehen im LO-Byte von Records mit Typ 0x0f. Der vollstaendige
Recordaufbau steckt in :mod:`src.dat_decoder`, das fuer ganze Dateien der
bessere Einstieg ist. Dieses Modul bleibt die Rechenschicht darunter.

WICHTIG: Welcher Record welchen Sollwert traegt, ist nicht nachgewiesen. Jeder
dekodierte Wert traegt deshalb ein ``plausible``-Flag und eine
``confidence``-Angabe. Siehe docs/DAT_FORMAT.md.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "HALF_STEP",
    "TemperatureDecodeError",
    "SetpointSpec",
    "Setpoint",
    "TemperatureCandidate",
    "TemperatureDecoder",
]

#: Aufloesung der FTC4-Temperaturkodierung in Grad Celsius pro Rohwert-Schritt.
HALF_STEP = 0.5

#: Breite in Bytes je unterstuetzter Kodierung.
_ENCODING_WIDTH: Mapping[str, int] = {
    "u8": 1,
    "s8": 1,
    "u16le": 2,
    "s16le": 2,
}

_SIGNED_ENCODINGS = frozenset({"s8", "s16le"})


class TemperatureDecodeError(ValueError):
    """Fehler beim Dekodieren oder Kodieren eines Temperaturwerts."""


@dataclass(frozen=True)
class SetpointSpec:
    """Beschreibt, wo und wie ein Sollwert in einer DAT-Datei liegt.

    Attribute:
        key:          Stabiler Schluessel fuer die Rueckgabe-Dicts.
        label:        Klartext-Bezeichnung fuer Reports.
        offset:       Byte-Offset in der DAT-Datei.
        encoding:     "u8", "s8", "u16le" oder "s16le".
        scale:        Faktor Rohwert -> Grad Celsius (0.5 bei Halbgradschritten).
        bias:         Additiver Nullpunkt: celsius = raw * scale + bias.
        valid_range:  Plausibilitaetsfenster (min, max) in Grad Celsius.
        confidence:   "confirmed" (am Geraet verifiziert) oder "hypothesis".
        note:         Freitext-Hinweis fuer Reports.
    """

    key: str
    label: str
    offset: int
    encoding: str = "u8"
    scale: float = HALF_STEP
    bias: float = 0.0
    valid_range: Tuple[float, float] = (0.0, 90.0)
    confidence: str = "hypothesis"
    note: str = ""

    def __post_init__(self) -> None:
        if self.encoding not in _ENCODING_WIDTH:
            raise TemperatureDecodeError(
                f"Unbekannte Kodierung {self.encoding!r} fuer {self.key!r}; "
                f"erlaubt: {sorted(_ENCODING_WIDTH)}"
            )
        if self.offset < 0:
            raise TemperatureDecodeError(f"Negativer Offset {self.offset} fuer {self.key!r}")
        if self.scale <= 0:
            raise TemperatureDecodeError(f"Skalierung muss > 0 sein, ist {self.scale}")
        low, high = self.valid_range
        if low > high:
            raise TemperatureDecodeError(f"Ungueltiger Wertebereich {self.valid_range} fuer {self.key!r}")

    @property
    def width(self) -> int:
        """Anzahl der von dieser Spezifikation gelesenen Bytes."""
        return _ENCODING_WIDTH[self.encoding]


@dataclass(frozen=True)
class Setpoint:
    """Ein dekodierter Sollwert samt Herkunft und Plausibilitaetsbewertung."""

    key: str
    label: str
    offset: int
    raw: int
    celsius: float
    plausible: bool
    confidence: str
    encoding: str
    hex: str
    note: str = ""
    #: Derselbe Offset unter den jeweils anderen Kodierungen - Entscheidungshilfe,
    #: solange die Byte-Breite nicht am Geraet bestaetigt ist.
    alternatives: Mapping[str, float] = None  # type: ignore[assignment]

    def as_dict(self) -> Dict[str, object]:
        """Report-taugliche Darstellung (JSON-serialisierbar)."""
        return {
            "key": self.key,
            "label": self.label,
            "offset": f"0x{self.offset:02x}",
            "raw": self.raw,
            "celsius": self.celsius,
            "plausible": self.plausible,
            "confidence": self.confidence,
            "encoding": self.encoding,
            "hex": self.hex,
            "note": self.note,
            "alternatives": dict(self.alternatives or {}),
        }


@dataclass(frozen=True)
class TemperatureCandidate:
    """Ein beim Scannen gefundener, temperaturverdaechtiger Rohwert."""

    offset: int
    raw: int
    celsius: float
    encoding: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "offset": f"0x{self.offset:02x}",
            "raw": self.raw,
            "celsius": self.celsius,
            "encoding": self.encoding,
        }


class TemperatureDecoder:
    """Dekodiert und kodiert FTC4-Temperaturwerte.

    Alle Dekodiermethoden sind zustandslos und lassen sich sowohl auf der
    Klasse als auch auf einer Instanz aufrufen::

        TemperatureDecoder.decode_0_5_degree_steps(0x4c)   # -> 38.0
        TemperatureDecoder().extract_setpoints(ht_cl_data)
    """

    HALF_STEP = HALF_STEP

    #: Am Geraet abgelesene Referenzwerte: Rohwert -> Grad Celsius.
    #: Grundlage der Validierung in :meth:`validate_known_values`.
    KNOWN_VALUES: Mapping[int, float] = {
        0x4C: 38.0,
        0x64: 50.0,
        0x5A: 45.0,
    }

    #: Erwartete Dateigroesse einer FTC4-DAT-Datei (ein Sektor).
    DAT_FILE_SIZE = 512

    #: Sollwert-Layout von HT&CL.DAT.
    #:
    #: An einem echten SD-Karten-Abzug nachgewiesen: die DAT-Dateien bestehen
    #: aus 3-Byte-Records ``[TYP][HI][LO]``. Ein Record mit Typ 0x0f und HI=0
    #: traegt im LO-Byte eine Temperatur in 0.5-Grad-Schritten. Die Wertebytes
    #: liegen also auf Offset ``3 * Recordnummer + 2``.
    #:
    #: Die frueher aus den Projektunterlagen uebernommene Annahme, die
    #: Sollwerte laegen auf Offset 0x02 und 0x04, ist damit **widerlegt**: dort
    #: stehen die Nullbytes der Records 0 und 1. Die Records 0 und 1 selbst
    #: tragen 0.0 und 1.0 und sind eher Modus-Flags als Temperaturen; sie
    #: stehen deshalb nicht in dieser Liste, tauchen aber in der vollstaendigen
    #: Recordliste von :mod:`src.dat_decoder` auf.
    #:
    #: Welcher dieser acht Werte Heizen, Kuehlen oder Warmwasser ist, ist
    #: weiterhin **nicht** nachgewiesen -- deshalb die neutralen Schluessel.
    HT_CL_SETPOINTS: Sequence[SetpointSpec] = tuple(
        SetpointSpec(
            key=f"ht_cl_r{record:02d}",
            label=f"HT&CL Record {record:02d}",
            offset=3 * record + 2,
            encoding="u8",
            scale=HALF_STEP,
            valid_range=(0.0, 90.0),
            confidence="encoding_confirmed",
            note="Kodierung belegt, Bedeutung der Position offen",
        )
        for record in (2, 3, 4, 5, 6, 10, 11, 12)
    )

    # ------------------------------------------------------------------
    # Skalare Konvertierung
    # ------------------------------------------------------------------

    @staticmethod
    def decode_0_5_degree_steps(byte_value: int) -> float:
        """Konvertiert einen Rohwert in Grad Celsius (0.5-Grad-Schritte).

        >>> TemperatureDecoder.decode_0_5_degree_steps(0x4c)
        38.0

        Raises:
            TemperatureDecodeError: bei nicht-ganzzahligem oder negativem Wert.
        """
        return TemperatureDecoder._scale_raw(byte_value, HALF_STEP)

    @staticmethod
    def decode_direct_celsius(byte_value: int) -> float:
        """Konvertiert einen Rohwert bei 1-Grad-Kodierung (Alternativhypothese)."""
        return TemperatureDecoder._scale_raw(byte_value, 1.0)

    @staticmethod
    def encode_0_5_degree_steps(celsius: float) -> int:
        """Rueckkonvertierung Grad Celsius -> Rohwert (0.5-Grad-Schritte).

        >>> TemperatureDecoder.encode_0_5_degree_steps(38.0)
        76

        Raises:
            TemperatureDecodeError: wenn der Wert kein Vielfaches von 0.5 ist
                oder nicht in ein vorzeichenloses Byte passt.
        """
        raw = TemperatureDecoder._unscale_celsius(celsius, HALF_STEP)
        if not 0 <= raw <= 0xFF:
            raise TemperatureDecodeError(
                f"{celsius} C ergibt Rohwert {raw}, passt nicht in ein Byte (0..255)"
            )
        return raw

    @staticmethod
    def encode_to_bytes(
        celsius: float, encoding: str = "u8", scale: float = HALF_STEP, bias: float = 0.0
    ) -> bytes:
        """Kodiert Grad Celsius in die Rohbytes der angegebenen Kodierung.

        >>> TemperatureDecoder.encode_to_bytes(50.0, "u16le")
        b'd\\x00'
        """
        width = TemperatureDecoder._encoding_width(encoding)
        raw = TemperatureDecoder._unscale_celsius(celsius, scale, bias)
        signed = encoding in _SIGNED_ENCODINGS
        try:
            return raw.to_bytes(width, "little", signed=signed)
        except OverflowError as exc:
            raise TemperatureDecodeError(
                f"{celsius} C ergibt Rohwert {raw}, passt nicht in {encoding}"
            ) from exc

    @staticmethod
    def _scale_raw(raw_value: int, scale: float, bias: float = 0.0) -> float:
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise TemperatureDecodeError(
                f"Rohwert muss ein int sein, ist {type(raw_value).__name__}: {raw_value!r}"
            )
        return raw_value * scale + bias

    @staticmethod
    def _unscale_celsius(celsius: float, scale: float, bias: float = 0.0) -> int:
        if isinstance(celsius, bool) or not isinstance(celsius, (int, float)):
            raise TemperatureDecodeError(
                f"Temperatur muss numerisch sein, ist {type(celsius).__name__}: {celsius!r}"
            )
        steps = (celsius - bias) / scale
        raw = round(steps)
        # Floating-Point-Toleranz: 0.5-Schritte sind binaer exakt, aber ein
        # Aufrufer kann z.B. 38.000000001 aus einer Berechnung hereinreichen.
        if abs(steps - raw) > 1e-9:
            raise TemperatureDecodeError(
                f"{celsius} C ist kein Vielfaches von {scale} C (Nullpunkt {bias}) "
                f"und nicht darstellbar"
            )
        return raw

    @staticmethod
    def _encoding_width(encoding: str) -> int:
        try:
            return _ENCODING_WIDTH[encoding]
        except KeyError:
            raise TemperatureDecodeError(
                f"Unbekannte Kodierung {encoding!r}; erlaubt: {sorted(_ENCODING_WIDTH)}"
            ) from None

    # ------------------------------------------------------------------
    # Zugriff auf Rohdaten
    # ------------------------------------------------------------------

    @staticmethod
    def read_raw(data: bytes, offset: int, encoding: str = "u8") -> int:
        """Liest den Rohwert an ``offset`` in der angegebenen Kodierung.

        Raises:
            TemperatureDecodeError: bei negativem Offset oder Lesen ueber das
                Pufferende hinaus.
        """
        width = TemperatureDecoder._encoding_width(encoding)
        if offset < 0:
            raise TemperatureDecodeError(f"Negativer Offset: {offset}")
        if offset + width > len(data):
            raise TemperatureDecodeError(
                f"Offset 0x{offset:02x} (+{width} Byte) liegt ausserhalb des "
                f"{len(data)}-Byte-Puffers"
            )
        return int.from_bytes(
            data[offset : offset + width], "little", signed=encoding in _SIGNED_ENCODINGS
        )

    @staticmethod
    def decode_at(
        data: bytes,
        offset: int,
        encoding: str = "u8",
        scale: float = HALF_STEP,
        bias: float = 0.0,
    ) -> float:
        """Liest ``offset`` und rechnet den Rohwert direkt in Grad Celsius um."""
        return TemperatureDecoder._scale_raw(
            TemperatureDecoder.read_raw(data, offset, encoding), scale, bias
        )

    # ------------------------------------------------------------------
    # Sollwert-Extraktion
    # ------------------------------------------------------------------

    @classmethod
    def extract_setpoints(
        cls, ht_cl_data: bytes, specs: Optional[Sequence[SetpointSpec]] = None
    ) -> Dict[str, float]:
        """Extrahiert die Sollwerte aus HT_CL.DAT.

        Args:
            ht_cl_data: Roher Inhalt von HT_CL.DAT (512 Byte).
            specs:      Abweichendes Layout; Standard ist :attr:`HT_CL_SETPOINTS`.

        Returns:
            Dict ``{"heating_setpoint": 38.0, "cooling_setpoint": 12.0, ...}``.
            Liegt ein Offset ausserhalb des Puffers, ist der Wert ``None``.

        Hinweis:
            Die Plausibilitaet der Werte steckt in
            :meth:`extract_setpoints_detailed`; diese Methode liefert bewusst
            die schlanke Form fuer Reports und Home-Assistant-Sensoren.
        """
        detailed = cls.extract_setpoints_detailed(ht_cl_data, specs)
        return {
            key: (setpoint.celsius if setpoint is not None else None)
            for key, setpoint in detailed.items()
        }

    @classmethod
    def extract_setpoints_detailed(
        cls, ht_cl_data: bytes, specs: Optional[Sequence[SetpointSpec]] = None
    ) -> Dict[str, Optional[Setpoint]]:
        """Wie :meth:`extract_setpoints`, aber mit voller Herkunftsinformation.

        Jeder :class:`Setpoint` traegt Rohwert, Hexdarstellung, Konfidenz und
        ein ``plausible``-Flag. Zusaetzlich liefert ``alternatives`` denselben
        Offset unter den anderen Kodierungen - solange die Byte-Breite nicht
        bestaetigt ist, zeigt genau das, welche Lesart sinnvolle Temperaturen
        ergibt.
        """
        cls._require_bytes(ht_cl_data)
        specs = specs if specs is not None else cls.HT_CL_SETPOINTS
        result: Dict[str, Optional[Setpoint]] = {}
        for spec in specs:
            result[spec.key] = cls._decode_setpoint(ht_cl_data, spec)
        return result

    @classmethod
    def _decode_setpoint(cls, data: bytes, spec: SetpointSpec) -> Optional[Setpoint]:
        try:
            raw = cls.read_raw(data, spec.offset, spec.encoding)
        except TemperatureDecodeError:
            # Offset ausserhalb des Puffers - z.B. verkuerzte Testdaten.
            return None

        celsius = cls._scale_raw(raw, spec.scale, spec.bias)
        chunk = data[spec.offset : spec.offset + spec.width]
        return Setpoint(
            key=spec.key,
            label=spec.label,
            offset=spec.offset,
            raw=raw,
            celsius=celsius,
            plausible=cls.is_plausible(celsius, spec.valid_range),
            confidence=spec.confidence,
            encoding=spec.encoding,
            hex=cls.format_hex(chunk),
            note=spec.note,
            alternatives=cls._alternative_readings(data, spec),
        )

    @classmethod
    def _alternative_readings(cls, data: bytes, spec: SetpointSpec) -> Dict[str, float]:
        """Denselben Offset unter allen anderen Kodierungen dekodieren."""
        alternatives: Dict[str, float] = {}
        for encoding in sorted(_ENCODING_WIDTH):
            if encoding == spec.encoding:
                continue
            try:
                alternatives[encoding] = cls.decode_at(
                    data, spec.offset, encoding, spec.scale, spec.bias
                )
            except TemperatureDecodeError:
                continue
        return alternatives

    # ------------------------------------------------------------------
    # Validierung
    # ------------------------------------------------------------------

    @staticmethod
    def is_plausible(celsius: float, valid_range: Tuple[float, float]) -> bool:
        """Prueft, ob ein Wert im erwarteten Fenster liegt (Grenzen inklusiv)."""
        low, high = valid_range
        return low <= celsius <= high

    @classmethod
    def validate_known_values(cls) -> Dict[str, object]:
        """Prueft die Dekodierung gegen die am Geraet abgelesenen Referenzwerte.

        Returns:
            ``{"ok": bool, "checks": [{"raw", "hex", "expected", "actual",
            "match", "roundtrip_ok"}, ...]}``. ``roundtrip_ok`` bestaetigt
            zusaetzlich, dass die Rueckkonvertierung den Rohwert reproduziert.
        """
        checks: List[Dict[str, object]] = []
        for raw, expected in sorted(cls.KNOWN_VALUES.items()):
            actual = cls.decode_0_5_degree_steps(raw)
            checks.append(
                {
                    "raw": raw,
                    "hex": f"0x{raw:02x}",
                    "expected": expected,
                    "actual": actual,
                    "match": actual == expected,
                    "roundtrip_ok": cls.encode_0_5_degree_steps(actual) == raw,
                }
            )
        ok = all(check["match"] and check["roundtrip_ok"] for check in checks)
        return {"ok": ok, "checks": checks}

    @classmethod
    def validate_setpoints(
        cls, ht_cl_data: bytes, specs: Optional[Sequence[SetpointSpec]] = None
    ) -> Dict[str, object]:
        """Bewertet die extrahierten Sollwerte gegen ihre Plausibilitaetsfenster.

        Returns:
            ``{"ok": bool, "issues": [...], "setpoints": {...}}``. ``ok`` ist
            nur dann ``True``, wenn jeder Sollwert lesbar und plausibel war -
            ein ``False`` ist das Signal, dass Offset oder Byte-Breite der
            Hypothese nicht stimmen.
        """
        detailed = cls.extract_setpoints_detailed(ht_cl_data, specs)
        issues: List[str] = []
        for key, setpoint in detailed.items():
            if setpoint is None:
                issues.append(f"{key}: Offset liegt ausserhalb der Datei")
                continue
            if not setpoint.plausible:
                issues.append(
                    f"{key}: {setpoint.celsius} C (roh {setpoint.raw}, {setpoint.hex}) "
                    f"liegt ausserhalb des erwarteten Bereichs"
                )
        return {
            "ok": not issues,
            "issues": issues,
            "setpoints": {
                key: (sp.as_dict() if sp is not None else None) for key, sp in detailed.items()
            },
        }

    # ------------------------------------------------------------------
    # Analysehilfen
    # ------------------------------------------------------------------

    @classmethod
    def find_temperature_candidates(
        cls,
        data: bytes,
        encoding: str = "u8",
        valid_range: Tuple[float, float] = (10.0, 70.0),
        scale: float = HALF_STEP,
        step: int = 1,
    ) -> List[TemperatureCandidate]:
        """Sucht Offsets, deren Rohwert eine plausible Temperatur ergibt.

        Das ist das Werkzeug, um die Offset-Hypothesen am echten Geraet zu
        pruefen: bekannten Sollwert am FTC4-Display einstellen, Datei sichern,
        scannen - der Offset, der genau diesen Wert liefert, ist der gesuchte.

        Args:
            step: Schrittweite des Scans; ``2`` fuer ein reines 16-Bit-Raster.
        """
        cls._require_bytes(data)
        if step < 1:
            raise TemperatureDecodeError(f"Schrittweite muss >= 1 sein, ist {step}")
        width = cls._encoding_width(encoding)
        candidates: List[TemperatureCandidate] = []
        for offset in range(0, len(data) - width + 1, step):
            raw = cls.read_raw(data, offset, encoding)
            if raw == 0:
                continue  # Padding, keine Temperatur
            celsius = cls._scale_raw(raw, scale)
            if cls.is_plausible(celsius, valid_range):
                candidates.append(
                    TemperatureCandidate(
                        offset=offset, raw=raw, celsius=celsius, encoding=encoding
                    )
                )
        return candidates

    @staticmethod
    def format_hex(data: bytes) -> str:
        """Formatiert Bytes als ``"4c 00"`` - gleiche Darstellung wie im Analyzer."""
        return " ".join(f"{byte:02x}" for byte in data)

    @classmethod
    def with_offsets(cls, **offsets: int) -> Tuple[SetpointSpec, ...]:
        """Kopiert das HT_CL-Layout mit korrigierten Offsets.

        Sobald ein Offset am Geraet bestaetigt ist::

            specs = TemperatureDecoder.with_offsets(heating_setpoint=0x06)
            TemperatureDecoder.extract_setpoints(data, specs)
        """
        known = {spec.key for spec in cls.HT_CL_SETPOINTS}
        unknown = set(offsets) - known
        if unknown:
            raise TemperatureDecodeError(
                f"Unbekannte Sollwert-Schluessel: {sorted(unknown)}; bekannt: {sorted(known)}"
            )
        return tuple(
            replace(spec, offset=offsets[spec.key]) if spec.key in offsets else spec
            for spec in cls.HT_CL_SETPOINTS
        )

    # ------------------------------------------------------------------
    # Datei-Ebene
    # ------------------------------------------------------------------

    @classmethod
    def decode_file(cls, path: "str | Path") -> Dict[str, object]:
        """Liest eine HT_CL.DAT und liefert einen kompletten Dekodier-Report."""
        file_path = Path(path)
        data = file_path.read_bytes()
        report = cls.validate_setpoints(data)
        report["filename"] = file_path.name
        report["size"] = len(data)
        report["size_ok"] = len(data) == cls.DAT_FILE_SIZE
        report["candidates"] = [
            candidate.as_dict() for candidate in cls.find_temperature_candidates(data)
        ]
        return report

    @staticmethod
    def _require_bytes(data: object) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TemperatureDecodeError(
                f"Erwarte bytes-artige Daten, bekomme {type(data).__name__}"
            )


def _print_report(report: Mapping[str, object]) -> None:
    """Gibt einen Report von :meth:`TemperatureDecoder.decode_file` aus."""
    print(f"Datei:  {report['filename']} ({report['size']} Byte)")
    if not report["size_ok"]:
        print(f"  Warnung: erwartet werden {TemperatureDecoder.DAT_FILE_SIZE} Byte")

    print("\nSollwerte (Offsets sind Hypothesen aus der Formatanalyse):")
    for key, setpoint in report["setpoints"].items():  # type: ignore[union-attr]
        if setpoint is None:
            print(f"  {key:<20} -- nicht lesbar (Offset ausserhalb der Datei)")
            continue
        flag = "ok" if setpoint["plausible"] else "UNPLAUSIBEL"
        print(
            f"  {setpoint['label']:<26} {setpoint['celsius']:>6.1f} C  "
            f"[{setpoint['offset']} = {setpoint['hex']}, roh {setpoint['raw']}, {flag}]"
        )
        if not setpoint["plausible"] and setpoint["alternatives"]:
            alt = ", ".join(f"{enc}={val} C" for enc, val in setpoint["alternatives"].items())
            print(f"  {'':<26} andere Lesarten: {alt}")

    issues = report["issues"]
    if issues:
        print("\nProbleme:")
        for issue in issues:  # type: ignore[union-attr]
            print(f"  - {issue}")

    candidates = report["candidates"]
    print(f"\nTemperaturverdaechtige Offsets (u8, 10-70 C): {len(candidates)}")  # type: ignore[arg-type]
    for candidate in candidates[:20]:  # type: ignore[index]
        print(f"  {candidate['offset']}: roh {candidate['raw']:>3} -> {candidate['celsius']} C")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``python -m src.temperature_decoder data/HT_CL.DAT``"""
    args = list(sys.argv[1:] if argv is None else argv)

    validation = TemperatureDecoder.validate_known_values()
    print("Validierung gegen bekannte Geraetewerte:")
    for check in validation["checks"]:  # type: ignore[union-attr]
        mark = "OK " if check["match"] else "FEHLER"
        print(
            f"  {mark} {check['hex']} (roh {check['raw']:>3}) -> {check['actual']} C "
            f"(erwartet {check['expected']} C)"
        )
    if not validation["ok"]:
        print("Dekodierung stimmt nicht mit den Referenzwerten ueberein!")
        return 1

    if not args:
        print("\nKeine Datei angegeben. Aufruf: python -m src.temperature_decoder <HT_CL.DAT>")
        return 0

    for path in args:
        print()
        try:
            _print_report(TemperatureDecoder.decode_file(path))
        except OSError as exc:
            print(f"Konnte {path} nicht lesen: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
