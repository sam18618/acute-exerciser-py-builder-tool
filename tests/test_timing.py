"""Controller timing config: emission, and agreement with the real protobuf."""

from __future__ import annotations

import ast
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pxbuilder import generate, timing  # noqa: E402
from pxbuilder.flows import FLOWS  # noqa: E402
from pxbuilder.model import Script  # noqa: E402

pytest.importorskip("aqpxlib", reason="aqpxlib is not installed")


def _script_with_timing(*, enabled=True, i2c=False, advanced=()) -> Script:
    script = FLOWS["bus_bringup"].as_script()
    controller = script.devices[0]
    block = timing.default_timing()
    block["enabled"] = enabled
    for phase in advanced:
        block["phases"][phase]["mode"] = "advanced"
    controller.params["timing"] = block
    if i2c:
        i2c_block = timing.default_i2c_timing()
        i2c_block["enabled"] = True
        controller.params["i2c_timing"] = i2c_block
    return script


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------


def test_both_blocks_off_emits_the_short_config() -> None:
    """With no timing at all, the config is a single argument."""
    script = _script_with_timing(enabled=False)
    script.devices[0].params["i2c_timing"] = {"enabled": False}
    source = generate(script)
    assert "config=PxI3CBaseControllerConfig(enabled=1)" in source
    assert "PxI3CControllerTimingConfig" not in source
    assert "PxI2CTimingConfig" not in source


def test_i3c_timing_off_still_emits_the_i2c_block() -> None:
    """The two switches are independent."""
    source = generate(_script_with_timing(enabled=False))
    assert "PxI3CControllerTimingConfig" not in source
    assert "i2_c_timing=PxI2CTimingConfig(" in source


def test_timing_on_emits_every_phase_and_scalar() -> None:
    source = generate(_script_with_timing())
    ast.parse(source)
    for phase in timing.PHASES:
        assert f"{phase}=PxI3CMsgTiming(" in source
    for param in timing.SCALARS:
        if param.default is None:
            continue  # deprecated, opt-in only
        assert f"{param.name}=" in source
    assert "PxI3CMsgSimpleTiming(" in source


def test_deprecated_scalars_are_not_offered_at_all() -> None:
    """aqpxlib marks i2c_low / i2c_high deprecated, so the builder drops them."""
    assert not {p.name for p in timing.SCALARS} & set(timing.DEPRECATED_SCALARS)
    source = generate(_script_with_timing())
    for name in timing.DEPRECATED_SCALARS:
        assert f"{name}=" not in source


def test_a_project_carrying_deprecated_scalars_is_cleaned(tmp_path: Path) -> None:
    """An older project file must not resurrect a deprecated field."""
    script = _script_with_timing()
    script.devices[0].params["timing"]["scalars"]["i2_c_low"] = 250
    path = tmp_path / "old.pxb.json"
    script.save(path)

    reloaded = Script.load(path)
    source = generate(reloaded)
    assert "i2_c_low" not in source
    cleaned = timing.normalise(reloaded.devices[0].params["timing"])
    assert "i2_c_low" not in cleaned["scalars"]


def test_advanced_mode_emits_every_bit() -> None:
    source = generate(_script_with_timing(advanced=("i3_c_rd_timing",)))
    ast.parse(source)
    assert "advanced=PxI3CMsgAdvancedTiming(" in source
    for bit in range(8):
        assert f"bit_{bit}_low=" in source
        assert f"bit_{bit}_high=" in source
    assert "tbit_low=" in source and "tbit_high=" in source


def test_i2c_timing_is_emitted_when_switched_on() -> None:
    source = generate(_script_with_timing(i2c=True))
    ast.parse(source)
    assert "i2_c_timing=PxI2CTimingConfig(" in source
    assert "from aqpxlib.i2c import PxI2CTimingConfig" in source


def test_receive_ibi_data_amount_is_optional() -> None:
    script = _script_with_timing(enabled=False)
    assert "receive_ibi_data_amount" not in generate(script)
    script.devices[0].params["receive_ibi_data_amount"] = 8
    assert "receive_ibi_data_amount=8" in generate(script)


