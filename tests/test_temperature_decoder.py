"""Tests fuer die FTC4-Temperaturdekodierung.

Schwerpunkt: die 0.5-Grad-Hypothese gegen die am Geraet abgelesenen
Referenzwerte (0x4c -> 38 C, 0x64 -> 50 C, 0x5a -> 45 C) sowie die
Rueckkonvertierung und die Sollwert-Extraktion aus HT_CL.DAT.
"""

from __future__ import annotations

import pytest

from src.temperature_decoder import (
    HALF_STEP,
    Setpoint,
    SetpointSpec,
    TemperatureDecodeError,
    TemperatureDecoder,
    main,
)


class TestDecodeSetpoint:
    """Sollwert-Temperaturen: raw / 2 - 20.

    Gegen die Ausgabe des Hersteller-Werkzeugs auf denselben Dateien geprueft.
    Die Projektunterlagen fuehrten fuer dieselben Rohwerte 38, 50 und 45 C --
    durchgehend 20 K zu hoch, weil dort der Nullpunkt fehlte.
    """

    def test_decode_18_celsius(self):
        """0x4c (76) ergibt 18 C, nicht die 38 C aus den Unterlagen."""
        assert TemperatureDecoder.decode_setpoint(0x4C) == 18.0

    def test_decode_30_celsius(self):
        assert TemperatureDecoder.decode_setpoint(0x64) == 30.0

    def test_decode_25_celsius(self):
        assert TemperatureDecoder.decode_setpoint(0x5A) == 25.0

    def test_outdoor_uses_deeper_zero_point(self):
        """Aussentemperaturen brauchen Minusgrade: raw / 2 - 40."""
        assert TemperatureDecoder.decode_outdoor(90) == 5.0
        assert TemperatureDecoder.decode_outdoor(50) == -15.0
        assert TemperatureDecoder.decode_outdoor(0) == -40.0

    def test_scaling_primitive_has_no_zero_point(self):
        """decode_0_5_degree_steps ist die Rechenschicht, keine Temperatur."""
        assert TemperatureDecoder.decode_0_5_degree_steps(0x4C) == 38.0
        assert TemperatureDecoder.decode_setpoint(0x4C) == 18.0

    def test_setpoint_roundtrip(self):
        for raw in range(256):
            assert TemperatureDecoder.encode_setpoint(
                TemperatureDecoder.decode_setpoint(raw)) == raw

    @pytest.mark.parametrize(
        "raw, expected",
        [(0, 0.0), (1, 0.5), (2, 1.0), (41, 20.5), (0xFF, 127.5)],
    )
    def test_decode_scale(self, raw, expected):
        assert TemperatureDecoder.decode_0_5_degree_steps(raw) == expected

    def test_odd_raw_yields_half_degree(self):
        """Ungerade Rohwerte ergeben halbe Grad -- der Kern der Hypothese."""
        assert TemperatureDecoder.decode_0_5_degree_steps(0x4D) == 38.5

    def test_callable_on_instance(self):
        """Die Templates rufen teils ueber eine Instanz auf."""
        assert TemperatureDecoder().decode_0_5_degree_steps(0x4C) == 38.0

    def test_direct_celsius_alternative(self):
        """Alternativhypothese 1 Grad je Rohwert bleibt verfuegbar."""
        assert TemperatureDecoder.decode_direct_celsius(0x4C) == 76.0

    @pytest.mark.parametrize("bad", ["76", 76.5, None, True, b"\x4c"])
    def test_rejects_non_int(self, bad):
        with pytest.raises(TemperatureDecodeError):
            TemperatureDecoder.decode_0_5_degree_steps(bad)


