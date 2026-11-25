"""Turn a :class:`pxbuilder.model.Script` into runnable Python source.

The output is meant to be handed to an engineer and read, not just executed --
so it keeps the shape of a hand-written aqpxlib sample: a `main()` inside a
`with AqProtocolExerciser.connect(...)` block, explicit bus setup, then one
statement per step.

Controller sends are emitted as explicit ``I3CControllerTransactionV2`` frames
handed to ``send_i3c_transactions()``, so every bit that goes on the wire is
visible in the generated code.

While emitting, the generator records which lines belong to which outline node
(:attr:`Generator.anchors`). The GUI uses that to scroll the preview to the code
for whatever you select, and to highlight it.
"""

from __future__ import annotations

from . import timing as timing_spec
from .catalog import (
    DAA_TABLE,
    DEVICES,
    OPERATIONS,
    OpSpec,
    Param,
    device_matches,
    is_set,
    normalise_daa_table,
)
from .model import Script, Step, Transaction
from .transactions import (
    CCC_VIA_OPERATION,
    FRAMES,
    ccc_via_operation,
    NOTE_KEY,
    TRANSACTIONS,
    block_info,
    expand,
    is_composite,
)

MAX_LINE = 96
INDENT = "    "

#: Names pulled from ``aqpxlib.message`` when a frame needs them.
MESSAGE_IMPORTS = {
    "sdr_write": ("I3CSdrWrite",),
    "sdr_read": ("I3CSdrRead",),
    "hdr_ddr_write": ("I3CDdrWrite",),
    "hdr_ddr_read": ("I3CDdrRead",),
    "stop": ("I3CStop",),
    "idle_time": ("IdleTime",),
}

ENUM_MODULES = {
    "I3CAddressHeaderPolicy": "aqpxlib.message",
    "I3CParityMode": "aqpxlib.message",
    "I3CStopType": "aqpxlib.message",
    "PxI3CTargetResetAction": "aqpxlib.i3c",
}

# Device configuration moved off protobuf in aqpxlib 0.7.0: the config types are
# now frozen dataclasses exported from the protocol package. Only the class name
# and its home module changed -- every field name below is the same as the
# protobuf one it replaced, which is why the emitters just substitute the name.
# Transaction frames are deliberately NOT in here: send_i3c_transactions still
# takes I3CControllerTransactionV2 from aqpxlib.message, by design.
CONTROLLER_CONFIG = "PxI3CBaseControllerConfig"
TIMING_CONFIG = "PxI3CControllerTimingConfig"
MSG_TIMING = "PxI3CMsgTiming"
MSG_SIMPLE_TIMING = "PxI3CMsgSimpleTiming"
MSG_ADVANCED_TIMING = "PxI3CMsgAdvancedTiming"
I2C_TIMING_CONFIG = "PxI2CTimingConfig"
HDR_PATTERN_CONFIG = "PxI3CHdrPatternTimingConfig"
HDR_DDR_CONFIG = "PxI3CHdrDdrTimingConfig"
DAA_TABLE_CONFIG = "PxI3CControllerDynamicAssignmentTable"


class CodeGenError(ValueError):
    """Raised when a script cannot be turned into valid source."""


# --------------------------------------------------------------------------
# Value rendering
# --------------------------------------------------------------------------


def _hex(value: int) -> str:
    """Render an int as a hex literal, padded to a sensible width."""
    width = 2
    magnitude = abs(int(value))
    while magnitude >= 1 << (width * 4):
        width += 2
    return f"0x{int(value):0{width}X}"


def _byte(value: object) -> str:
    """Render one element of a byte list -- a number, or a named CCC."""
    if isinstance(value, str):
        return f"PxI3CCommonCommandCode.{value}"
    return _hex(int(value))  # type: ignore[arg-type]