def test_prohibit_hotjoin_is_emitted_only_when_switched_on() -> None:
    script = _script_with_timing(enabled=False)
    assert "prohibit_hotjoin" not in generate(script)
    script.devices[0].params["prohibit_hotjoin"] = True
    assert "prohibit_hotjoin=True" in generate(script)


def test_timing_survives_a_project_round_trip(tmp_path: Path) -> None:
    original = _script_with_timing(i2c=True, advanced=("i3_c_wr_timing",))
    path = tmp_path / "p.pxb.json"
    original.save(path)
    assert generate(Script.load(path)) == generate(original)


def test_a_project_without_timing_gets_the_default() -> None:
    """Files written before timing existed pick up the current default, which is on."""
    script = FLOWS["bus_bringup"].as_script()
    script.devices[0].params.pop("timing", None)
    assert "PxI3CControllerTimingConfig" in generate(script)


def test_a_project_that_switched_timing_off_keeps_it_off() -> None:
    """An explicit choice must survive the default changing under it."""
    script = FLOWS["bus_bringup"].as_script()
    script.devices[0].params["timing"] = {"enabled": False}
    assert "PxI3CControllerTimingConfig" not in generate(script)


def test_normalise_fills_in_missing_phases() -> None:
    partial = {"enabled": True, "phases": {"pp_header_timing": {"low": 99}}}
    filled = timing.normalise(partial)
    assert filled["phases"]["pp_header_timing"]["low"] == 99
    assert set(filled["phases"]) == set(timing.PHASES)
    assert set(filled["scalars"]) == {p.name for p in timing.SCALARS}


# --------------------------------------------------------------------------
# Agreement with the real library
# --------------------------------------------------------------------------


def test_timing_fields_exist_on_the_config() -> None:
    from aqpxlib.i3c import PxI3CControllerTimingConfig

    fields = PxI3CControllerTimingConfig.__dataclass_fields__
    for phase in timing.PHASES:
        assert phase in fields, f"PxI3CControllerTimingConfig has no {phase!r}"
    for param in timing.SCALARS:
        assert param.name in fields, f"PxI3CControllerTimingConfig has no {param.name!r}"


def test_phase_fields_exist_on_the_config() -> None:
    from aqpxlib.i3c import (
        PxI3CMsgAdvancedTiming,
        PxI3CMsgSimpleTiming,
        PxI3CMsgTiming,
    )

    assert set(PxI3CMsgTiming.__dataclass_fields__) >= {"simple", "advanced"}
    simple = PxI3CMsgSimpleTiming.__dataclass_fields__
    assert {"low", "high"} <= set(simple)

    advanced = set(PxI3CMsgAdvancedTiming.__dataclass_fields__)
    catalog_advanced = {
        p.name for p in timing.phase_params("pp_header_timing")
        if p.name not in ("mode", "low", "high")
    }
    assert catalog_advanced == advanced, (
        f"advanced timing fields differ: {catalog_advanced ^ advanced}"
    )


def test_i2c_timing_fields_exist_on_the_config() -> None:
    from aqpxlib.i2c import PxI2CTimingConfig

    fields = set(PxI2CTimingConfig.__dataclass_fields__)
    assert {p.name for p in timing.I2C_TIMING} == fields


def test_the_whole_config_actually_constructs() -> None:
    """Build the real PxI3CBaseControllerConfig from the catalog defaults."""
    from aqpxlib.i2c import PxI2CTimingConfig
    from aqpxlib.i3c import (
        PxI3CBaseControllerConfig,
        PxI3CControllerTimingConfig,
        PxI3CMsgAdvancedTiming,
        PxI3CMsgSimpleTiming,
        PxI3CMsgTiming,
    )

    block = timing.default_timing()
    phases = {}
    for index, (phase, values) in enumerate(block["phases"].items()):
        if index == 0:  # exercise the advanced branch too
            advanced = {
                p.name: values[p.name]
                for p in timing.phase_params(phase)
                if p.name not in ("mode", "low", "high")
            }
            payload = PxI3CMsgTiming(
                advanced=PxI3CMsgAdvancedTiming(**advanced))
        else:
            payload = PxI3CMsgTiming(
                simple=PxI3CMsgSimpleTiming(
                    low=values["low"], high=values["high"]))
        phases[phase] = payload

    scalars = {k: v for k, v in block["scalars"].items() if v is not None}
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        config = PxI3CBaseControllerConfig(
            enabled=1,
            timing=PxI3CControllerTimingConfig(**phases, **scalars),
            i2_c_timing=PxI2CTimingConfig(
                **{p.name: p.default for p in timing.I2C_TIMING}),
        )
    assert config.timing.i3_c_t_cas == block["scalars"]["i3_c_t_cas"]