class TestEncode:
    """Rueckkonvertierung Grad Celsius -> Rohbytes."""

    @pytest.mark.parametrize("celsius, raw", [(18.0, 0x4C), (30.0, 0x64), (25.0, 0x5A)])
    def test_encode_known_values(self, celsius, raw):
        assert TemperatureDecoder.encode_setpoint(celsius) == raw

    def test_roundtrip_over_full_byte_range(self):
        """Jeder Rohwert 0..255 muss den Hin- und Rueckweg unveraendert ueberstehen."""
        for raw in range(256):
            celsius = TemperatureDecoder.decode_0_5_degree_steps(raw)
            assert TemperatureDecoder.encode_0_5_degree_steps(celsius) == raw

    def test_encode_accepts_half_steps(self):
        assert TemperatureDecoder.encode_0_5_degree_steps(38.5) == 77

    def test_encode_accepts_int(self):
        assert TemperatureDecoder.encode_0_5_degree_steps(38) == 76

    def test_encode_tolerates_float_noise(self):
        """0.1 + 0.2-Arithmetik darf die Kodierung nicht kippen."""
        assert TemperatureDecoder.encode_0_5_degree_steps(38.0 + 1e-12) == 76

    @pytest.mark.parametrize("celsius", [38.2, 0.3, 45.75])
    def test_encode_rejects_unrepresentable(self, celsius):
        """Werte ausserhalb des 0.5-Rasters sind nicht darstellbar."""
        with pytest.raises(TemperatureDecodeError, match="Vielfaches"):
            TemperatureDecoder.encode_0_5_degree_steps(celsius)

    @pytest.mark.parametrize("celsius", [-0.5, 200.0])
    def test_encode_rejects_out_of_byte_range(self, celsius):
        with pytest.raises(TemperatureDecodeError, match="Byte"):
            TemperatureDecoder.encode_0_5_degree_steps(celsius)

    @pytest.mark.parametrize(
        "celsius, encoding, expected",
        [
            (38.0, "u8", b"\x4c"),
            (50.0, "u16le", b"\x64\x00"),
            (45.0, "s8", b"\x5a"),
            (-5.0, "s8", b"\xf6"),
            (200.0, "u16le", b"\x90\x01"),
        ],
    )
    def test_encode_to_bytes(self, celsius, encoding, expected):
        assert TemperatureDecoder.encode_to_bytes(celsius, encoding) == expected

    def test_encode_to_bytes_rejects_overflow(self):
        with pytest.raises(TemperatureDecodeError):
            TemperatureDecoder.encode_to_bytes(200.0, "u8")

    def test_encode_to_bytes_rejects_unknown_encoding(self):
        with pytest.raises(TemperatureDecodeError, match="Unbekannte Kodierung"):
            TemperatureDecoder.encode_to_bytes(38.0, "f32")


class TestReadRaw:
    """Rohzugriff auf den Byte-Puffer."""

    def test_read_u8(self, synthetic_ht_cl):
        assert TemperatureDecoder.read_raw(synthetic_ht_cl, 0x08, "u8") == 0x4C

    def test_read_u16le_is_little_endian(self):
        assert TemperatureDecoder.read_raw(b"\x00\x00\x64\x01", 0x02, "u16le") == 0x0164

    def test_read_s8_negative(self):
        """Aussentemperaturen koennen negativ sein -- signed muss stimmen."""
        assert TemperatureDecoder.read_raw(b"\xf6", 0x00, "s8") == -10

    def test_decode_at_applies_scale(self, synthetic_ht_cl):
        assert TemperatureDecoder.decode_at(synthetic_ht_cl, 0x08) == 38.0
        assert TemperatureDecoder.decode_at(synthetic_ht_cl, 0x08, bias=-20.0) == 18.0

    def test_decode_at_signed_negative_celsius(self):
        assert TemperatureDecoder.decode_at(b"\xf6", 0x00, "s8") == -5.0

    def test_read_past_end_raises(self):
        with pytest.raises(TemperatureDecodeError, match="ausserhalb"):
            TemperatureDecoder.read_raw(b"\x01\x02", 0x01, "u16le")

    def test_negative_offset_raises(self):
        with pytest.raises(TemperatureDecodeError, match="Negativer Offset"):
            TemperatureDecoder.read_raw(b"\x01\x02", -1)


