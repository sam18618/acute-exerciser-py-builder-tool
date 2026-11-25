"""Controller timing: ``PxI3CControllerTimingConfig`` and ``PxI2CTimingConfig``.

The controller's bus timing is a nested structure, so the GUI gives it its own
sub-tree under the controller device rather than one very long form:

* **Timing** -- the bus-level scalars, plus an on/off switch for the whole
  block. It is on by default; off means no ``timing=`` argument at all, and the
  library's defaults apply.
* One node per **phase** (push-pull header, open-drain headers, I3C read/write,
  ODR8). Each phase is either *simple* -- one low/high pair for every bit -- or
  *advanced*, where all eight data bits and the T-bit get their own low/high.
* **I2C timing** -- the legacy-I2C timings, also on by default and likewise
  switchable.
* **HDR pattern timing** and **HDR-DDR timing** -- the two HDR blocks. These
  have no on/off switch: every field is optional, so a blank one is left out
  and the block is emitted only once something is filled in.
  aqpxlib ships no reference values for them, so the builder does not invent
  any -- blank means the device decides.

Default values come from a working Acute setup. They are a starting point, not
a specification: timings depend on the bus, the pull-ups and the targets, so
treat them as something to tune rather than to trust.
"""

from __future__ import annotations

from typing import Any

from .catalog import Param, default_params, is_set

#: Phase name on PxI3CControllerTimingConfig -> label and default low/high.
PHASES: dict[str, tuple[str, int, int, str]] = {
    "pp_header_timing": (
        "Push-pull header", 17, 8,
        "Header driven push-pull.",
    ),
    "op_header_timing": (
        "Optimized header", 65, 35,
        "Header under the OPTIMIZED address header policy.",
    ),
    "ods_header_timing": (
        "Open-drain slow header", 45, 55,
        "Header under OPEN_DRAIN_SLOW.",
    ),
    "odf_header_timing": (
        "Open-drain fast header", 45, 55,
        "Header under OPEN_DRAIN_FAST.",
    ),
    "i3_c_rd_timing": (
        "I3C read", 13, 13,
        "Data phase of an SDR read.",
    ),
    "i3_c_wr_timing": (
        "I3C write", 13, 13,
        "Data phase of an SDR write.",
    ),
    "i3_c_odr8_timing": (
        "I3C ODR8", 45, 55,
        "Open-drain 8-bit transfers.",
    ),
}

#: The bus-level scalars that sit directly on PxI3CControllerTimingConfig.
SCALARS: list[Param] = [
    Param("i3_c_t_cas", "t_CAS", "int", default=8, always_emit=True,
          help="Clock-after-start time."),
    Param("i3_c_t_ca_sr", "t_CASr", "int", default=4, always_emit=True,
          help="Clock-after-start time on a repeated start."),
    Param("i3_c_t_cb_sr", "t_CBSr", "int", default=4, always_emit=True,
          help="Clock-before-start time on a repeated start."),
    Param("i3_c_t_cbp", "t_CBP", "int", default=4, always_emit=True,
          help="Clock-before-stop time."),
]

#: Fields aqpxlib marks deprecated. They are not offered and are dropped from
#: projects that still carry them, so nothing this tool emits can raise a
#: DeprecationWarning. i2c_low / i2c_high are replaced by the I2C timing block
#: (``PxI3CBaseControllerConfig.i2_c_timing``).
DEPRECATED_SCALARS = ("i2_c_low", "i2_c_high")

MODE = Param(
    "mode", "Mode", "enum", default="simple", choices=("simple", "advanced"),
    always_emit=True,
    help="Simple uses one low/high pair for every bit; advanced times each bit.",
)

SIMPLE: list[Param] = [
    Param("low", "Low", "int", default=13, always_emit=True),
    Param("high", "High", "int", default=13, always_emit=True),
]


def _advanced_params(low: int, high: int) -> list[Param]:
    """Per-bit timing: eight data bits plus the T-bit."""
    params: list[Param] = []
    for bit in range(8):
        params.append(Param(f"bit_{bit}_low", f"Bit {bit} low", "int",
                            default=low, always_emit=True))
        params.append(Param(f"bit_{bit}_high", f"Bit {bit} high", "int",
                            default=high, always_emit=True))
    params.append(Param("tbit_low", "T-bit low", "int", default=low, always_emit=True))
    params.append(Param("tbit_high", "T-bit high", "int", default=high, always_emit=True))
    return params


#: Legacy I2C timings, in nanoseconds.
I2C_TIMING: list[Param] = [
    Param("high", "High", "int", default=4000, always_emit=True),
    Param("start_setup_time", "Start setup", "int", default=4000, always_emit=True),
    Param("start_hold_time", "Start hold", "int", default=4000, always_emit=True),
    Param("data_setup_time", "Data setup", "int", default=500, always_emit=True),
    Param("data_hold_time", "Data hold", "int", default=4000, always_emit=True),
    Param("stop_setup_time", "Stop setup", "int", default=4000, always_emit=True),
]


#: Re-exported: the HDR blocks share the "no switch, emitted once filled in"
#: rule with the dynamic address table, so the predicate lives in the catalog.
__all__ = ["is_set"]

#: HDR Restart/Exit and Target Reset Pattern timing
#: (``PxI3CBaseControllerConfig.hdr_pattern_timing``).
#:
#: Both fields are optional, and unlike the SDR phases there is no reference
#: value to seed them with, so they start blank. Asymmetric low and high periods
#: are allowed.
HDR_PATTERN: list[Param] = [
    Param("low", "Low", "int", default=None,
          help="Low period of a pattern's SDA transitions."),
    Param("high", "High", "int", default=None,
          help="High period of a pattern's SDA transitions."),
]

