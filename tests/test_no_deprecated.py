"""Nothing the builder offers or emits may be deprecated in aqpxlib.

The deprecated names are read out of the installed library rather than listed
here, so an aqpxlib upgrade that deprecates something new fails this suite
instead of quietly shipping in generated scripts.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pxbuilder import generate, timing  # noqa: E402
from pxbuilder.catalog import DEVICES, OPERATIONS  # noqa: E402
from pxbuilder.flows import FLOWS, device  # noqa: E402
from pxbuilder.model import SEND_FRAME, Script, Step, Transaction  # noqa: E402
from pxbuilder.transactions import FRAMES, TRANSACTIONS, composite_kind  # noqa: E402

pytest.importorskip("aqpxlib", reason="aqpxlib is not installed")


def _warns_deprecated(member: object) -> bool:
    """Whether a callable's own body raises a DeprecationWarning."""
    try:
        source = inspect.getsource(member)  # type: ignore[arg-type]
    except (OSError, TypeError):  # pragma: no cover - builtins
        return False
    return "DeprecationWarning" in source and "warnings.warn" in source


def _deprecated_methods() -> set[str]:
    """Methods of the I3C classes whose body raises a DeprecationWarning."""
    import aqpxlib.i3c as i3c

    found: set[str] = set()
    for spec in DEVICES.values():
        cls = getattr(i3c, spec.cls, None)
        if cls is None:
            continue
        for name, member in vars(cls).items():
            if callable(member) and _warns_deprecated(member):
                found.add(name)
    return found


def _deprecated_message_fields() -> dict[str, set[str]]:
    """Protobuf fields that warn when set, by message name."""
    import aqpxlib.message as message

    found: dict[str, set[str]] = {}
    source = Path(inspect.getfile(message)).read_text(encoding="utf-8")
    for match in re.finditer(r'"(\w+)\.(\w+) is deprecated"', source):
        found.setdefault(match.group(1), set()).add(match.group(2))
    return found


def _every_flow_source() -> str:
    """The generated code for every flow, concatenated."""
    return "\n".join(generate(flow.as_script()) for flow in FLOWS.values())


def _kitchen_sink() -> Script:
    """One script exercising every device, block and operation the GUI offers."""
    script = Script(name="everything")
    script.devices = [device("controller")]
    for kind, spec in DEVICES.items():
        if spec.role == "target":
            script.devices.append(device(kind))

    block = timing.default_timing()
    block["enabled"] = True
    for phase in block["phases"]:
        block["phases"][phase]["mode"] = "advanced"
    script.devices[0].params["timing"] = block
    i2c = timing.default_i2c_timing()
    i2c["enabled"] = True
    script.devices[0].params["i2c_timing"] = i2c

    frame = Step(op=SEND_FRAME, target_var="i3c_controller", transactions=[])
    for key in FRAMES:
        params = {p.name: p.default for p in FRAMES[key].params}
        frame.transactions.append(Transaction(composite_kind(key), params))
    # A STOP ends the frame and an Idle Time may not, so those two go last in
    # the right order rather than in dictionary order.
    tail = ("stop", "idle_time")
    for kind in [k for k in TRANSACTIONS if k not in tail]:
        params = {p.name: p.default for p in TRANSACTIONS[kind].params}
        frame.transactions.append(Transaction(kind, params))
    idle = {p.name: p.default for p in TRANSACTIONS["idle_time"].params}
    frame.transactions.insert(1, Transaction("idle_time", idle))
    stop = {p.name: p.default for p in TRANSACTIONS["stop"].params}
    frame.transactions.append(Transaction("stop", stop))
    script.steps.append(frame)

    from pxbuilder.catalog import device_matches
    from pxbuilder.flows import call

    for key, spec in OPERATIONS.items():
        target = next(
            (d.var for d in script.devices if device_matches(d.kind, spec.applies_to)),
            None,
        )
        if target:
            script.steps.append(call(key, target))
    return script


# --------------------------------------------------------------------------


def test_the_deprecation_scan_actually_detects_a_deprecation() -> None:
    """Guard the guard: a scan that cannot detect anything passes vacuously.

    This used to name a real deprecated method, but 0.7.0 dropped the last of
    them from the I3C classes, so an empty result is now legitimate. Point the
    detector at a known-deprecated callable of our own instead -- that keeps
    the guard honest without betting on what upstream still has deprecated.
    """

    def deprecated_thing() -> None:
        warnings.warn("gone in the next one", DeprecationWarning, stacklevel=2)

    def ordinary_thing() -> None:
        return None

    assert _warns_deprecated(deprecated_thing)
    assert not _warns_deprecated(ordinary_thing)

    # The message-field scan still has real teeth: i2_c_low / i2_c_high on the
    # controller timing message are deprecated upstream to this day.
    fields = _deprecated_message_fields()
    assert "I3CControllerTimingConfig" in fields


def test_no_flow_calls_a_deprecated_method() -> None:
    source = _every_flow_source()
    for name in _deprecated_methods():
        assert f".{name}(" not in source, f"a flow calls the deprecated {name}()"


def test_the_full_surface_calls_no_deprecated_method() -> None:
    source = generate(_kitchen_sink())
    ast.parse(source)
    for name in _deprecated_methods():
        assert f".{name}(" not in source, f"the builder emits the deprecated {name}()"


def test_the_full_surface_sets_no_deprecated_field() -> None:
    source = generate(_kitchen_sink())
    for message, fields in _deprecated_message_fields().items():
        if message not in source:
            continue
        for field in fields:
            assert f"{field}=" not in source, (
                f"the builder sets {message}.{field}, which aqpxlib deprecates"
            )


def test_no_catalog_parameter_is_deprecated() -> None:
    """Nothing deprecated should even be offered as a field in the GUI."""
    deprecated = {f for fields in _deprecated_message_fields().values() for f in fields}
    offered = {p.name for spec in TRANSACTIONS.values() for p in spec.params}
    offered |= {p.name for spec in FRAMES.values() for p in spec.params}
    offered |= {p.name for spec in DEVICES.values() for p in spec.params}
    offered |= {p.name for spec in OPERATIONS.values() for p in spec.params}
    offered |= {p.name for p in timing.SCALARS}
    offered |= {p.name for p in timing.I2C_TIMING}
    assert not offered & deprecated, sorted(offered & deprecated)


def test_no_catalog_operation_is_deprecated() -> None:
    deprecated = _deprecated_methods()
    used = {spec.method for spec in OPERATIONS.values()}
    assert not used & deprecated, sorted(used & deprecated)


def test_building_every_flow_config_raises_no_warning() -> None:
    """Generating must not itself trip a DeprecationWarning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        generate(_kitchen_sink())
        for flow in FLOWS.values():
            generate(flow.as_script())