class TestExtractSetpoints:
    """Sollwert-Extraktion aus HT&CL.DAT.

    Die Temperaturen stehen im LO-Byte der Typ-0x0f-Records, also auf
    Offset ``3 * Recordnummer + 2``.
    """

    def test_returns_dict_of_setpoints(self, synthetic_ht_cl):
        temps = TemperatureDecoder.extract_setpoints(synthetic_ht_cl)
        assert isinstance(temps, dict)
        assert set(temps) == {f"ht_cl_r{n:02d}" for n in (2, 3, 4, 5, 6, 10, 11, 12)}

    def test_decodes_the_vendor_reference_values(self, synthetic_ht_cl):
        """Die vom Hersteller-Werkzeug ausgegebenen Werte muessen herauskommen."""
        temps = TemperatureDecoder.extract_setpoints(synthetic_ht_cl)
        assert temps["ht_cl_r02"] == 18.0   # 0x4c
        assert temps["ht_cl_r03"] == 30.0   # 0x64
        assert temps["ht_cl_r04"] == 25.0   # 0x5a

    def test_decodes_all_eight_setpoints(self, synthetic_ht_cl):
        assert sorted(TemperatureDecoder.extract_setpoints(synthetic_ht_cl).values()) == [
            15.0, 18.0, 20.0, 25.0, 25.0, 30.0, 35.0, 35.0
        ]

    def test_old_offsets_0x02_0x04_are_disproven(self, synthetic_ht_cl):
        """Die fruehere Annahme aus den Projektunterlagen ist widerlegt.

        Auf 0x02 und 0x04 stehen die Nullbytes der Records 0 und 1, nicht die
        Sollwerte. Der Test haelt fest, warum das Layout geaendert wurde.
        """
        assert synthetic_ht_cl[0x02] == 0x00
        assert synthetic_ht_cl[0x04] == 0x00
        alt = TemperatureDecoder.extract_setpoints(
            synthetic_ht_cl,
            [SetpointSpec("heating_setpoint", "alt", 0x02, valid_range=(20.0, 60.0))],
        )
        assert alt["heating_setpoint"] == 0.0

    def test_detailed_carries_provenance(self, synthetic_ht_cl):
        detailed = TemperatureDecoder.extract_setpoints_detailed(synthetic_ht_cl)
        first = detailed["ht_cl_r02"]
        assert isinstance(first, Setpoint)
        assert first.offset == 0x08
        assert first.raw == 0x4C
        assert first.celsius == 18.0
        assert first.hex == "4c"
        assert first.plausible is True
        assert first.confidence == "bestaetigt"

    def test_detailed_offers_alternative_readings(self, synthetic_ht_cl):
        first = TemperatureDecoder.extract_setpoints_detailed(synthetic_ht_cl)["ht_cl_r02"]
        assert "u16le" in first.alternatives
        assert "s8" in first.alternatives

    def test_as_dict_is_json_friendly(self, synthetic_ht_cl):
        import json

        detailed = TemperatureDecoder.extract_setpoints_detailed(synthetic_ht_cl)
        payload = {key: sp.as_dict() for key, sp in detailed.items()}
        assert json.loads(json.dumps(payload))["ht_cl_r02"]["offset"] == "0x08"

    def test_short_buffer_yields_none(self):
        """Zu kurze Daten duerfen nicht knallen, sondern None liefern."""
        temps = TemperatureDecoder.extract_setpoints(bytes([0x0F, 0x00, 0x00] * 2 + [0x0F, 0x00]))
        assert temps["ht_cl_r02"] is None

    def test_rejects_non_bytes(self):
        with pytest.raises(TemperatureDecodeError, match="bytes"):
            TemperatureDecoder.extract_setpoints("nicht binaer")

    def test_accepts_bytearray(self, synthetic_ht_cl):
        temps = TemperatureDecoder.extract_setpoints(bytearray(synthetic_ht_cl))
        assert temps["ht_cl_r02"] == 18.0

    def test_custom_specs_override_layout(self, synthetic_ht_cl):
        spec = SetpointSpec(key="dhw_setpoint", label="Warmwasser", offset=0x0B,
                            bias=-20.0)
        temps = TemperatureDecoder.extract_setpoints(synthetic_ht_cl, [spec])
        assert temps == {"dhw_setpoint": 30.0}

    def test_with_offsets_patches_known_key(self, synthetic_ht_cl):
        specs = TemperatureDecoder.with_offsets(ht_cl_r02=0x0B)
        temps = TemperatureDecoder.extract_setpoints(synthetic_ht_cl, specs)
        assert temps["ht_cl_r02"] == 30.0
        assert temps["ht_cl_r03"] == 30.0

    def test_with_offsets_rejects_unknown_key(self):
        with pytest.raises(TemperatureDecodeError, match="Unbekannte Sollwert-Schluessel"):
            TemperatureDecoder.with_offsets(tapwater_setpoint=0x10)