#: HDR-DDR mode timing (``PxI3CBaseControllerConfig.hdr_ddr_timing``). These
#: are how clock stalling is configured. All twelve are optional.
HDR_DDR: list[Param] = [
    Param("pre0_setup", "Preamble 0 setup", "int", default=None),
    Param("pre0_hold", "Preamble 0 hold", "int", default=None),
    Param("pre1_setup", "Preamble 1 setup", "int", default=None),
    Param("pre1_hold", "Preamble 1 hold", "int", default=None),
    Param("par0_setup", "Parity 0 setup", "int", default=None),
    Param("par0_hold", "Parity 0 hold", "int", default=None),
    Param("par1_setup", "Parity 1 setup", "int", default=None),
    Param("par1_hold", "Parity 1 hold", "int", default=None),
    Param("data_setup_rising", "Data setup (rising)", "int", default=None,
          help="DDR clocks data on both edges; this is the rising one."),
    Param("data_hold_rising", "Data hold (rising)", "int", default=None),
    Param("data_setup_falling", "Data setup (falling)", "int", default=None),
    Param("data_hold_falling", "Data hold (falling)", "int", default=None),
]

#: The HDR-DDR fields in display groups, so the form reads as the signal does.
HDR_DDR_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Preamble", ("pre0_setup", "pre0_hold", "pre1_setup", "pre1_hold")),
    ("Parity", ("par0_setup", "par0_hold", "par1_setup", "par1_hold")),
    ("Data", ("data_setup_rising", "data_hold_rising",
              "data_setup_falling", "data_hold_falling")),
)


def phase_params(phase: str) -> list[Param]:
    """Every editable field of one phase, both modes."""
    _, low, high, _ = PHASES[phase]
    simple = [
        Param("low", "Low", "int", default=low, always_emit=True),
        Param("high", "High", "int", default=high, always_emit=True),
    ]
    return [MODE, *simple, *_advanced_params(low, high)]


def phase_params_for_mode(phase: str, mode: str) -> list[Param]:
    """The fields that actually matter in `mode` -- what the GUI should show."""
    _, low, high, _ = PHASES[phase]
    if mode == "advanced":
        return [MODE, *_advanced_params(low, high)]
    return [
        MODE,
        Param("low", "Low", "int", default=low, always_emit=True),
        Param("high", "High", "int", default=high, always_emit=True),
    ]


def phase_label(phase: str) -> str:
    """Human-readable name of a timing phase."""
    return PHASES[phase][0]


def phase_help(phase: str) -> str:
    """One-line description of what a timing phase covers."""
    return PHASES[phase][3]


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def default_phase(phase: str) -> dict[str, Any]:
    """A phase filled with its default values, in simple mode."""
    return default_params(phase_params(phase))


def default_timing() -> dict[str, Any]:
    """The whole timing block, switched on.

    A controller is emitted with an explicit timing config by default; switch
    the block off to leave the argument out and take the library's own.
    """
    return {
        "enabled": True,
        "phases": {name: default_phase(name) for name in PHASES},
        "scalars": default_params(SCALARS),
    }


def default_i2c_timing() -> dict[str, Any]:
    """The legacy-I2C timing block, switched on.

    Emitted as ``i2_c_timing=PxI2CTimingConfig(...)``. Switch it off to leave the
    argument out and take the library's own timings.
    """
    return {"enabled": True, **default_params(I2C_TIMING)}


def default_hdr_pattern() -> dict[str, Any]:
    """The HDR pattern block, blank -- which emits nothing."""
    return default_params(HDR_PATTERN)


def default_hdr_ddr() -> dict[str, Any]:
    """The HDR-DDR block, blank -- which emits nothing."""
    return default_params(HDR_DDR)


def normalise_hdr_pattern(block: dict[str, Any] | None) -> dict[str, Any]:
    """Fill in anything a project file is missing for the HDR pattern block."""
    base = default_hdr_pattern()
    if block:
        base.update({k: v for k, v in block.items() if k in base})
    return base


def normalise_hdr_ddr(block: dict[str, Any] | None) -> dict[str, Any]:
    """Fill in anything a project file is missing for the HDR-DDR block."""
    base = default_hdr_ddr()
    if block:
        base.update({k: v for k, v in block.items() if k in base})
    return base


def normalise(timing: dict[str, Any] | None) -> dict[str, Any]:
    """Fill in anything a project file is missing, so old files keep working."""
    base = default_timing()
    if not timing:
        return base
    # Fall back to the current default, so a project saved before the block
    # had a switch is treated the same as a new one.
    base["enabled"] = bool(timing.get("enabled", base["enabled"]))
    for name, defaults in base["phases"].items():
        defaults.update(timing.get("phases", {}).get(name, {}))
    base["scalars"].update({
        name: value for name, value in timing.get("scalars", {}).items()
        if name not in DEPRECATED_SCALARS
    })
    return base


def normalise_i2c(timing: dict[str, Any] | None) -> dict[str, Any]:
    """Fill in anything a project file is missing for the I2C block.

    A stored ``enabled`` wins, so switching the block off stays off when the
    default changes; a project that predates the switch picks the default up.
    """
    base = default_i2c_timing()
    if timing:
        base.update(timing)
    return base
