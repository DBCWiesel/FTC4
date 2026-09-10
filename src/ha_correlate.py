"""Log-Felder gegen Home-Assistant-Sensoren zuordnen.

Die FTC4-Logs sagen, *welche Zahl* auf welchem Byte steht -- aber nicht, was
sie bedeutet. Ein ESPHome-Modul an der Waermepumpe liefert genau die fehlende
Referenz: benannte Sensoren mit Zeitstempel. Wer beide Reihen ueber denselben
Zeitraum uebereinanderlegt, bekommt die Zuordnung geschenkt.

Ablauf:

1. Verlauf aus Home Assistant holen (siehe docs/HOME_ASSISTANT.md)::

       curl -H "Authorization: Bearer $TOKEN" \\
            "$HA/api/history/period/2026-09-09T20:36:00Z?end_time=..." > ha.json

2. Zuordnen lassen::

       python -m src.ha_correlate ha.json data/logs --utc-offset auto

Fuer jedes Logfeld werden die Sensoren nach Uebereinstimmung sortiert. Ein
Feld gilt als zugeordnet, wenn ein Sensor ueber den ganzen Zeitraum innerhalb
der Toleranz liegt -- ein einzelner Treffer zu einem Zeitpunkt kann Zufall
sein, ein Gleichlauf ueber Stunden nicht.

Das Ergebnis laesst sich direkt als Namensdatei fuer :mod:`src.log_decoder`
schreiben (``--namen namen.json``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.log_decoder import LOG_LAYOUT, FTC4Log, FieldSpec, LogDecodeError

__all__ = [
    "Sample",
    "MatchScore",
    "load_ha_history",
    "load_log_series",
    "align",
    "score_pair",
    "rank_matches",
    "render_report",
    "main",
]

#: Zustaende, die Home Assistant statt eines Messwerts liefern kann.
NON_NUMERIC_STATES = frozenset({"unavailable", "unknown", "none", "", "null"})

#: Standardtoleranz in Grad, innerhalb derer zwei Werte als gleich gelten.
#: 0.3 K liegt zwischen der Log-Aufloesung (0.01 K) und dem 0.5-K-Raster der
#: Statusbytes, faengt also Rundung ab, ohne benachbarte Fuehler zu verwischen.
DEFAULT_TOLERANCE = 0.3

Sample = Tuple[dt.datetime, float]


class CorrelateError(ValueError):
    """Fehler beim Einlesen oder Zuordnen der Reihen."""


@dataclass(frozen=True)
class MatchScore:
    """Wie gut ein HA-Sensor zu einem Logfeld passt."""

    field_key: str
    entity_id: str
    samples: int
    within_tolerance: float   # Anteil 0..1 der Punkte innerhalb der Toleranz
    median_abs_error: float   # robustes Hauptkriterium
    mean_abs_error: float
    max_abs_error: float
    correlation: Optional[float]
    offset: float             # mittlere Differenz Sensor minus Logfeld
    tolerance: float = DEFAULT_TOLERANCE

    @property
    def is_match(self) -> bool:
        """Deckungsgleich: typische Abweichung innerhalb der Toleranz.

        Bewertet wird der Median, nicht der Anteil innerhalb der Toleranz. HA
        und FTC4 tasten in unterschiedlichem Raster ab; an jedem Sprung
        entsteht dadurch kurz eine Abweichung. Der Median steckt das weg, ein
        echter Versatz nicht.
        """
        if self.samples < 10 or self.median_abs_error > self.tolerance:
            return False
        # Bewegt sich das Feld, muss auch der Verlauf passen.
        return self.correlation is None or self.correlation >= 0.9

    @property
    def is_shifted_match(self) -> bool:
        """Gleicher Verlauf, aber konstant versetzt -- z.B. Vor- und Ruecklauf."""
        return (
            self.samples >= 10
            and not self.is_match
            and self.correlation is not None
            and self.correlation >= 0.95
        )

    @property
    def confidence(self) -> str:
        """Wie belastbar der Treffer ist.

        Ein Gleichstand zweier konstanter Reihen ist kein Beweis -- dann passt
        jeder Sensor mit demselben Wert.
        """
        if not self.is_match:
            return "kein Treffer"
        return "hoch" if self.correlation is not None else "schwach (Reihe konstant)"

    def as_dict(self) -> Dict[str, object]:
        return {
            "field": self.field_key,
            "entity_id": self.entity_id,
            "samples": self.samples,
            "within_tolerance": round(self.within_tolerance, 4),
            "median_abs_error": round(self.median_abs_error, 3),
            "mean_abs_error": round(self.mean_abs_error, 3),
            "max_abs_error": round(self.max_abs_error, 3),
            "correlation": None if self.correlation is None else round(self.correlation, 4),
            "offset": round(self.offset, 3),
            "verdict": "treffer" if self.is_match
                       else ("gleichlauf_versetzt" if self.is_shifted_match else "kein_treffer"),
            "confidence": self.confidence,
        }


# ----------------------------------------------------------------------
# Einlesen
# ----------------------------------------------------------------------

def _parse_ha_timestamp(raw: str, utc_offset_hours: Optional[float]) -> Optional[dt.datetime]:
    """Wandelt einen HA-Zeitstempel in lokale, zeitzonenlose Zeit um.

    Home Assistant liefert ISO-8601 mit Zeitzone (meist UTC). Die FTC4 schreibt
    lokale Zeit ohne Zone. ``utc_offset_hours`` ueberbrueckt das.
    """
    try:
        stamp = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return stamp
    utc = stamp.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return utc + dt.timedelta(hours=utc_offset_hours or 0.0)


def load_ha_history(
    path: "str | Path", utc_offset_hours: Optional[float] = 0.0
) -> Dict[str, List[Sample]]:
    """Liest einen Export von ``/api/history/period`` ein.

    Erwartet wird die Struktur, die Home Assistant liefert: eine Liste von
    Listen, je eine je Entitaet, mit Objekten aus ``entity_id``, ``state`` und
    ``last_changed`` (oder ``last_updated``). Nicht numerische Zustaende
    (``unavailable``, ``unknown``) werden uebersprungen.

    ``?minimal_response`` wird unterstuetzt: dort traegt nur der erste Eintrag
    je Entitaet die ``entity_id``, die folgenden erben sie.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):           # manche Exporte packen alles in ein Objekt
        raw = list(raw.values())
    if not isinstance(raw, list):
        raise CorrelateError("Unerwartetes Format: erwartet wird die Liste aus /api/history/period")

    series: Dict[str, List[Sample]] = {}
    for entry in raw:
        states = entry if isinstance(entry, list) else [entry]
        # Mit ?minimal_response traegt nur der erste Eintrag je Entitaet die
        # entity_id; die folgenden erben sie. Ohne das Nachziehen gingen alle
        # Werte ausser dem ersten verloren.
        current_entity: Optional[str] = None
        for state in states:
            if not isinstance(state, dict):
                continue
            current_entity = state.get("entity_id") or current_entity
            entity = current_entity
            value_raw = state.get("state")
            when = state.get("last_changed") or state.get("last_updated")
            if not entity or not when or value_raw is None:
                continue
            if str(value_raw).strip().lower() in NON_NUMERIC_STATES:
                continue
            try:
                value = float(value_raw)
            except (TypeError, ValueError):
                continue
            stamp = _parse_ha_timestamp(str(when), utc_offset_hours)
            if stamp is None:
                continue
            series.setdefault(entity, []).append((stamp, value))

    for samples in series.values():
        samples.sort(key=lambda s: s[0])
    return series