def render_value(param: Param, value: object, cls_name: str = "") -> str:
    """Render one parameter value as a Python expression.

    `cls_name` fills the ``{cls}`` placeholder in an enum prefix, for enums that
    live on the device class rather than on a shared one.
    """
    if param.type == "hex":
        return _hex(int(value))  # type: ignore[arg-type]
    if param.type == "int":
        return str(int(value))  # type: ignore[arg-type]
    if param.type == "bool":
        return "True" if value else "False"
    if param.type == "enum":
        prefix = param.enum_prefix.replace("{cls}", cls_name)
        return f"{prefix}.{value}"
    if param.type == "enum_flags":
        prefix = param.enum_prefix.replace("{cls}", cls_name)
        return " | ".join(f"{prefix}.{name}" for name in value)  # type: ignore[union-attr]
    if param.type == "ccc":
        return f"PxI3CCommonCommandCode.{value}"
    if param.type == "str":
        return repr(value)
    if param.type == "bytes_literal":
        items = ", ".join(_byte(v) for v in value)  # type: ignore[union-attr]
        return f"bytes([{items}])"
    if param.type in ("bytes", "int_list"):
        items = ", ".join(_byte(v) for v in value)  # type: ignore[union-attr]
        return f"[{items}]"
    if param.type == "addr_pairs":
        items = ", ".join(f"({_hex(a)}, {_hex(b)})" for a, b in value)  # type: ignore[union-attr]
        return f"[{items}]"
    if param.type == "byte_pairs":
        items = ", ".join(
            f"({_hex(defining)}, bytes([{', '.join(_hex(b) for b in data)}]))"
            for defining, data in value  # type: ignore[union-attr]
        )
        return f"[{items}]"
    if param.type == "pairs":
        items = ", ".join(
            f"({_hex(addr)}, [{', '.join(_hex(b) for b in data)}])"
            for addr, data in value  # type: ignore[union-attr]
        )
        return f"[{items}]"
    msg = f"Unknown parameter type {param.type!r} on {param.name!r}"
    raise CodeGenError(msg)


def _should_emit(param: Param, value: object) -> bool:
    """Decide whether an argument is worth putting in the generated call."""
    if value is None:
        return False
    if param.always_emit:
        return True
    if value == param.default:
        return False
    # An empty list carries no information unless it differs from the default.
    return not (isinstance(value, (list, tuple)) and len(value) == 0)


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------