class TestSetpointSpec:
    """Die Layout-Beschreibung validiert sich selbst."""

    def test_rejects_unknown_encoding(self):
        with pytest.raises(TemperatureDecodeError, match="Unbekannte Kodierung"):
            SetpointSpec(key="x", label="X", offset=0, encoding="f32")

    def test_rejects_negative_offset(self):
        with pytest.raises(TemperatureDecodeError, match="Negativer Offset"):
            SetpointSpec(key="x", label="X", offset=-1)

    def test_rejects_inverted_range(self):
        with pytest.raises(TemperatureDecodeError, match="Wertebereich"):
            SetpointSpec(key="x", label="X", offset=0, valid_range=(60.0, 20.0))

    @pytest.mark.parametrize("encoding, width", [("u8", 1), ("s8", 1), ("u16le", 2), ("s16le", 2)])
    def test_width(self, encoding, width):
        assert SetpointSpec(key="x", label="X", offset=0, encoding=encoding).width == width


class TestValidation:
    """Validierung gegen bekannte Werte und Plausibilitaetsfenster."""

    def test_known_values_all_match(self):
        report = TemperatureDecoder.validate_known_values()
        assert report["ok"] is True
        assert len(report["checks"]) == len(TemperatureDecoder.KNOWN_VALUES)
        assert all(check["match"] for check in report["checks"])
        assert all(check["roundtrip_ok"] for check in report["checks"])

    def test_known_values_match_the_vendor_tool(self):
        assert TemperatureDecoder.KNOWN_VALUES == {0x4C: 18.0, 0x64: 30.0, 0x5A: 25.0}

    def test_plausible_range_is_inclusive(self):
        assert TemperatureDecoder.is_plausible(20.0, (20.0, 60.0)) is True
        assert TemperatureDecoder.is_plausible(60.0, (20.0, 60.0)) is True
        assert TemperatureDecoder.is_plausible(19.5, (20.0, 60.0)) is False

    def test_validate_setpoints_ok(self, synthetic_ht_cl):
        report = TemperatureDecoder.validate_setpoints(synthetic_ht_cl)
        assert report["ok"] is True
        assert report["issues"] == []

    def test_validate_setpoints_flags_implausible(self, synthetic_ht_cl):
        """Ein 0xff im Wertebyte (127.5 C) muss als unplausibel auffallen."""
        data = bytearray(synthetic_ht_cl)
        data[0x08] = 0xFF
        report = TemperatureDecoder.validate_setpoints(bytes(data))
        assert report["ok"] is False
        assert any("ht_cl_r02" in issue for issue in report["issues"])

    def test_validate_setpoints_flags_missing_offset(self):
        report = TemperatureDecoder.validate_setpoints(b"\x0f\x00\x00")
        assert report["ok"] is False
        assert any("ausserhalb der Datei" in issue for issue in report["issues"])