def load_log_series(
    log_dir: "str | Path", layout: Sequence[FieldSpec] = LOG_LAYOUT
) -> Dict[str, List[Sample]]:
    """Liest alle Logs eines Verzeichnisses als Zeitreihe je Feld."""
    logs: List[FTC4Log] = []
    for path in sorted(Path(log_dir).rglob("*.LOG")) + sorted(Path(log_dir).rglob("*.log")):
        try:
            logs.append(FTC4Log.from_path(path))
        except (LogDecodeError, OSError):
            continue          # TEST.LOG und beschaedigte Dateien
    logs = [log for log in logs if log.timestamp is not None]
    if not logs:
        raise CorrelateError(f"Keine verwertbaren Logs in {log_dir}")
    logs.sort(key=lambda l: l.timestamp)

    return {
        spec.key: [(log.timestamp, log.decode_field(spec).value) for log in logs]
        for spec in layout
    }


# ----------------------------------------------------------------------
# Zuordnung
# ----------------------------------------------------------------------

def align(
    reference: Sequence[Sample], candidate: Sequence[Sample], max_gap_minutes: float = 10.0
) -> List[Tuple[dt.datetime, float, float]]:
    """Tastet ``candidate`` an den Zeitpunkten von ``reference`` ab.

    Genommen wird der zeitlich naechstgelegene Punkt, davor oder danach. Nur
    den letzten Wert davor zu halten waere unfair, wenn beide Seiten in
    unterschiedlichem Raster abtasten: an jedem Sprung entstuende eine
    Abweichung von der halben Rasterbreite, obwohl die Reihen identisch sind.

    Punkte, zu denen der naechste Kandidatenwert weiter als
    ``max_gap_minutes`` entfernt liegt, entfallen -- so liefert ein
    ausgefallener Sensor keine Scheintreffer.
    """
    if not reference or not candidate:
        return []
    aligned: List[Tuple[dt.datetime, float, float]] = []
    max_gap = dt.timedelta(minutes=max_gap_minutes)
    index = 0

    for stamp, ref_value in reference:
        while index + 1 < len(candidate) and candidate[index + 1][0] <= stamp:
            index += 1
        best = candidate[index]
        if index + 1 < len(candidate):
            following = candidate[index + 1]
            if abs(following[0] - stamp) < abs(best[0] - stamp):
                best = following
        if abs(best[0] - stamp) > max_gap:
            continue
        aligned.append((stamp, ref_value, best[1]))
    return aligned


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Korrelationskoeffizient; None, wenn eine Reihe konstant ist."""
    if len(xs) < 3:
        return None
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = (sum(v * v for v in dx) ** 0.5) * (sum(v * v for v in dy) ** 0.5)
    if denom == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denom


def score_pair(
    field_key: str,
    entity_id: str,
    field_series: Sequence[Sample],
    entity_series: Sequence[Sample],
    tolerance: float = DEFAULT_TOLERANCE,
) -> Optional[MatchScore]:
    """Bewertet, wie gut ein Sensor zu einem Logfeld passt."""
    aligned = align(field_series, entity_series)
    if len(aligned) < 3:
        return None
    field_values = [row[1] for row in aligned]
    entity_values = [row[2] for row in aligned]
    errors = [abs(a - b) for a, b in zip(field_values, entity_values)]
    return MatchScore(
        field_key=field_key,
        entity_id=entity_id,
        samples=len(aligned),
        within_tolerance=sum(e <= tolerance for e in errors) / len(errors),
        median_abs_error=statistics.median(errors),
        mean_abs_error=statistics.fmean(errors),
        max_abs_error=max(errors),
        correlation=_pearson(field_values, entity_values),
        offset=statistics.fmean(b - a for a, b in zip(field_values, entity_values)),
        tolerance=tolerance,
    )


def rank_matches(
    log_series: Dict[str, List[Sample]],
    ha_series: Dict[str, List[Sample]],
    tolerance: float = DEFAULT_TOLERANCE,
    top: int = 3,
) -> Dict[str, List[MatchScore]]:
    """Sortiert je Logfeld die Sensoren nach Uebereinstimmung."""
    ranked: Dict[str, List[MatchScore]] = {}
    for field_key, field_samples in log_series.items():
        scores = [
            score
            for entity_id, entity_samples in ha_series.items()
            if (score := score_pair(field_key, entity_id, field_samples, entity_samples,
                                    tolerance)) is not None
        ]
        scores.sort(
            key=lambda s: (s.median_abs_error, -(s.correlation or -1.0), s.mean_abs_error)
        )
        ranked[field_key] = scores[:top]
    return ranked


def detect_utc_offset(
    ha_path: "str | Path",
    log_series: Dict[str, List[Sample]],
    candidates: Iterable[float] = range(-12, 15),
    tolerance: float = DEFAULT_TOLERANCE,
) -> Tuple[float, int]:
    """Ermittelt den Zeitversatz, bei dem die meisten Felder zusammenpassen.

    Die FTC4 schreibt lokale Zeit, Home Assistant liefert UTC. Statt den
    Versatz raten zu lassen, wird er durchprobiert: der Wert mit den meisten
    Treffern gewinnt.

    Returns:
        ``(offset_in_stunden, anzahl_treffer)``.
    """
    best_offset, best_hits, best_quality = 0.0, 0, float("-inf")
    for offset in candidates:
        ha_series = load_ha_history(ha_path, float(offset))
        if not ha_series:
            continue
        ranked = rank_matches(log_series, ha_series, tolerance, top=1)
        best_per_field = [scores[0] for scores in ranked.values() if scores]
        if not best_per_field:
            continue
        hits = sum(score.is_match for score in best_per_field)
        # Bei null Treffern entscheidet der Gleichlauf: so findet das Verfahren
        # auch dann den richtigen Versatz, wenn die Toleranz zu eng gewaehlt ist.
        quality = (hits, sum(s.correlation or 0.0 for s in best_per_field))
        if quality > (best_hits, best_quality):
            best_offset, best_hits, best_quality = float(offset), hits, quality[1]
    return best_offset, best_hits


# ----------------------------------------------------------------------
# Ausgabe
# ----------------------------------------------------------------------

def render_report(
    ranked: Dict[str, List[MatchScore]],
    ha_series: Dict[str, List[Sample]],
    log_series: Dict[str, List[Sample]],
    utc_offset: float,
    tolerance: float,
) -> str:
    """Klartext-Bericht der Zuordnung."""
    out: List[str] = []
    add = out.append

    add("=" * 78)
    add("ZUORDNUNG LOGFELDER <-> HOME-ASSISTANT-SENSOREN")
    add("=" * 78)
    add(f"HA-Sensoren     : {len(ha_series)}")
    add(f"Logfelder       : {len(log_series)}")
    add(f"Zeitversatz     : {utc_offset:+.0f} h auf die HA-Zeitstempel")
    add(f"Toleranz        : {tolerance} K")

    matched = {k: v for k, v in ranked.items() if v and v[0].is_match}
    shifted = {k: v for k, v in ranked.items()
               if v and not v[0].is_match and v[0].is_shifted_match}

    add("")
    add("-" * 78)
    add(f"ZUGEORDNET ({len(matched)})  -- Sensor und Logfeld laufen deckungsgleich")
    add("-" * 78)
    if matched:
        add(f"  {'Logfeld':<20} {'Sensor':<40} {'Punkte':>7} {'Median':>7}  Guete")
        for key, scores in matched.items():
            best = scores[0]
            add(f"  {key:<20} {best.entity_id:<40} {best.samples:7d} "
                f"{best.median_abs_error:7.2f}  {best.confidence}")
    else:
        add("  keine -- siehe Hinweise unten")

    add("")
    add("-" * 78)
    add(f"GLEICHLAUF MIT VERSATZ ({len(shifted)})  -- verwandte Groesse, nicht dieselbe")
    add("-" * 78)
    if shifted:
        add(f"  {'Logfeld':<20} {'Sensor':<40} {'Korr':>6} {'Versatz':>8}")
        for key, scores in shifted.items():
            best = scores[0]
            add(f"  {key:<20} {best.entity_id:<40} {best.correlation:6.3f} "
                f"{best.offset:+8.2f}")
    else:
        add("  keine")

    add("")
    add("-" * 78)
    add("ALLE FELDER MIT DEN BESTEN KANDIDATEN")
    add("-" * 78)
    for key, scores in ranked.items():
        values = {v for _, v in log_series.get(key, [])}
        constant = "  (Feld ist konstant -- Zuordnung nicht belastbar)" if len(values) <= 1 else ""
        add(f"  {key}{constant}")
        if not scores:
            add("      keine Kandidaten mit genug ueberlappenden Punkten")
            continue
        for score in scores:
            corr = "  --  " if score.correlation is None else f"{score.correlation:6.3f}"
            add(f"      {score.entity_id:<40} Median {score.median_abs_error:6.2f}  "
                f"Korr {corr}  in Toleranz {score.within_tolerance:5.1%}")

    add("")
    add("-" * 78)
    add("HINWEIS")
    add("-" * 78)
    add("  Ein Feld, das ueber den ganzen Zeitraum konstant ist, laesst sich so")
    add("  nicht zuordnen -- dafuer muss der Wert sich bewegen. Am besten einen")
    add("  Zeitraum waehlen, in dem die Anlage arbeitet (WW-Bereitung, Heizen).")
    add("  Passt gar nichts, stimmt meist der Zeitversatz nicht: --utc-offset auto")
    return "\n".join(out)


def build_names(ranked: Dict[str, List[MatchScore]]) -> Dict[str, str]:
    """Namensdatei fuer :mod:`src.log_decoder` aus den sicheren Treffern."""
    return {
        key: scores[0].entity_id
        for key, scores in ranked.items()
        if scores and scores[0].is_match and scores[0].correlation is not None
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``python -m src.ha_correlate ha.json data/logs [--utc-offset auto]``"""
    parser = argparse.ArgumentParser(
        prog="python -m src.ha_correlate",
        description="Ordnet FTC4-Logfelder den Sensoren aus Home Assistant zu.",
    )
    parser.add_argument("ha_history", metavar="HA_JSON",
                        help="Export von /api/history/period")
    parser.add_argument("log_dir", metavar="LOG_VERZEICHNIS",
                        help="Verzeichnis mit den *.LOG-Dateien")
    parser.add_argument("--utc-offset", default="auto",
                        help="Stunden, die auf die HA-Zeitstempel addiert werden, "
                             "oder 'auto' (Standard)")
    parser.add_argument("--toleranz", type=float, default=DEFAULT_TOLERANCE,
                        help=f"Abweichung in K, die noch als gleich gilt "
                             f"(Standard {DEFAULT_TOLERANCE})")
    parser.add_argument("--namen", metavar="DATEI",
                        help="sichere Treffer als Namensdatei fuer src.log_decoder schreiben")
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        log_series = load_log_series(args.log_dir)
    except CorrelateError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        if str(args.utc_offset).lower() == "auto":
            offset, hits = detect_utc_offset(args.ha_history, log_series, tolerance=args.toleranz)
            print(f"Zeitversatz automatisch bestimmt: {offset:+.0f} h "
                  f"({hits} Felder passen)", file=sys.stderr)
        else:
            offset = float(args.utc_offset)
        ha_series = load_ha_history(args.ha_history, offset)
    except (CorrelateError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"HA-Verlauf nicht verwendbar: {exc}", file=sys.stderr)
        return 2

    if not ha_series:
        print("Keine numerischen Sensorwerte im HA-Export gefunden.", file=sys.stderr)
        return 1

    ranked = rank_matches(log_series, ha_series, args.toleranz)

    if args.json:
        print(json.dumps(
            {key: [s.as_dict() for s in scores] for key, scores in ranked.items()},
            indent=2, ensure_ascii=False))
    else:
        print(render_report(ranked, ha_series, log_series, offset, args.toleranz))

    if args.namen:
        names = build_names(ranked)
        Path(args.namen).write_text(
            json.dumps(names, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{len(names)} sichere Treffer geschrieben nach {args.namen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
