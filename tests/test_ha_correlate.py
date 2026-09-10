"""Tests fuer die Zuordnung Logfelder <-> Home-Assistant-Sensoren."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import build_log
from src.ha_correlate import (
    DEFAULT_TOLERANCE,
    CorrelateError,
    MatchScore,
    align,
    build_names,
    detect_utc_offset,
    load_ha_history,
    load_log_series,
    main,
    rank_matches,
    render_report,
    score_pair,
)

BASE = dt.datetime(2026, 9, 9, 22, 0)


def ha_export(entity: str, values, start_utc=dt.datetime(2026, 9, 9, 20, 0), step_min=1):
    """Baut einen HA-Verlauf im Format von /api/history/period."""
    return [
        {
            "entity_id": entity,
            "state": str(value),
            "last_changed": (start_utc + dt.timedelta(minutes=i * step_min))
            .replace(tzinfo=dt.timezone.utc)
            .isoformat(),
        }
        for i, value in enumerate(values)
    ]


def samples(values, start=BASE, step_min=1):
    return [(start + dt.timedelta(minutes=i * step_min), v) for i, v in enumerate(values)]


class TestLoadHaHistory:
    def test_reads_nested_lists(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([ha_export("sensor.a", [1.0, 2.0])]), encoding="utf-8")
        series = load_ha_history(path, utc_offset_hours=2.0)
        assert list(series) == ["sensor.a"]
        assert series["sensor.a"][0] == (dt.datetime(2026, 9, 9, 22, 0), 1.0)

    def test_applies_utc_offset(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([ha_export("sensor.a", [1.0])]), encoding="utf-8")
        assert load_ha_history(path, 0.0)["sensor.a"][0][0] == dt.datetime(2026, 9, 9, 20, 0)
        assert load_ha_history(path, 2.0)["sensor.a"][0][0] == dt.datetime(2026, 9, 9, 22, 0)

    @pytest.mark.parametrize("state", ["unavailable", "unknown", "", "an", None])
    def test_skips_non_numeric_states(self, tmp_path, state):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([[{
            "entity_id": "sensor.a", "state": state,
            "last_changed": "2026-09-09T20:00:00+00:00",
        }]]), encoding="utf-8")
        assert load_ha_history(path) == {}

    def test_accepts_last_updated(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([[{
            "entity_id": "sensor.a", "state": "5.5",
            "last_updated": "2026-09-09T20:00:00+00:00",
        }]]), encoding="utf-8")
        assert load_ha_history(path)["sensor.a"][0][1] == 5.5

    def test_sorts_by_time(self, tmp_path):
        path = tmp_path / "ha.json"
        entries = ha_export("sensor.a", [1.0, 2.0, 3.0])
        path.write_text(json.dumps([list(reversed(entries))]), encoding="utf-8")
        stamps = [s for s, _ in load_ha_history(path)["sensor.a"]]
        assert stamps == sorted(stamps)

    def test_rejects_wrong_shape(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text('"nur ein string"', encoding="utf-8")
        with pytest.raises(CorrelateError, match="Unerwartetes Format"):
            load_ha_history(path)


class TestAlign:
    def test_picks_nearest_sample(self):
        reference = samples([1.0, 2.0, 3.0])
        candidate = [(BASE + dt.timedelta(seconds=50), 9.0)]
        aligned = align(reference, candidate)
        # 22:00:50 liegt naeher an 22:01 als an 22:00
        assert [row[0] for row in aligned] == [s for s, _ in reference]
        assert all(row[2] == 9.0 for row in aligned)

    def test_drops_points_beyond_max_gap(self):
        reference = samples([1.0] * 30)
        candidate = [(BASE, 5.0)]
        aligned = align(reference, candidate, max_gap_minutes=5)
        assert len(aligned) == 6          # 22:00 bis 22:05

    def test_empty_inputs(self):
        assert align([], samples([1.0])) == []
        assert align(samples([1.0]), []) == []

    def test_coarser_candidate_grid_stays_close(self):
        """Groeberes Raster darf den typischen Abstand nicht aufblaehen.

        Bei 3-Minuten-Raster und 1.0 je Minute Steigung liegt der naechste
        Kandidat hoechstens 1.5 Minuten daneben -- am Reihenende, wo kein
        spaeterer Punkt mehr folgt, entsprechend mehr. Entscheidend fuer die
        Bewertung ist der Median, und der muss klein bleiben.
        """
        import statistics

        reference = samples([float(i) for i in range(30)])
        candidate = samples([float(i) for i in range(30)])[::3]
        errors = [abs(row[1] - row[2]) for row in align(reference, candidate)]
        assert statistics.median(errors) <= 1.0
        assert max(errors) <= 2.0


class TestScorePair:
    def test_identical_series_is_match(self):
        series = samples([20.0 + i * 0.1 for i in range(40)])
        score = score_pair("f", "sensor.a", series, series)
        assert score.median_abs_error == 0.0
        assert score.correlation == pytest.approx(1.0)
        assert score.is_match is True
        assert score.confidence == "hoch"

    def test_offset_series_is_shifted_match(self):
        base = samples([20.0 + i * 0.1 for i in range(40)])
        shifted = [(s, v - 1.5) for s, v in base]
        score = score_pair("f", "sensor.a", base, shifted)
        assert score.is_match is False
        assert score.is_shifted_match is True
        assert score.offset == pytest.approx(-1.5)

    def test_unrelated_series_is_no_match(self):
        a = samples([20.0 + i * 0.1 for i in range(40)])
        b = samples([50.0 - i * 0.3 for i in range(40)])
        score = score_pair("f", "sensor.a", a, b)
        assert score.is_match is False
        assert score.is_shifted_match is False

    def test_constant_match_is_flagged_as_weak(self):
        """Zwei konstante Reihen auf demselben Wert sind kein Beweis."""
        series = samples([22.0] * 40)
        score = score_pair("f", "sensor.a", series, series)
        assert score.correlation is None
        assert score.is_match is True
        assert "schwach" in score.confidence

    def test_too_few_points_returns_none(self):
        assert score_pair("f", "sensor.a", samples([1.0]), samples([1.0])) is None

    def test_tolerance_is_respected(self):
        base = samples([20.0] * 40)
        near = [(s, v + 0.2) for s, v in base]
        assert score_pair("f", "s", base, near, tolerance=0.3).is_match is True
        assert score_pair("f", "s", base, near, tolerance=0.1).is_match is False

    def test_as_dict_is_json_serialisable(self):
        series = samples([20.0 + i * 0.1 for i in range(40)])
        payload = score_pair("f", "sensor.a", series, series).as_dict()
        assert json.loads(json.dumps(payload))["verdict"] == "treffer"


class TestLogSeries:
    @pytest.fixture
    def log_dir(self, tmp_path):
        for minute in range(20):
            value = 2000 + minute * 50          # 20.00 C, dann 0.5 C je Minute
            (tmp_path / f"22{minute:02d}00.LOG").write_bytes(
                build_log({0x4E: value.to_bytes(2, "little")}, hour=22, minute=minute)
            )
        (tmp_path / "TEST.LOG").write_bytes(bytes(range(16)))
        return tmp_path

    def test_reads_series_per_field(self, log_dir):
        series = load_log_series(log_dir)
        assert len(series["soll01"]) == 20
        assert series["soll01"][0][1] == 20.0
        assert series["soll01"][1][1] == 20.5

    def test_skips_card_test_file(self, log_dir):
        assert all(len(v) == 20 for v in load_log_series(log_dir).values())

    def test_raises_on_empty_directory(self, tmp_path):
        with pytest.raises(CorrelateError, match="Keine verwertbaren Logs"):
            load_log_series(tmp_path)


class TestEndToEnd:
    """Der eigentliche Anwendungsfall: Sensor im HA-Export wiederfinden."""

    @pytest.fixture
    def log_dir(self, tmp_path):
        target = tmp_path / "logs"
        target.mkdir()
        for minute in range(40):
            raw = 4400 - minute * 10          # 44.00 C, fallend
            (target / f"22{minute:02d}00.LOG").write_bytes(
                build_log({0x4E: raw.to_bytes(2, "little")}, hour=22, minute=minute)
            )
        return target

    @pytest.fixture
    def ha_json(self, tmp_path):
        values = [round(44.00 - m * 0.10, 2) for m in range(40)]
        noise = [round(v + 0.02, 2) for v in values]
        export = [
            ha_export("sensor.speicher", noise),
            ha_export("sensor.ruecklauf", [round(v - 3.0, 2) for v in values]),
            ha_export("sensor.konstant", [15.0] * 40),
        ]
        path = tmp_path / "ha.json"
        path.write_text(json.dumps(export), encoding="utf-8")
        return path

    def test_detects_utc_offset(self, ha_json, log_dir):
        offset, hits = detect_utc_offset(ha_json, load_log_series(log_dir))
        assert offset == 2.0
        assert hits >= 1

    def test_finds_the_right_sensor(self, ha_json, log_dir):
        ranked = rank_matches(load_log_series(log_dir), load_ha_history(ha_json, 2.0))
        best = ranked["soll01"][0]
        assert best.entity_id == "sensor.speicher"
        assert best.is_match is True

    def test_offset_sensor_ranks_as_shifted(self, ha_json, log_dir):
        ranked = rank_matches(load_log_series(log_dir), load_ha_history(ha_json, 2.0))
        by_entity = {s.entity_id: s for s in ranked["soll01"]}
        assert by_entity["sensor.ruecklauf"].is_match is False
        assert by_entity["sensor.ruecklauf"].is_shifted_match is True

    def test_wrong_offset_finds_nothing(self, ha_json, log_dir):
        ranked = rank_matches(load_log_series(log_dir), load_ha_history(ha_json, 0.0))
        assert not any(scores and scores[0].is_match for scores in ranked.values())

    def test_build_names_only_takes_strong_matches(self, ha_json, log_dir):
        ranked = rank_matches(load_log_series(log_dir), load_ha_history(ha_json, 2.0))
        names = build_names(ranked)
        assert names["soll01"] == "sensor.speicher"
        # Konstante Felder liefern keine belastbare Zuordnung.
        assert "soll04" not in names

    def test_report_mentions_constant_fields(self, ha_json, log_dir):
        log_series = load_log_series(log_dir)
        ha_series = load_ha_history(ha_json, 2.0)
        text = render_report(rank_matches(log_series, ha_series), ha_series,
                             log_series, 2.0, DEFAULT_TOLERANCE)
        assert "ZUGEORDNET" in text
        assert "Feld ist konstant" in text


class TestCli:
    @pytest.fixture
    def setup(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        for minute in range(40):
            raw = 4400 - minute * 10
            (logs / f"22{minute:02d}00.LOG").write_bytes(
                build_log({0x4E: raw.to_bytes(2, "little")}, hour=22, minute=minute)
            )
        ha = tmp_path / "ha.json"
        ha.write_text(json.dumps([
            ha_export("sensor.speicher", [round(44.0 - m * 0.1, 2) for m in range(40)])
        ]), encoding="utf-8")
        return ha, logs

    def test_runs_with_auto_offset(self, setup, capsys):
        ha, logs = setup
        assert main([str(ha), str(logs)]) == 0
        captured = capsys.readouterr()
        assert "sensor.speicher" in captured.out
        assert "+2 h" in captured.err

    def test_explicit_offset(self, setup, capsys):
        ha, logs = setup
        assert main([str(ha), str(logs), "--utc-offset", "2"]) == 0
        assert "ZUGEORDNET (1)" in capsys.readouterr().out

    def test_json_output(self, setup, capsys):
        ha, logs = setup
        assert main([str(ha), str(logs), "--utc-offset", "2", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["soll01"][0]["verdict"] == "treffer"

    def test_writes_names_file(self, setup, tmp_path, capsys):
        ha, logs = setup
        target = tmp_path / "namen.json"
        assert main([str(ha), str(logs), "--utc-offset", "2", "--namen", str(target)]) == 0
        assert json.loads(target.read_text(encoding="utf-8"))["soll01"] == "sensor.speicher"

    def test_missing_logs(self, tmp_path, setup, capsys):
        ha, _ = setup
        assert main([str(ha), str(tmp_path / "leer")]) == 1
        assert "Keine verwertbaren Logs" in capsys.readouterr().err

    def test_broken_ha_file(self, setup, tmp_path, capsys):
        _, logs = setup
        bad = tmp_path / "kaputt.json"
        bad.write_text("{nicht json", encoding="utf-8")
        assert main([str(bad), str(logs), "--utc-offset", "0"]) == 2
        assert "nicht verwendbar" in capsys.readouterr().err


class TestMinimalResponse:
    """HA laesst mit ?minimal_response die entity_id in Folgeeintraegen weg."""

    def test_entity_id_is_carried_forward(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([[
            {"entity_id": "sensor.a", "state": "20.0",
             "last_changed": "2026-09-09T20:00:00+00:00"},
            {"state": "20.5", "last_changed": "2026-09-09T20:01:00+00:00"},
            {"state": "21.0", "last_changed": "2026-09-09T20:02:00+00:00"},
        ]]), encoding="utf-8")
        series = load_ha_history(path, 2.0)
        assert list(series) == ["sensor.a"]
        assert [v for _, v in series["sensor.a"]] == [20.0, 20.5, 21.0]

    def test_entity_ids_do_not_leak_between_lists(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([
            [{"entity_id": "sensor.a", "state": "1.0",
              "last_changed": "2026-09-09T20:00:00+00:00"}],
            [{"entity_id": "sensor.b", "state": "2.0",
              "last_changed": "2026-09-09T20:00:00+00:00"},
             {"state": "2.5", "last_changed": "2026-09-09T20:01:00+00:00"}],
        ]), encoding="utf-8")
        series = load_ha_history(path, 2.0)
        assert len(series["sensor.a"]) == 1
        assert len(series["sensor.b"]) == 2

    def test_leading_entry_without_entity_id_is_skipped(self, tmp_path):
        path = tmp_path / "ha.json"
        path.write_text(json.dumps([[
            {"state": "20.0", "last_changed": "2026-09-09T20:00:00+00:00"},
        ]]), encoding="utf-8")
        assert load_ha_history(path) == {}