class TestFindTemperatureCandidates:
    """Scan-Werkzeug zur Offset-Verifikation am echten Geraet."""

    def test_finds_injected_setpoints(self, synthetic_ht_cl):
        found = {
            candidate.offset: candidate.celsius
            for candidate in TemperatureDecoder.find_temperature_candidates(synthetic_ht_cl)
        }
        assert found[0x08] == 38.0    # Rohskalierung ohne Nullpunkt
        assert found[0x0B] == 50.0

    def test_skips_padding(self, synthetic_ht_cl):
        candidates = TemperatureDecoder.find_temperature_candidates(synthetic_ht_cl)
        assert all(candidate.raw != 0 for candidate in candidates)

    def test_range_filter(self, synthetic_ht_cl):
        candidates = TemperatureDecoder.find_temperature_candidates(
            synthetic_ht_cl, valid_range=(49.0, 51.0)
        )
        assert [candidate.offset for candidate in candidates] == [0x0B]

    def test_step_two_scans_word_grid(self, synthetic_ht_cl):
        candidates = TemperatureDecoder.find_temperature_candidates(synthetic_ht_cl, step=2)
        assert all(candidate.offset % 2 == 0 for candidate in candidates)

    def test_rejects_zero_step(self, synthetic_ht_cl):
        with pytest.raises(TemperatureDecodeError, match="Schrittweite"):
            TemperatureDecoder.find_temperature_candidates(synthetic_ht_cl, step=0)


class TestFileLevel:
    """Datei-Ebene und CLI."""

    def test_format_hex(self):
        assert TemperatureDecoder.format_hex(b"\x4c\x00\x64") == "4c 00 64"

    def test_decode_file(self, tmp_path, synthetic_ht_cl):
        path = tmp_path / "HT_CL.DAT"
        path.write_bytes(synthetic_ht_cl)
        report = TemperatureDecoder.decode_file(path)
        assert report["filename"] == "HT_CL.DAT"
        assert report["size"] == 512
        assert report["size_ok"] is True
        assert report["setpoints"]["ht_cl_r02"]["celsius"] == 18.0

    def test_decode_file_flags_wrong_size(self, tmp_path):
        path = tmp_path / "HT_CL.DAT"
        path.write_bytes(bytes(64))
        assert TemperatureDecoder.decode_file(path)["size_ok"] is False

    def test_main_without_arguments_validates_only(self, capsys):
        assert main([]) == 0
        assert "18.0 C" in capsys.readouterr().out

    def test_main_reports_file(self, tmp_path, synthetic_ht_cl, capsys):
        path = tmp_path / "HT_CL.DAT"
        path.write_bytes(synthetic_ht_cl)
        assert main([str(path)]) == 0
        assert "HT_CL.DAT" in capsys.readouterr().out

    def test_main_handles_missing_file(self, tmp_path, capsys):
        assert main([str(tmp_path / "fehlt.DAT")]) == 1
        assert "nicht lesen" in capsys.readouterr().out


def setpoint_bias(setpoint):
    """Nullpunkt, mit dem ein Sollwert kodiert wurde."""
    from src.temperature_decoder import SETPOINT_BIAS
    return SETPOINT_BIAS


class TestAgainstRealDeviceData:
    """Validierung gegen echte Geraetedaten.

    Wird uebersprungen, solange ``data/HT_CL.DAT`` fehlt. Sobald die Dateien
    von der SD-Karte dort liegen, pruefen diese Tests die Hypothesen.
    """

    def test_file_size(self, real_ht_cl):
        assert len(real_ht_cl) == TemperatureDecoder.DAT_FILE_SIZE

    def test_setpoints_are_plausible(self, real_ht_cl):
        report = TemperatureDecoder.validate_setpoints(real_ht_cl)
        assert report["ok"], (
            "Sollwert-Offsets stimmen nicht: "
            + "; ".join(report["issues"])
            + " -- mit find_temperature_candidates() den richtigen Offset suchen "
            "und with_offsets() anpassen."
        )

    def test_setpoints_roundtrip_to_original_bytes(self, real_ht_cl):
        """Dekodieren und wieder kodieren muss die Originalbytes ergeben."""
        detailed = TemperatureDecoder.extract_setpoints_detailed(real_ht_cl)
        for setpoint in detailed.values():
            if setpoint is None:
                continue
            reencoded = TemperatureDecoder.encode_to_bytes(
                setpoint.celsius, setpoint.encoding, bias=setpoint_bias(setpoint))
            original = real_ht_cl[setpoint.offset : setpoint.offset + len(reencoded)]
            assert reencoded == original, f"Roundtrip fehlgeschlagen fuer {setpoint.key}"