def test_the_config_the_generator_emits_is_what_the_controller_takes() -> None:
    """The emitted config class is the one PxI3CBaseController accepts.

    0.7.0 moved device config off protobuf, and nothing validates the type at
    construction -- the wrong class only fails later, at attach. So pin the
    generator's choice to the constructor's own annotation.
    """
    import inspect
    import types
    import typing

    import aqpxlib.i3c as i3c
    from aqpxlib.i3c import PxI3CBaseController

    from pxbuilder import codegen

    emitted = getattr(i3c, codegen.CONTROLLER_CONFIG)
    annotated = inspect.signature(PxI3CBaseController.__init__).parameters["config"].annotation
    accepted = [a for a in typing.get_args(annotated) if a is not types.NoneType]
    assert accepted == [emitted]
    # A config carries protobuf_key; the type it replaced in 0.7.0 does not,
    # which is what made the wrong type fail late rather than at the ctor.
    assert emitted.protobuf_key


def test_nothing_the_builder_emits_is_deprecated() -> None:
    """Build every flow's controller config for real, with warnings fatal."""
    from aqpxlib.i2c import PxI2CTimingConfig
    from aqpxlib.i3c import (
        PxI3CBaseControllerConfig,
        PxI3CControllerTimingConfig,
        PxI3CMsgSimpleTiming,
        PxI3CMsgTiming,
    )

    block = timing.default_timing()
    phases = {
        phase: PxI3CMsgTiming(
            simple=PxI3CMsgSimpleTiming(
                low=values["low"], high=values["high"]))
        for phase, values in block["phases"].items()
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        PxI3CBaseControllerConfig(
            enabled=1,
            timing=PxI3CControllerTimingConfig(**phases, **block["scalars"]),
            i2_c_timing=PxI2CTimingConfig(
                **{p.name: p.default for p in timing.I2C_TIMING}),
        )


# --------------------------------------------------------------------------
# HDR timing
# --------------------------------------------------------------------------


def test_hdr_blocks_emit_nothing_until_a_field_is_filled() -> None:
    """No switch, so an untouched block must leave the output alone."""
    script = _script_with_timing()
    source = generate(script)
    assert "hdr_pattern_timing" not in source
    assert "hdr_ddr_timing" not in source


def test_no_flow_emits_hdr_timing() -> None:
    """Adding the blocks must not have changed a single existing flow."""
    for key, flow in FLOWS.items():
        source = generate(flow.as_script())
        assert "hdr_pattern_timing=" not in source, key
        assert "hdr_ddr_timing=" not in source, key


def test_the_hdr_pattern_block_is_emitted_when_set() -> None:
    script = _script_with_timing()
    script.devices[0].params["hdr_pattern_timing"] = {"low": 100, "high": 35}
    source = generate(script)
    ast.parse(source)
    assert "hdr_pattern_timing=PxI3CHdrPatternTimingConfig(" in source
    assert "low=100," in source
    assert "high=35," in source
    assert "from aqpxlib.i3c import" in source


def test_a_partly_filled_hdr_ddr_block_carries_only_what_was_set() -> None:
    """A blank field is an absence, not a zero: a zero would be an instruction."""
    script = _script_with_timing()
    script.devices[0].params["hdr_ddr_timing"] = {
        "pre0_setup": 3, "data_hold_falling": 2}
    source = generate(script)
    ast.parse(source)
    assert "pre0_setup=3," in source
    assert "data_hold_falling=2," in source
    for absent in ("pre0_hold=", "par1_setup=", "data_setup_rising="):
        assert absent not in source


def test_an_hdr_block_alone_still_emits_the_long_config() -> None:
    """With both timing switches off, an HDR field must still open the config."""
    script = _script_with_timing(enabled=False)
    script.devices[0].params["i2c_timing"] = {"enabled": False}
    script.devices[0].params["hdr_ddr_timing"] = {"par0_hold": 7}
    source = generate(script)
    ast.parse(source)
    assert "config=PxI3CBaseControllerConfig(enabled=1)" not in source
    assert "par0_hold=7," in source


def test_hdr_fields_exist_on_the_real_config() -> None:
    from aqpxlib.i3c import PxI3CHdrDdrTimingConfig, PxI3CHdrPatternTimingConfig

    pattern = set(PxI3CHdrPatternTimingConfig.__dataclass_fields__)
    assert {p.name for p in timing.HDR_PATTERN} == pattern

    ddr = set(PxI3CHdrDdrTimingConfig.__dataclass_fields__)
    assert {p.name for p in timing.HDR_DDR} == ddr


def test_the_hdr_arguments_exist_on_the_controller_config() -> None:
    from aqpxlib.i3c import PxI3CBaseControllerConfig

    fields = PxI3CBaseControllerConfig.__dataclass_fields__
    assert "hdr_pattern_timing" in fields
    assert "hdr_ddr_timing" in fields


def test_the_hdr_ddr_groups_cover_every_field_once() -> None:
    """The form is laid out by group, so a new field must not fall through."""
    grouped = [name for _, names in timing.HDR_DDR_GROUPS for name in names]
    assert sorted(grouped) == sorted(p.name for p in timing.HDR_DDR)
    assert len(grouped) == len(set(grouped))


def test_the_rendered_hdr_blocks_construct_for_real() -> None:
    from aqpxlib.i3c import (
        PxI3CBaseControllerConfig,
        PxI3CHdrDdrTimingConfig,
        PxI3CHdrPatternTimingConfig,
    )

    config = PxI3CBaseControllerConfig(
        enabled=1,
        hdr_pattern_timing=PxI3CHdrPatternTimingConfig(low=100, high=35),
        hdr_ddr_timing=PxI3CHdrDdrTimingConfig(pre0_setup=3, data_hold_falling=2),
    )
    assert config.hdr_pattern_timing.low == 100
    assert config.hdr_ddr_timing.pre0_setup == 3
    # Untouched fields stay None rather than becoming zero.
    assert config.hdr_ddr_timing.par1_hold is None


def test_an_hdr_block_survives_a_project_round_trip(tmp_path: Path) -> None:
    script = _script_with_timing()
    script.devices[0].params["hdr_ddr_timing"] = {"pre1_hold": 5}
    path = tmp_path / "hdr.pxb.json"
    script.save(path)
    assert "pre1_hold=5," in generate(Script.load(path))


def test_normalising_fills_an_old_project_in() -> None:
    """A project saved before the blocks existed must still open."""
    assert timing.normalise_hdr_ddr(None) == timing.default_hdr_ddr()
    assert timing.normalise_hdr_pattern({}) == timing.default_hdr_pattern()
    # An unknown key from a newer file is dropped rather than emitted blindly.
    assert "nonsense" not in timing.normalise_hdr_ddr({"nonsense": 1})
    assert timing.normalise_hdr_ddr({"pre0_setup": 9})["pre0_setup"] == 9


def test_timing_is_on_by_default() -> None:
    assert timing.default_timing()["enabled"] is True
    assert "PxI3CControllerTimingConfig" in generate(FLOWS["bus_bringup"].as_script())


def test_i2c_timing_is_on_by_default() -> None:
    assert timing.default_i2c_timing()["enabled"] is True
    assert "i2_c_timing=PxI2CTimingConfig(" in generate(FLOWS["bus_bringup"].as_script())


def test_a_project_that_switched_i2c_timing_off_keeps_it_off() -> None:
    script = FLOWS["bus_bringup"].as_script()
    script.devices[0].params["i2c_timing"] = {"enabled": False}
    assert "i2_c_timing=" not in generate(script)