class Generator:
    """Builds the source text for one :class:`Script`.

    The body is emitted into :attr:`_body` line by line so that the range each
    outline node occupies can be recorded as it goes; the import block is built
    afterwards, because what it needs is only known once the body is done.
    """

    def __init__(self, script: Script) -> None:
        self.script = script
        #: outline node id -> (first line, last line), 1-based and inclusive.
        self.anchors: dict[str, tuple[int, int]] = {}
        self._body: list[str] = []
        self._used_names: dict[str, int] = {}
        self._needs_format_bytes = False
        self._message_names: set[str] = set()
        self._i3c_names: set[str] = set()
        self._i2c_names: set[str] = set()
        self._needs_ccc = False

    # -- emitting --------------------------------------------------------

    def _emit(self, *lines: str) -> None:
        self._body.extend(lines)

    def _here(self) -> int:
        """Index of the next line to be emitted."""
        return len(self._body)

    def _mark(self, node: str, start: int, end: int | None = None) -> None:
        """Record that `node`'s code spans body lines [start, end)."""
        stop = self._here() if end is None else end
        if stop > start:
            self.anchors[node] = (start, stop - 1)

    # -- naming ----------------------------------------------------------

    def _unique(self, base: str) -> str:
        """Give each step's result its own variable name."""
        count = self._used_names.get(base, 0)
        self._used_names[base] = count + 1
        return base if count == 0 else f"{base}_{count + 1}"

    def _device_cls(self, var: str) -> str:
        """The aqpxlib class of the device bound to `var`."""
        device = self.script.device(var)
        if device is None:
            return ""
        spec = DEVICES.get(device.kind)
        return spec.cls if spec else ""

    # -- validation ------------------------------------------------------

    def validate(self) -> list[str]:
        """Return human-readable problems that would make the output unusable."""
        problems: list[str] = []
        s = self.script

        for device in s.devices:
            if device.kind not in DEVICES:
                problems.append(f"Unknown device type {device.kind!r} for {device.var!r}.")
            if not device.var.isidentifier():
                problems.append(f"{device.var!r} is not a valid Python variable name.")

        seen: dict[str, str] = {}
        for device in s.devices:
            if device.var in seen:
                problems.append(f"Duplicate device variable name {device.var!r}.")
            seen[device.var] = device.kind

        controllers = [d for d in s.devices
                       if DEVICES.get(d.kind) and DEVICES[d.kind].role == "controller"]
        if len(controllers) > 1:
            problems.append("Only one controller can be active on the bus at a time.")

        for i, step in enumerate(s.steps, start=1):
            if not step.enabled:
                continue
            problems += self._validate_step(i, step, seen)

        if any(st.enabled for st in s.steps) and not controllers and any(
            st.is_frame or OPERATIONS.get(st.op, OpSpec("", "", "")).group == "controller"
            for st in s.steps if st.enabled
        ):
            problems.append("The script has controller steps but no controller on the bus.")

        return problems

    def _validate_step(self, index: int, step: Step, devices: dict[str, str]) -> list[str]:
        problems: list[str] = []

        if step.is_frame:
            label = "Controller send"
            if not step.target_var:
                problems.append(f"Step {index} ({label}): no controller selected.")
            elif step.target_var not in devices:
                problems.append(f"Step {index} ({label}): {step.target_var!r} is not on the bus.")
            elif DEVICES[devices[step.target_var]].role != "controller":
                problems.append(
                    f"Step {index} ({label}): {step.target_var!r} is not a controller."
                )
            if not [t for t in step.transactions if t.enabled]:
                problems.append(f"Step {index} ({label}): the frame has no transactions.")
            for t in step.transactions:
                if block_info(t.kind) is None:
                    problems.append(f"Step {index}: unknown transaction {t.kind!r}.")
                # A whole block is routed to the controller method; raw bytes
                # carrying the same CCC cannot be, so only those are reported.
                if is_composite(t.kind):
                    continue
                offending = ccc_via_operation(t.params)
                if offending:
                    problems.append(f"Step {index}: {self._ccc_problem(offending)}")
            try:
                self._daa_routing(step)
            except CodeGenError as exc:
                problems.append(f"Step {index}: {exc}")
            problems += self._validate_idle_times(index, step, label)
            return problems

        spec = OPERATIONS.get(step.op)
        if spec is None:
            return [f"Step {index}: unknown operation {step.op!r}."]

        if not step.target_var:
            problems.append(f"Step {index} ({spec.label}): no device selected.")
        elif step.target_var not in devices:
            problems.append(f"Step {index} ({spec.label}): {step.target_var!r} is not on the bus.")
        elif not device_matches(devices[step.target_var], spec.applies_to):
            kind_label = DEVICES[devices[step.target_var]].label
            problems.append(
                f"Step {index} ({spec.label}): not available on a {kind_label}."
            )
        return problems

    @staticmethod
    def _ccc_problem(ccc: str) -> str:
        """Why hand-built bytes for this CCC cannot work, and what to use."""
        op_key = CCC_VIA_OPERATION[ccc]
        spec = OPERATIONS[op_key]
        short = ccc.rsplit("_", 1)[-1]
        return (
            f"{short} cannot go out as raw bytes -- the command byte alone does "
            f"not carry out the procedure. Pick it in a Broadcast CCC block, or "
            f"use the {spec.label!r} step; either calls {spec.method}()."
        )

    @staticmethod
    def _validate_idle_times(index: int, step: Step, label: str) -> list[str]:
        """An idle gap only means something between two transfers.

        At the head of a frame there is nothing before it to wait after, and at
        the tail the STOP ends the frame anyway.
        """
        enabled = [t for t in step.transactions if t.enabled]
        if not enabled:
            return []
        problems = []
        if enabled[0].kind == "idle_time":
            problems.append(
                f"Step {index} ({label}): a frame cannot start with an Idle Time -- "
                "there is nothing before it to wait after."
            )
        if len(enabled) > 1 and enabled[-1].kind == "idle_time":
            problems.append(
                f"Step {index} ({label}): a frame cannot end with an Idle Time -- "
                "move it between two transfers."
            )
        return problems

    # -- imports ---------------------------------------------------------

    def _imports(self) -> list[str]:
        s = self.script
        self._i3c_names.add("PxI3CBus")
        needs_error = False
        needs_controller_config = False

        for device in s.devices:
            spec = DEVICES[device.kind]
            self._i3c_names.add(spec.cls)
            # Both roles guard attach_to_bus, so any device needs the error type.
            needs_error = True
            if spec.role == "controller":
                needs_controller_config = True

        lines = [
            "import logging",
            "",
            "from aqpxlib import AqProtocolExerciser",
        ]
        if needs_error:
            lines.append("from aqpxlib.exceptions import PxAPIError")
        i3c_names = set(self._i3c_names)
        if needs_controller_config:
            i3c_names.add(CONTROLLER_CONFIG)
        lines += self._import_block("aqpxlib.i2c", sorted(self._i2c_names))
        lines += self._import_block("aqpxlib.i3c", sorted(i3c_names))
        if self._needs_ccc:
            lines.append("from aqpxlib.i3c.ccc import PxI3CCommonCommandCode")

        if self._message_names:
            lines += self._import_block("aqpxlib.message", sorted(self._message_names))
        return lines

    def _register_enum(self, param: Param) -> None:
        """Note the import for an enum that is not nested on a device class.

        A nested one (``{cls}.SubAddressType``) rides in on the class itself and
        needs nothing; a standalone one has to be imported from whichever module
        ENUM_MODULES says it lives in.
        """
        module = ENUM_MODULES.get(param.enum_prefix)
        if module == "aqpxlib.i3c":
            self._i3c_names.add(param.enum_prefix)
        elif module == "aqpxlib.i2c":
            self._i2c_names.add(param.enum_prefix)
        elif module == "aqpxlib.message":
            self._message_names.add(param.enum_prefix)

    @staticmethod
    def _import_block(module: str, names: list[str]) -> list[str]:
        """One import statement, wrapped in parentheses when it gets long."""
        if not names:
            return []
        single = f"from {module} import {', '.join(names)}"
        if len(single) <= MAX_LINE:
            return [single]
        return [f"from {module} import ("] + [f"{INDENT}{n}," for n in names] + [")"]

    # -- session and bus -------------------------------------------------

    def _emit_session(self, indent: str) -> None:
        s = self.script
        start = self._here()
        self._emit(
            f"{INDENT}with AqProtocolExerciser.connect(port={s.session.port}) as px_session:",
            f'{indent}logger.info('
            f'"Connected to the Protocol Exerciser on port {s.session.port}")',
        )
        if s.session.clear_handles:
            self._emit(f"{indent}px_session.set_handles([])")
        self._mark("session", start)

    def _emit_bus(self, indent: str) -> None:
        bus = self.script.bus
        start = self._here()
        self._emit(
            f"{indent}# Configure the I3C bus pins and electrical levels.",
            f"{indent}i3c_bus = PxI3CBus(px_session, scl={bus.scl}, sda={bus.sda})",
            f"{indent}i3c_bus.voltage = {bus.voltage}",
            f"{indent}i3c_bus.la_threshold_voltage = {bus.la_threshold_voltage}",
            f"{indent}i3c_bus.pullup_resistance = {bus.pullup_resistance}",
            f"{indent}i3c_bus.pulldown_resistance = {bus.pulldown_resistance}",
            f'{indent}logger.info("Created %s", i3c_bus)',
        )
        self._mark("bus", start)

    # -- devices ---------------------------------------------------------

    def _emit_devices(self, indent: str) -> None:
        if not self.script.devices:
            return
        section = self._here()
        for index, device in enumerate(self.script.devices):
            spec = DEVICES[device.kind]
            self._emit("")
            start = self._here()
            self._emit(f"{indent}# {spec.label}")

            if spec.role == "controller":
                self._emit_controller_ctor(index, device, spec.cls, indent)
            else:
                args = [f'name="{device.var}"']
                for param in spec.params:
                    value = device.params.get(param.name, param.default)
                    if not _should_emit(param, value):
                        continue
                    if param.type in ("enum", "enum_flags"):
                        self._register_enum(param)
                    args.append(f"{param.name}={render_value(param, value, spec.cls)}")
                self._emit(*self._ctor(device.var, spec.cls, ["px_session", *args], indent))

            if spec.role == "controller":
                self._emit(
                    f"{indent}try:",
                    f"{indent}{INDENT}{device.var}.attach_to_bus(i3c_bus)",
                    f"{indent}except PxAPIError:",
                    f"{indent}{INDENT}logger.warning(",
                    f'{indent}{INDENT}{INDENT}"Could not attach a new controller, '
                    f'reusing the one already on the bus"',
                    f"{indent}{INDENT})",
                    f"{indent}{INDENT}{device.var} = i3c_bus.get_current_controller()",
                )
            else:
                self._emit_target_attach(device, spec, indent)
            self._emit(f'{indent}logger.info("Attached %s", {device.var}.name)')
            self._mark(f"dev:{index}", start)
        self._mark("devices", section + 1)

    def _emit_target_attach(self, device, spec, indent: str) -> None:
        """Attach a target, saying what went wrong if it will not go on.

        Unlike a controller there is nothing to fall back to: the bus takes any
        number of targets, so a refusal is a real problem rather than "one is
        already there". The error is named and then re-raised, so the script
        stops at the cause instead of failing later in a way that looks
        unrelated.
        """
        address = device.params.get("static_addr")
        where = f" at static address {_hex(address)}" if address is not None else ""
        self._emit(
            f"{indent}try:",
            f"{indent}{INDENT}{device.var}.attach_to_bus(i3c_bus)",
            f"{indent}except PxAPIError:",
            f"{indent}{INDENT}logger.error(",
            f'{indent}{INDENT}{INDENT}"Could not attach %s{where} -- is the address '
            f'already taken, "',
            f'{indent}{INDENT}{INDENT}"or does the bus already hold as many targets '
            f'as it supports?",',
            f"{indent}{INDENT}{INDENT}{device.var}.name,",
            f"{indent}{INDENT})",
            f"{indent}{INDENT}raise",
        )

    @staticmethod
    def _ctor(var: str, cls: str, args: list[str], indent: str) -> list[str]:
        """Render a constructor call, wrapping when long."""
        single = f"{indent}{var} = {cls}({', '.join(args)})"
        if len(single) <= MAX_LINE:
            return [single]
        lines = [f"{indent}{var} = {cls}("]
        lines += [f"{indent}{INDENT}{arg}," for arg in args]
        lines.append(f"{indent})")
        return lines

    # -- controller config, including bus timing -------------------------

    def _emit_controller_ctor(self, index: int, device, cls: str, indent: str) -> None:
        """Emit the controller constructor with its controller-config block."""
        self._i3c_names.add(CONTROLLER_CONFIG)
        inner = indent + INDENT

        timing = timing_spec.normalise(device.params.get("timing"))
        i2c = timing_spec.normalise_i2c(device.params.get("i2c_timing"))
        # The HDR blocks have no switch: they appear once a field is filled in.
        hdr_pattern = timing_spec.normalise_hdr_pattern(
            device.params.get("hdr_pattern_timing"))
        hdr_ddr = timing_spec.normalise_hdr_ddr(device.params.get("hdr_ddr_timing"))
        daa_table = normalise_daa_table(device.params.get("dyn_addr_table"))
        simple_args = self._config_scalars(device)

        if (not timing["enabled"] and not i2c["enabled"]
                and not is_set(hdr_pattern) and not is_set(hdr_ddr)
                and not is_set(daa_table)):
            config = f"{CONTROLLER_CONFIG}({', '.join(simple_args)})"
            self._emit(*self._ctor(
                device.var, cls,
                ["px_session", f'name="{device.var}"', f"config={config}"], indent,
            ))
            return

        self._emit(
            f"{indent}{device.var} = {cls}(",
            f"{inner}px_session,",
            f'{inner}name="{device.var}",',
            f"{inner}config={CONTROLLER_CONFIG}(",
        )
        deep = inner + INDENT
        self._emit(*[f"{deep}{arg}," for arg in simple_args])
        if timing["enabled"]:
            self._emit_timing(index, timing, deep)
        if i2c["enabled"]:
            self._emit_i2c_timing(index, i2c, deep)
        if is_set(hdr_pattern):
            self._emit_optional_block(index, "hdr_pattern_timing", HDR_PATTERN_CONFIG,
                                      timing_spec.HDR_PATTERN, hdr_pattern, deep)
        if is_set(hdr_ddr):
            self._emit_optional_block(index, "hdr_ddr_timing", HDR_DDR_CONFIG,
                                      timing_spec.HDR_DDR, hdr_ddr, deep)
        if is_set(daa_table):
            self._emit_optional_block(index, "dyn_addr_table", DAA_TABLE_CONFIG,
                                      DAA_TABLE, daa_table, deep)
        self._emit(f"{inner}),", f"{indent})")

    @staticmethod
    def _config_scalars(device) -> list[str]:
        """The plain arguments of the controller config."""
        args = [f"enabled={1 if device.params.get('enabled', True) else 0}"]
        ibi_amount = device.params.get("receive_ibi_data_amount")
        if ibi_amount is not None:
            args.append(f"receive_ibi_data_amount={int(ibi_amount)}")
        if device.params.get("prohibit_hotjoin"):
            args.append("prohibit_hotjoin=True")
        return args

    def _emit_timing(self, index: int, timing: dict, indent: str) -> None:
        """timing=PxI3CControllerTimingConfig(...) with one entry per phase."""
        self._i3c_names.add(TIMING_CONFIG)
        inner = indent + INDENT
        start = self._here()

        self._emit(f"{indent}timing={TIMING_CONFIG}(")
        for phase in timing_spec.PHASES:
            self._emit_phase(index, phase, timing["phases"][phase], inner)
        for param in timing_spec.SCALARS:
            value = timing["scalars"].get(param.name, param.default)
            if value is None:
                continue  # the deprecated i2c_low / i2c_high default to blank
            self._emit(f"{inner}{param.name}={int(value)},")
        self._emit(f"{indent}),")
        self._mark(f"timing:{index}", start)

    def _emit_phase(self, index: int, phase: str, values: dict, indent: str) -> None:
        """One PxI3CMsgTiming, in simple or advanced form."""
        self._i3c_names.add(MSG_TIMING)
        inner = indent + INDENT
        deep = inner + INDENT
        start = self._here()
        mode = values.get("mode", "simple")

        if mode == "advanced":
            self._i3c_names.add(MSG_ADVANCED_TIMING)
            fields = [p.name for p in timing_spec.phase_params(phase)
                      if p.name not in ("mode", "low", "high")]
            payload = f"advanced={MSG_ADVANCED_TIMING}("
        else:
            self._i3c_names.add(MSG_SIMPLE_TIMING)
            fields = ["low", "high"]
            payload = f"simple={MSG_SIMPLE_TIMING}("

        rendered = ", ".join(f"{f}={int(values.get(f, 0))}" for f in fields)
        single = f"{indent}{phase}={MSG_TIMING}({payload}{rendered})),"
        if len(single) <= MAX_LINE:
            self._emit(single)
        else:
            self._emit(f"{indent}{phase}={MSG_TIMING}(", f"{inner}{payload}")
            self._emit(*[f"{deep}{f}={int(values.get(f, 0))}," for f in fields])
            self._emit(f"{inner}),", f"{indent}),")
        self._mark(f"phase:{index}:{phase}", start)

    def _emit_optional_block(self, index: int, argument: str, cls: str,
                             params: list[Param], block: dict, indent: str) -> None:
        """One all-optional config argument, carrying only the filled fields.

        Used by the HDR timings and the dynamic address table. Every field is
        optional, so a blank one is left out rather than sent as a zero -- a
        zero is an instruction, not an absence.
        """
        self._i3c_names.add(cls)
        inner = indent + INDENT
        start = self._here()
        self._emit(f"{indent}{argument}={cls}(")
        for param in params:
            value = block.get(param.name)
            if value is not None:
                self._emit(f"{inner}{param.name}={render_value(param, value)},")
        self._emit(f"{indent}),")
        self._mark(f"{argument}:{index}", start)

    def _emit_i2c_timing(self, index: int, i2c: dict, indent: str) -> None:
        """i2_c_timing=PxI2CTimingConfig(...)."""
        self._i2c_names.add(I2C_TIMING_CONFIG)
        inner = indent + INDENT
        start = self._here()
        self._emit(f"{indent}i2_c_timing={I2C_TIMING_CONFIG}(")
        for param in timing_spec.I2C_TIMING:
            value = i2c.get(param.name, param.default)
            if value is not None:
                self._emit(f"{inner}{param.name}={int(value)},")
        self._emit(f"{indent}),")
        self._mark(f"i2ctiming:{index}", start)

    # -- steps -----------------------------------------------------------

    def _emit_steps(self, indent: str) -> None:
        active = [(i, s) for i, s in enumerate(self.script.steps) if s.enabled]
        if not active:
            return

        self._emit("", f"{indent}# --- Test sequence ---")
        section = self._here()
        for index, step in active:
            self._emit("")
            start = self._here()
            if step.is_frame:
                self._emit_frame(index, step, indent)
            else:
                self._emit_call(step, indent)
            self._mark(f"step:{index}", start)
        self._mark("steps", section)

    def _emit_frame(self, index: int, step: Step, indent: str) -> None:
        """Emit a transaction list and the send_i3c_transactions() call.

        Unless the frame is one of the CCCs a controller method carries out, in
        which case the call takes the place of the whole list.
        """
        routed = self._daa_routing(step)
        if routed is not None:
            position, op_key = routed
            start = self._here()
            # Emitted exactly as the dedicated step would be, so the two routes
            # to a DAA produce the same script. A comment that is just a frame's
            # own label would read oddly over a method call ("Broadcast CCC"
            # above do_daa()), so it gives way to the operation's label; one the
            # user wrote themselves is kept.
            generic = {spec.label for spec in FRAMES.values()}
            self._emit_call(
                Step(op=op_key, target_var=step.target_var,
                     comment="" if step.comment in generic else step.comment),
                indent,
            )
            self._mark(f"tx:{index}:{position}", start)
            return

        self._message_names.add("I3CControllerTransactionV2")

        name = self._unique("txs")
        self._emit(f"{indent}# {step.comment or 'Controller send'}")
        self._emit(f"{indent}{name} = [")
        # A block that opens a bus segment drives its address phase in open
        # drain; one reached by a repeated start stays push-pull. See
        # _segment_starts for why this is derived rather than asked for.
        starts = self._segment_starts(step)
        for position, transaction in enumerate(step.transactions):
            if not transaction.enabled:
                continue
            start = self._here()
            self._emit_transaction(
                transaction, indent + INDENT, opens_segment=starts[position],
            )
            self._mark(f"tx:{index}:{position}", start)
        self._emit(f"{indent}]")

        # The frame already carries its own comment, so the log just marks that
        # it ran. The response stays bound: it is the return value, and what you
        # would reach for to check what came back.
        result = self._unique("response")
        self._emit(f"{indent}{result} = {step.target_var}.send_i3c_transactions({name})")
        if self.script.log_results:
            self._emit(f'{indent}logger.info("Transaction Finished")')

    def _daa_routing(self, step: Step) -> tuple[int, str] | None:
        """The block to route to a controller method, and which method.

        Only a whole block routes. A primitive carrying the CCC by hand does
        not: those bytes were typed deliberately, and rewriting them as a
        method call would throw the edit away -- _emit_primitive refuses it
        instead.

        The method is a complete bus procedure, with its own start and stop, so
        it cannot sit inside a frame that holds the bus across several
        transfers. Mixed with another transfer, that is an error rather than a
        guess about what was meant.
        """
        routed: tuple[int, str] | None = None
        transfers = 0
        for position, transaction in enumerate(step.transactions):
            if not transaction.enabled or transaction.kind in ("stop", "idle_time"):
                continue
            transfers += 1
            if not is_composite(transaction.kind):
                continue
            ccc = transaction.params.get("ccc")
            if isinstance(ccc, str) and ccc in CCC_VIA_OPERATION:
                routed = (position, CCC_VIA_OPERATION[ccc])

        if routed is None:
            return None
        if transfers > 1:
            short = next(c for c, op in CCC_VIA_OPERATION.items() if op == routed[1])
            raise CodeGenError(self._ccc_mixed_problem(short))
        return routed

    @staticmethod
    def _ccc_mixed_problem(ccc: str) -> str:
        """Why a routed CCC cannot share a frame with another transfer."""
        spec = OPERATIONS[CCC_VIA_OPERATION[ccc]]
        short = ccc.rsplit("_", 1)[-1]
        return (
            f"{short} is carried out by {spec.method}(), a whole bus procedure "
            f"with its own start and stop, so it cannot share a transaction "
            f"with another transfer. Give it a transaction of its own."
        )

    @staticmethod
    def _segment_starts(step: Step) -> list[bool]:
        """Which blocks begin a bus segment rather than continue one.

        A frame runs between one START and one STOP. The first block opens the
        frame, and any block after a STOP opens a new segment -- both arbitrate
        in open drain. A block reached without a STOP in between is a repeated
        start, which stays push-pull.
        """
        starts: list[bool] = []
        opening = True
        for transaction in step.transactions:
            starts.append(opening)
            if not transaction.enabled:
                continue
            if transaction.kind == "stop":
                opening = True
            elif transaction.kind != "idle_time":
                opening = False
        return starts

    def _emit_transaction(
        self, transaction: Transaction, indent: str, *, opens_segment: bool = True,
    ) -> None:
        """Emit one block, expanding a named send into its primitives."""
        if transaction.comment:
            self._emit(f"{indent}# {transaction.comment}")
        pieces = expand(transaction.kind, transaction.params)
        if is_composite(transaction.kind):
            # The policy is not a field on a composite, so it is derived. A
            # primitive placed by hand shows the field, so that choice is left
            # to whoever made it.
            pieces = self._apply_header_policy(pieces, opens_segment)
        for kind, params in pieces:
            self._emit_primitive(kind, params, indent)

    @staticmethod
    def _apply_header_policy(
        pieces: list[tuple[str, dict]], opens_segment: bool,
    ) -> list[tuple[str, dict]]:
        """Set the leading address phase of an expanded block."""
        adjusted = []
        leading = True
        for kind, params in pieces:
            if leading and "address_header_policy" in params:
                params = dict(params)
                params["address_header_policy"] = (
                    "OPEN_DRAIN_FAST" if opens_segment else "PUSH_PULL"
                )
                leading = False
            adjusted.append((kind, params))
        return adjusted

    def _emit_primitive(self, kind: str, params: dict, indent: str) -> None:
        # A builder can tag a transaction with something the reader should know
        # -- an entry that contradicts its CCC, say. It rides in the params but
        # is not a field, so it comes out as a comment.
        note = params.get(NOTE_KEY)
        if note:
            self._emit(f"{indent}# {note}")
        spec = TRANSACTIONS[kind]
        offending = ccc_via_operation(params)
        if offending:
            raise CodeGenError(self._ccc_problem(offending))
        self._message_names.add(spec.message)
        self._message_names.update(MESSAGE_IMPORTS.get(kind, ()))

        args: list[str] = []
        for param in spec.params:
            value = params.get(param.name, param.default)
            if not _should_emit(param, value):
                continue
            if param.type in ("enum", "enum_flags"):
                self._register_enum(param)
            if param.type == "ccc" or (
                param.type in ("bytes", "bytes_literal")
                and any(isinstance(v, str) for v in (value or []))
            ):
                self._needs_ccc = True
            args.append(f"{param.name}={render_value(param, value)}")

        inner = indent + INDENT
        deep = inner + INDENT
        self._emit(f"{indent}I3CControllerTransactionV2(")
        self._emit(f"{inner}{spec.field_name}={spec.message}(")
        self._emit(*[f"{deep}{arg}," for arg in args])
        self._emit(f"{inner}),", f"{indent}),")

    def _emit_call(self, step: Step, indent: str) -> None:
        spec = OPERATIONS[step.op]
        cls_name = self._device_cls(step.target_var)
        self._emit(f"{indent}# {step.comment or spec.label}")

        rendered: dict[str, str] = {}
        args: list[str] = []
        for param in spec.params:
            value = step.params.get(param.name, param.default)
            rendered[param.name] = (
                render_value(param, value, cls_name) if value is not None else "None"
            )
            if _should_emit(param, value):
                args.append(f"{param.name}={rendered[param.name]}")

        self._i3c_names.update(spec.extra_imports)

        if spec.template:
            result = self._unique(spec.returns) if spec.returns else ""
            for line in spec.template:
                self._emit(
                    indent + line.format(receiver=step.target_var, result=result, **rendered)
                )
            if spec.returns and self.script.log_results:
                self._emit(self._log_line(spec, result, indent))
            return

        if spec.returns:
            result = self._unique(spec.returns)
            self._emit(*self._assign_call(result, step.target_var, spec.method, args, indent))
            if self.script.log_results:
                self._emit(self._log_line(spec, result, indent))
        else:
            self._emit(*self._plain_call(step.target_var, spec.method, args, indent))

    @staticmethod
    def _plain_call(var: str, method: str, args: list[str], indent: str) -> list[str]:
        single = f"{indent}{var}.{method}({', '.join(args)})"
        if len(single) <= MAX_LINE or not args:
            return [single]
        return [f"{indent}{var}.{method}("] + [f"{indent}{INDENT}{a}," for a in args] + [f"{indent})"]

    @staticmethod
    def _assign_call(name: str, var: str, method: str, args: list[str], indent: str) -> list[str]:
        single = f"{indent}{name} = {var}.{method}({', '.join(args)})"
        if len(single) <= MAX_LINE:
            return [single]
        lines = [f"{indent}{name} = {var}.{method}("]
        lines += [f"{indent}{INDENT}{arg}," for arg in args]
        lines.append(f"{indent})")
        return lines

    def _log_line(self, spec: OpSpec, name: str, indent: str) -> str:
        label = spec.result_label or spec.label
        if spec.returns in ("data", "i2c_data", "mem_data", "ddr_data"):
            self._needs_format_bytes = True
            return f'{indent}logger.info("{label}: %s", format_bytes({name}))'
        return f'{indent}logger.info("{label}: %s", {name})'

    # -- teardown --------------------------------------------------------

    def _emit_teardown(self, indent: str) -> None:
        """Detach everything, when the script asks for it."""
        if not self.script.devices or not self.script.detach_on_finish:
            return
        self._emit("", f"{indent}# Leave the bus clean for the next run.")
        for device in reversed(self.script.devices):
            spec = DEVICES[device.kind]
            if spec.role == "controller":
                self._emit(
                    f"{indent}if {device.var} is not None:",
                    f"{indent}{INDENT}{device.var}.detach_from_bus()",
                )
            else:
                self._emit(f"{indent}{device.var}.detach_from_bus()")
        self._emit(f'{indent}logger.info("Detached all devices from the bus")')

    # -- helpers emitted into the generated file -------------------------

    def _helper_lines(self) -> list[str]:
        lines: list[str] = []
        if self._needs_format_bytes:
            lines += [
                "",
                "def format_bytes(data: object) -> str:",
                '    """Render a byte sequence as spaced hex, tolerating a missing response."""',
                "    if data is None:",
                '        return "<no data>"',
                '    return " ".join(f"{byte:02X}" for byte in data)',
                "",
            ]
        return lines

    # -- entry point -----------------------------------------------------

    def generate(self) -> str:
        """Return the complete script source, filling in :attr:`anchors`."""
        problems = self.validate()
        if problems:
            raise CodeGenError("\n".join(problems))

        s = self.script
        indent = INDENT * 2

        # The body goes first: it decides which imports and helpers are needed,
        # and it is where every anchor lives.
        self._emit_session(indent)
        self._emit("")
        self._emit_bus(indent)
        self._emit_devices(indent)
        self._emit_steps(indent)
        self._emit_teardown(indent)

        prefix: list[str] = [f'"""{s.description or s.name}"""', ""]
        prefix += self._imports()
        prefix += ["", "", "logger = logging.getLogger(__name__)", ""]
        prefix += self._helper_lines()
        prefix += [
            "",
            "def main() -> None:",
            f'{INDENT}"""Run the generated I3C sequence."""',
        ]

        # Anchors were recorded against the body; shift them into the whole file
        # and make them 1-based, which is what a Tk text widget wants.
        offset = len(prefix)
        self.anchors = {
            node: (start + offset + 1, end + offset + 1)
            for node, (start, end) in self.anchors.items()
        }

        suffix = [
            "",
            "",
            'if __name__ == "__main__":',
            f'{INDENT}logging.basicConfig(format="[%(levelname)s] %(message)s", level=logging.INFO)',
            f"{INDENT}main()",
        ]
        return "\n".join(prefix + self._body + suffix).rstrip() + "\n"


def generate(script: Script) -> str:
    """Generate Python source for `script`."""
    return Generator(script).generate()


def generate_with_anchors(script: Script) -> tuple[str, dict[str, tuple[int, int]]]:
    """Generate the source and the line range each outline node occupies."""
    generator = Generator(script)
    source = generator.generate()
    return source, generator.anchors


def validate(script: Script) -> list[str]:
    """Return problems that would stop `script` from generating."""
    return Generator(script).validate()
