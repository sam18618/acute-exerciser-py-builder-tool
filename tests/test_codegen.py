"""Checks that the builder emits valid code matching the real aqpxlib API.

The signature tests import aqpxlib and are skipped when it is not installed, so
the suite still runs on a machine that only has the builder.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pxbuilder import generate, validate  # noqa: E402
from pxbuilder import values  # noqa: E402
from pxbuilder.catalog import (  # noqa: E402
    DEVICES,
    OPERATIONS,
    Param,
    default_params,
    device_matches,
)
from pxbuilder.codegen import CodeGenError  # noqa: E402
from pxbuilder.flows import FLOWS, call, device, frame  # noqa: E402
from pxbuilder.model import SEND_FRAME, Script, Step, Transaction  # noqa: E402
from pxbuilder.transactions import (  # noqa: E402
    CCC_CODES,
    FRAMES,
    TRANSACTIONS,
    build_frame,
    block_info,
    composite_kind,
    expand,
    frame_body,
    is_composite,
)

aqpxlib = pytest.importorskip("aqpxlib", reason="aqpxlib is not installed")


CTRL = "i3c_controller"


def _controller_script() -> Script:
    script = Script(name="probe")
    script.devices = [device("controller")]
    return script


# --------------------------------------------------------------------------
# Area 3 -- flows
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(FLOWS))
def test_flows_generate_valid_python(key: str) -> None:
    script = FLOWS[key].as_script()
    assert validate(script) == []
    ast.parse(generate(script))


def test_round_trip_through_project_file(tmp_path: Path) -> None:
    original = FLOWS["discovery"].as_script()
    path = tmp_path / "p.pxb.json"
    original.save(path)
    assert generate(Script.load(path)) == generate(original)


def test_project_file_preserves_transactions(tmp_path: Path) -> None:
    original = FLOWS["private_loopback"].as_script()
    path = tmp_path / "p.pxb.json"
    original.save(path)
    reloaded = Script.load(path)
    frames = [s for s in reloaded.steps if s.is_frame]
    assert frames and all(f.transactions for f in frames)
    assert all(isinstance(t, Transaction) for f in frames for t in f.transactions)


# --------------------------------------------------------------------------
# Area 2 -- controller sends as V2 frames
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(FRAMES))
def test_every_frame_generates_valid_python(key: str) -> None:
    script = _controller_script()
    script.steps = [frame(key, CTRL)]
    assert validate(script) == []
    source = generate(script)
    ast.parse(source)
    assert "I3CControllerTransactionV2(" in source
    assert "send_i3c_transactions(" in source


@pytest.mark.parametrize("key", sorted(TRANSACTIONS))
def test_every_transaction_primitive_generates(key: str) -> None:
    spec = TRANSACTIONS[key]
    block = Transaction(key, {p.name: p.default for p in spec.params})
    script = _controller_script()
    if key == "idle_time":
        # A gap only means something between two transfers.
        blocks = [_primitive("sdr_write"), block, _primitive("sdr_read"),
                  _primitive("stop")]
    else:
        blocks = [block]
    script.steps = [Step(op=SEND_FRAME, target_var=CTRL, transactions=blocks)]
    assert validate(script) == []
    source = generate(script)
    ast.parse(source)
    assert f"{spec.field_name}={spec.message}(" in source


def test_frame_with_no_transactions_is_rejected() -> None:
    script = _controller_script()
    script.steps = [Step(op=SEND_FRAME, target_var=CTRL, transactions=[])]
    assert any("no transactions" in p for p in validate(script))


def test_frame_on_a_target_is_rejected() -> None:
    script = _controller_script()
    script.devices.append(device("target_32bit_register"))
    script.steps = [frame("private_write", "i3c_target")]
    assert any("not a controller" in p for p in validate(script))


def test_disabled_transaction_is_omitted() -> None:
    script = _controller_script()
    step = frame("private_write", CTRL)
    step.transactions[0].enabled = False
    script.steps = [step]
    source = generate(script)
    # The private write drops its 0x7E header transaction but keeps the rest.
    assert source.count("I3CControllerTransactionV2(") == len(step.transactions) - 1


def test_ccc_is_emitted_as_a_named_constant() -> None:
    script = _controller_script()
    script.steps = [frame("direct_ccc", CTRL, ccc="I3C_CCC_DIRECT_GETBCR")]
    source = generate(script)
    assert "bytes([PxI3CCommonCommandCode.I3C_CCC_DIRECT_GETBCR])" in source
    assert "from aqpxlib.i3c.ccc import PxI3CCommonCommandCode" in source


def test_hdr_exit_is_a_stop_transaction() -> None:
    script = _controller_script()
    script.steps = [frame("hdr_exit", CTRL)]
    source = generate(script)
    assert "I3CStop(" in source
    assert "type=I3CStopType.HDR_EXIT" in source


def test_target_reset_is_a_stop_transaction() -> None:
    script = _controller_script()
    script.steps = [frame("target_reset_pattern", CTRL)]
    assert "type=I3CStopType.TARGET_RESET" in generate(script)


def test_private_write_without_broadcast_header() -> None:
    with_header = build_frame("private_write", {"with_broadcast_hdr": True})
    without = build_frame("private_write", {"with_broadcast_hdr": False})
    assert len(with_header) == len(without) + 1


def test_frame_variables_are_unique() -> None:
    script = _controller_script()
    script.steps = [frame("private_read", CTRL), frame("private_read", CTRL)]
    source = generate(script)
    assert "txs = [" in source
    assert "txs_2 = [" in source
    assert "response_2 = " in source


# --------------------------------------------------------------------------
# Area 2 -- direct calls (target events, I2C, bus management)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("op_key", sorted(OPERATIONS))
def test_every_operation_generates_valid_python(op_key: str) -> None:
    spec = OPERATIONS[op_key]
    script = _controller_script()
    for kind, device_spec in DEVICES.items():
        if device_spec.role == "target":
            script.devices.append(device(kind))
    target = next(
        d.var for d in script.devices
        if DEVICES[d.kind].role in spec.applies_to or d.kind in spec.applies_to
    )
    script.steps = [call(op_key, target)]
    assert validate(script) == []
    ast.parse(generate(script))


def test_target_only_operation_rejects_a_controller() -> None:
    script = _controller_script()
    script.steps = [call("ibi_request", CTRL)]
    assert any("not available on" in p for p in validate(script))


def test_fifo_only_operation_rejects_a_register_target() -> None:
    script = _controller_script()
    script.devices.append(device("target_32bit_register"))
    script.steps = [call("target_enqueue", "i3c_target")]
    assert any("not available on" in p for p in validate(script))


def test_ibi_enum_uses_the_target_class() -> None:
    script = _controller_script()
    script.devices.append(device("target_fifo"))
    script.steps = [call("ibi_request", "i3c_fifo_target")]
    assert "PxI3CFifoTarget.IbiType.IMMEDIATE" in generate(script)


def test_register_value_is_emitted_as_an_assignment() -> None:
    script = _controller_script()
    script.devices.append(device("target_32bit_register"))
    script.steps = [call("target_set_register", "i3c_target", value=0xABCD)]
    assert "i3c_target.register_value = 0xABCD" in generate(script)


def test_missing_device_is_reported() -> None:
    script = FLOWS["private_loopback"].as_script()
    script.steps[0].target_var = "nope"
    assert any("nope" in p for p in validate(script))


# --------------------------------------------------------------------------
# Value parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DE AD BE EF", [0xDE, 0xAD, 0xBE, 0xEF]),
        ("0xDE, 0xAD", [0xDE, 0xAD]),
        ("[08 09]", [8, 9]),
        ("", []),
        ("I3C_CCC_DIRECT_GETBCR", ["I3C_CCC_DIRECT_GETBCR"]),
    ],
)
def test_parse_int_list(text: str, expected: list[object]) -> None:
    assert values.parse_int_list(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A byte array is an array of bytes: a token wider than one is split.
        ("1234", [0x12, 0x34]),
        ("0x1234", [0x12, 0x34]),
        ("DEADBEEF", [0xDE, 0xAD, 0xBE, 0xEF]),
        # Taken two digits at a time, so a leading zero stays a byte of its own
        # -- 0x00 0x40 on the wire is not 0x40.
        ("0040", [0x00, 0x40]),
        ("0x0040", [0x00, 0x40]),
        # An odd count of digits pads on the left.
        ("123", [0x01, 0x23]),
        # One byte each, as before.
        ("10", [0x10]),
        ("FF 00", [0xFF, 0x00]),
    ],
)
def test_a_byte_array_splits_a_token_into_bytes(
    text: str, expected: list[int],
) -> None:
    assert values.parse_int_list(text) == expected


def test_byte_arrays_and_hex_fields_read_as_hex() -> None:
    """The fields are shown as hex, so 10 in one of them is 0x10, not ten."""
    address = Param("address", "Address", "hex", default=8, always_emit=True)
    data = Param("data", "Data", "bytes", default=[], always_emit=True)

    assert values.from_text(address, "10") == 0x10
    assert values.from_text(address, "0x10") == 0x10
    assert values.to_text(address, values.from_text(address, "20")) == "0x20"
    assert values.from_text(data, "10 20") == [0x10, 0x20]

    # A plain int field is still decimal: timings are given in nanoseconds.
    nanoseconds = Param("low", "Low", "int", default=0, always_emit=True)
    assert values.from_text(nanoseconds, "10") == 10


def test_every_number_in_a_ccc_frame_is_hex() -> None:
    """Addresses, command codes, payloads and counts in one base."""
    for key in ("broadcast_ccc", "direct_ccc", "direct_ccc_write", "direct_ccc_read"):
        for param in FRAMES[key].params:
            assert param.type not in ("int", "int_list"), f"{key}.{param.name}"


def test_a_hex_field_rejects_what_is_not_hex() -> None:
    data = Param("data", "Data", "bytes", default=[], always_emit=True)
    with pytest.raises(values.ValueError_):
        values.from_text(data, "GG")


def test_parse_addr_pairs() -> None:
    assert values.parse_addr_pairs("08>09, 0A->0B") == [[8, 9], [10, 11]]


def test_parse_pairs() -> None:
    assert values.parse_pairs("08: DE AD; 0A: BE") == [[8, [0xDE, 0xAD]], [10, [0xBE]]]


def test_parse_int_rejects_nonsense() -> None:
    with pytest.raises(values.ValueError_):
        values.parse_int("zz!", label="addr")


# --------------------------------------------------------------------------
# Catalog vs. the real library
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(DEVICES))
def test_device_classes_exist(key: str) -> None:
    import aqpxlib.i3c as i3c

    spec = DEVICES[key]
    cls = getattr(i3c, spec.cls, None)
    assert cls is not None, f"{spec.cls} is missing from aqpxlib.i3c"

    # A subclass may forward the inherited arguments through **kwargs rather
    # than re-declaring them -- PxI3CMctpEndpoint names only `dcr` -- so the
    # whole MRO counts as the device's signature.
    accepted: set[str] = set()
    for base in cls.__mro__:
        init = base.__dict__.get("__init__")
        if init is not None:
            accepted |= set(inspect.signature(init).parameters)

    for param in spec.params:
        if key == "controller":
            continue  # `enabled` is folded into the controller config, not a kwarg
        assert param.name in accepted, (
            f"{spec.cls} has no parameter {param.name!r}"
        )


@pytest.mark.parametrize("op_key", sorted(OPERATIONS))
def test_operation_signatures_match(op_key: str) -> None:
    """Every catalog parameter must be a real argument of the real method."""
    import aqpxlib.i3c as i3c

    spec = OPERATIONS[op_key]
    owners = [
        getattr(i3c, DEVICES[k].cls)
        for k in DEVICES
        if DEVICES[k].role in spec.applies_to or k in spec.applies_to
    ]
    assert owners, f"{op_key} applies to nothing"

    for owner in owners:
        method = getattr(owner, spec.method, None)
        assert method is not None, f"{owner.__name__} has no {spec.method!r}"
        if isinstance(inspect.getattr_static(owner, spec.method), property):
            continue  # register_value is a property, not a call

        signature = inspect.signature(method)
        for param in spec.params:
            assert param.name in signature.parameters, (
                f"{owner.__name__}.{spec.method}() has no parameter {param.name!r}"
            )
        catalog_names = {p.name for p in spec.params}
        for name, sig_param in signature.parameters.items():
            if name in ("self", "args", "kwargs"):
                continue
            if sig_param.default is inspect.Parameter.empty:
                assert name in catalog_names, (
                    f"{owner.__name__}.{spec.method}() requires {name!r} "
                    "but the catalog does not expose it"
                )


@pytest.mark.parametrize("key", sorted(TRANSACTIONS))
def test_transaction_messages_match_protobuf(key: str) -> None:
    """Each transaction's fields must exist on the real protobuf message."""
    import aqpxlib.message as message
    from aqpxlib.message import I3CControllerTransactionV2

    spec = TRANSACTIONS[key]
    assert spec.field_name in I3CControllerTransactionV2.__dataclass_fields__, (
        f"I3CControllerTransactionV2 has no field {spec.field_name!r}"
    )
    cls = getattr(message, spec.message, None)
    assert cls is not None, f"aqpxlib.message has no {spec.message!r}"
    for param in spec.params:
        assert param.name in cls.__dataclass_fields__, (
            f"{spec.message} has no field {param.name!r}"
        )


@pytest.mark.parametrize("key", sorted(TRANSACTIONS))
def test_transaction_messages_actually_construct(key: str) -> None:
    """Build the real protobuf object with the catalog defaults."""
    import aqpxlib.message as message
    from aqpxlib.message import I3CControllerTransactionV2

    spec = TRANSACTIONS[key]
    kwargs = {}
    for param in spec.params:
        if param.default is None:
            continue
        if param.type in ("bytes", "bytes_literal"):
            kwargs[param.name] = bytes(v for v in param.default if isinstance(v, int))
        elif param.type == "enum":
            kwargs[param.name] = getattr(getattr(message, param.enum_prefix), param.default)
        else:
            kwargs[param.name] = param.default
    payload = getattr(message, spec.message)(**kwargs)
    I3CControllerTransactionV2(**{spec.field_name: payload})


def test_ccc_constants_are_in_sync() -> None:
    """The hard-coded CCC list must match the library's enum."""
    from aqpxlib.i3c.ccc import PxI3CCommonCommandCode

    assert set(CCC_CODES) == {member.name for member in PxI3CCommonCommandCode}


@pytest.mark.parametrize("op_key", sorted(OPERATIONS))
def test_enum_values_exist(op_key: str) -> None:
    """Every enum choice offered in the GUI must exist on the real class."""
    import aqpxlib.i3c as i3c

    spec = OPERATIONS[op_key]
    for param in spec.params:
        if param.type != "enum":
            continue
        owners = (
            [DEVICES[k].cls for k in DEVICES
             if DEVICES[k].role in spec.applies_to or k in spec.applies_to]
            if "{cls}" in param.enum_prefix
            else [param.enum_prefix.split(".")[0]]
        )
        enum_name = param.enum_prefix.split(".")[-1]
        for cls_name in owners:
            enum_cls = getattr(getattr(i3c, cls_name), enum_name)
            missing = set(param.choices) - {m.name for m in enum_cls}
            assert not missing, f"{cls_name}.{enum_name} has no member(s) {sorted(missing)}"


@pytest.mark.parametrize("key", sorted(TRANSACTIONS))
def test_transaction_enum_values_exist(key: str) -> None:
    import aqpxlib.message as message

    for param in TRANSACTIONS[key].params:
        if param.type != "enum":
            continue
        enum_cls = getattr(message, param.enum_prefix)
        missing = set(param.choices) - {m.name for m in enum_cls}
        assert not missing, f"{param.enum_prefix} has no member(s) {sorted(missing)}"


# --------------------------------------------------------------------------
# Targets: FIFO and the 256-byte memory
# --------------------------------------------------------------------------


def test_every_i3c_target_class_is_offered() -> None:
    labels = {k: DEVICES[k].label for k in DEVICES if DEVICES[k].role == "target"}
    assert labels == {
        "target_32bit_register": "32-Bit Register Target",
        "target_8bit_memory": "256-Byte Memory Target",
        "target_fifo": "FIFO Target",
        "target_mctp_endpoint": "MCTP Endpoint",
    }


def test_the_memory_target_really_is_256_bytes() -> None:
    """The label must not drift from what the library documents."""
    from aqpxlib.i3c import PxI3C8BitAddressMemoryTarget

    assert "256-byte" in (PxI3C8BitAddressMemoryTarget.__doc__ or "").lower()


# -- ENTDAA and RSTDAA must go through the controller methods ---------------


def test_the_daa_cccs_are_not_offered_inside_a_transaction() -> None:
    """They are added from the Transactions list instead -- see the GUI tests.

    Not being on the menu is not the same as being dropped: CCC_CODES mirrors
    the enum and is tested against it, so they stay there.
    """
    from pxbuilder.transactions import CCC_VIA_OPERATION, FRAMES

    for key, spec in FRAMES.items():
        for param in spec.params:
            if param.name != "ccc":
                continue
            offered = [c for c in param.choices if c in CCC_VIA_OPERATION]
            assert not offered, f"{key} still offers {offered}"

    for ccc in CCC_VIA_OPERATION:
        assert ccc in CCC_CODES


def test_a_directed_rstdaa_is_not_routed() -> None:
    """It resets one target and aqpxlib has no method for it, so a frame is right."""
    from pxbuilder.transactions import CCC_VIA_OPERATION

    assert "I3C_CCC_DIRECT_RSTDAA" not in CCC_VIA_OPERATION

    script = _controller_script()
    script.devices.append(device("target_fifo"))
    script.steps = [frame("direct_ccc_write", CTRL,
                          ccc="I3C_CCC_DIRECT_RSTDAA", address=0x08)]
    source = generate(script)
    ast.parse(source)
    assert "I3C_CCC_DIRECT_RSTDAA" in source
    assert "send_i3c_transactions" in source


def _hand_placed(kind: str, **params: object) -> Step:
    """A frame holding one hand-placed primitive, bypassing the palette."""
    values = default_params(TRANSACTIONS[kind].params)
    values.update(params)
    step = Step(op=SEND_FRAME, target_var=CTRL)
    step.transactions = [Transaction(kind, values)]
    return step


def _ccc_block(ccc: str, *extra: Transaction) -> Step:
    """A Broadcast CCC block the way the GUI stores one, plus its STOP."""
    params = default_params(FRAMES["broadcast_ccc"].params)
    params["ccc"] = ccc
    blocks = [Transaction(composite_kind("broadcast_ccc"), params), *extra]
    blocks.append(Transaction("stop", default_params(TRANSACTIONS["stop"].params)))
    step = Step(op=SEND_FRAME, target_var=CTRL, comment="Broadcast CCC")
    step.transactions = blocks
    return step


@pytest.mark.parametrize(("ccc", "method", "result"), [
    ("I3C_CCC_BROADCAST_ENTDAA", "do_daa", "daa_ok"),
    ("I3C_CCC_BROADCAST_RSTDAA", "do_rstdaa", "rstdaa_ok"),
])
def test_a_block_carrying_a_daa_ccc_becomes_the_method_call(
    ccc: str, method: str, result: str,
) -> None:
    """A project saved while these were on the CCC menu still generates right.

    They cannot be chosen there any more, but a stored block must not turn
    into a frame that addresses nobody, so the routing stays.
    """
    script = _controller_script()
    script.steps = [_ccc_block(ccc)]

    assert validate(script) == []
    source = generate(script)
    ast.parse(source)
    assert f"{result} = {CTRL}.{method}()" in source
    # Nothing of the frame survives: no command byte, no send.
    assert "send_i3c_transactions" not in source
    assert "PxI3CCommonCommandCode" not in source
    assert "I3CControllerTransactionV2" not in source


def test_the_routed_call_matches_the_dedicated_step() -> None:
    """Both routes to a DAA have to produce the same script."""
    via_block = _controller_script()
    via_block.steps = [_ccc_block("I3C_CCC_BROADCAST_ENTDAA")]

    via_step = _controller_script()
    via_step.steps = [call("do_daa", CTRL)]

    assert generate(via_block) == generate(via_step)


def test_a_generic_frame_label_gives_way_to_the_operation_label() -> None:
    script = _controller_script()
    script.steps = [_ccc_block("I3C_CCC_BROADCAST_ENTDAA")]
    assert f"# {OPERATIONS['do_daa'].label}" in generate(script)


def test_a_comment_of_your_own_is_kept() -> None:
    script = _controller_script()
    step = _ccc_block("I3C_CCC_BROADCAST_ENTDAA")
    step.comment = "Address everything from scratch"
    script.steps = [step]
    assert "# Address everything from scratch" in generate(script)


def test_a_routed_ccc_cannot_share_a_frame_with_another_transfer() -> None:
    """The method is a whole bus procedure, so it cannot sit inside a frame."""
    other = Transaction(composite_kind("private_write"),
                        default_params(FRAMES["private_write"].params))
    script = _controller_script()
    script.devices.append(device("target_fifo"))
    script.steps = [_ccc_block("I3C_CCC_BROADCAST_ENTDAA", other)]

    problems = validate(script)
    assert problems and "do_daa()" in problems[0]
    with pytest.raises(CodeGenError, match="transaction of its own"):
        generate(script)


def test_a_stop_and_an_idle_do_not_count_as_another_transfer() -> None:
    """They carry no transfer of their own, so the block still routes."""
    idle = Transaction("idle_time", default_params(TRANSACTIONS["idle_time"].params))
    script = _controller_script()
    script.steps = [_ccc_block("I3C_CCC_BROADCAST_ENTDAA", idle)]
    assert validate(script) == []
    assert "do_daa()" in generate(script)


@pytest.mark.parametrize(("ccc", "method"), [
    ("I3C_CCC_BROADCAST_ENTDAA", "do_daa"),
    ("I3C_CCC_BROADCAST_RSTDAA", "do_rstdaa"),
])
def test_hand_built_bytes_for_a_daa_ccc_are_refused(ccc: str, method: str) -> None:
    """An expanded block cannot be routed -- those bytes were typed on purpose.

    Rewriting them as a method call would throw the edit away, so this is an
    error naming both ways to do it properly instead.
    """
    script = _controller_script()
    script.steps = [_hand_placed("sdr_write", address=0x7E, data=[ccc])]

    problems = validate(script)
    assert problems, "validate said nothing"
    assert method in problems[0]
    assert "raw bytes" in problems[0]

    with pytest.raises(CodeGenError, match=method):
        generate(script)


def test_the_proper_steps_generate_as_before() -> None:
    """The replacement path has to be the one the flows already use."""
    script = _controller_script()
    script.steps = [call("do_rstdaa", CTRL), call("do_daa", CTRL)]
    source = generate(script)
    ast.parse(source)
    assert "do_rstdaa()" in source
    assert "do_daa()" in source
    # Neither goes near a frame: no command byte, no send_i3c_transactions.
    assert "PxI3CCommonCommandCode" not in source
    assert "send_i3c_transactions" not in source


# -- dynamic address assignment --------------------------------------------


def test_do_daa_takes_no_argument_by_default() -> None:
    script = _controller_script()
    script.steps = [call("do_daa", CTRL)]
    source = generate(script)
    ast.parse(source)
    assert "do_daa()" in source


def test_do_daa_emits_a_cap_when_one_is_given() -> None:
    script = _controller_script()
    step = call("do_daa", CTRL)
    step.params["max_assignments"] = 4
    script.steps = [step]
    source = generate(script)
    ast.parse(source)
    assert "do_daa(max_assignments=4)" in source


def test_do_rstdaa_still_takes_nothing() -> None:
    """It gained no arguments in 0.7 -- pin that, so the catalog stays honest."""
    import inspect

    from aqpxlib.i3c import PxI3CBaseController

    parameters = inspect.signature(PxI3CBaseController.do_rstdaa).parameters
    assert list(parameters) == ["self"]
    assert OPERATIONS["do_rstdaa"].params == []


def test_the_daa_table_is_emitted_only_once_something_is_filled() -> None:
    script = _controller_script()
    assert "dyn_addr_table" not in generate(script)

    script.devices[0].params["dyn_addr_table"] = {"priority_addr_table": [0x08, 0x09]}
    source = generate(script)
    ast.parse(source)
    assert "dyn_addr_table=PxI3CControllerDynamicAssignmentTable(" in source
    assert "priority_addr_table=bytes([0x08, 0x09])" in source
    # The masks were left blank, so they stay out rather than going as zeros.
    for absent in ("reserved_addr_mask_l=", "used_addr_mask_h="):
        assert absent not in source


def test_the_daa_masks_render_as_hex() -> None:
    script = _controller_script()
    script.devices[0].params["dyn_addr_table"] = {"reserved_addr_mask_l": 0x0F}
    source = generate(script)
    ast.parse(source)
    assert "reserved_addr_mask_l=0x0F," in source


def test_no_flow_emits_a_daa_table() -> None:
    for key, flow in FLOWS.items():
        assert "dyn_addr_table=" not in generate(flow.as_script()), key


def test_the_daa_table_fields_exist_on_the_real_config() -> None:
    from aqpxlib.i3c import PxI3CBaseControllerConfig, PxI3CControllerDynamicAssignmentTable
    from pxbuilder.catalog import DAA_TABLE

    assert "dyn_addr_table" in PxI3CBaseControllerConfig.__dataclass_fields__
    fields = PxI3CControllerDynamicAssignmentTable.__dataclass_fields__
    for param in DAA_TABLE:
        assert param.name in fields, f"the table has no field {param.name!r}"


def test_the_rendered_daa_table_constructs_for_real() -> None:
    from aqpxlib.i3c import PxI3CControllerDynamicAssignmentTable as Table

    table = Table(priority_addr_table=bytes([0x08, 0x09]), reserved_addr_mask_l=0x0F)
    assert table.priority_addr_table == bytes([0x08, 0x09])
    assert table.reserved_addr_mask_l == 0x0F
    # entries is the part the builder does not offer yet; it must stay empty.
    assert table.entries == ()


# -- the MCTP endpoint, new in aqpxlib 0.7 ---------------------------------


def test_the_mctp_endpoint_offers_the_dcr_the_library_identifies_it_by() -> None:
    """0xCC is not a magic number we chose -- it is how get_targets() matches."""
    from aqpxlib.i3c.i3c_mctp_endpoint import MCTP_DCR

    dcr = next(p for p in DEVICES["target_mctp_endpoint"].params if p.name == "dcr")
    assert dcr.default == MCTP_DCR
    from aqpxlib.i3c import PxI3CMctpEndpoint

    assert PxI3CMctpEndpoint.is_mctp_dcr(dcr.default)


def test_the_mctp_endpoint_is_a_fifo_target() -> None:
    """It subclasses PxI3CFifoTarget, so the FIFO operations must reach it."""
    from aqpxlib.i3c import PxI3CFifoTarget, PxI3CMctpEndpoint

    assert issubclass(PxI3CMctpEndpoint, PxI3CFifoTarget)
    for key in ("target_enqueue", "target_fifo_capacity", "target_fifo_fill",
                "target_total_read", "target_total_written"):
        assert device_matches("target_mctp_endpoint", OPERATIONS[key].applies_to), (
            f"{key} does not reach the MCTP endpoint"
        )


def test_mctp_only_operations_are_refused_elsewhere() -> None:
    for key in ("target_send_mctp_packet", "target_mctp_address"):
        applies = OPERATIONS[key].applies_to
        assert device_matches("target_mctp_endpoint", applies)
        assert not device_matches("target_fifo", applies)
        assert not device_matches("target_32bit_register", applies)


def test_sending_an_mctp_packet_generates() -> None:
    script = Script(name="mctp")
    script.devices = [device("controller"), device("target_mctp_endpoint")]
    script.steps = [call("target_send_mctp_packet", "i3c_mctp_endpoint")]
    source = generate(script)
    ast.parse(source)
    assert "PxI3CMctpEndpoint(" in source
    assert "packet=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06]" in source
    # Both of these are always emitted: the PEC decision must be visible in the
    # script rather than left to a default the reader cannot see.
    assert "skip_pec_check=True" in source
    # These two match the library default, so they stay out.
    assert "announce=" not in source
    assert "unit=" not in source


def test_the_default_mctp_packet_clears_the_length_floor() -> None:
    """The library refuses a frame under 5 bytes, 6 with SOM."""
    from aqpxlib.i3c.i3c_mctp_endpoint import i3c_frame_too_short

    packet = next(p for p in OPERATIONS["target_send_mctp_packet"].params
                  if p.name == "packet")
    assert not i3c_frame_too_short(bytes(packet.default))


def test_announcing_and_a_unit_are_emitted_when_set() -> None:
    script = Script(name="mctp")
    script.devices = [device("controller"), device("target_mctp_endpoint")]
    step = call("target_send_mctp_packet", "i3c_mctp_endpoint")
    step.params["announce"] = False
    step.params["unit"] = 128
    script.steps = [step]
    source = generate(script)
    ast.parse(source)
    assert "announce=False" in source
    assert "unit=128" in source


# -- target reset, new in aqpxlib 0.7 --------------------------------------


@pytest.mark.parametrize("kind", ["target_fifo", "target_8bit_memory",
                                  "target_32bit_register"])
def test_reset_actions_render_as_an_or_chain(kind: str) -> None:
    script = Script(name="probe")
    script.devices = [device(kind, reset_pin=7, reset_actions=["MWL", "DATA"])]
    source = generate(script)
    ast.parse(source)
    assert "reset_pin=7" in source
    assert ("reset_actions=PxI3CTargetResetAction.MWL "
            "| PxI3CTargetResetAction.DATA") in source
    # The enum is a protocol-package name in 0.7, not an aqpxlib.message one.
    assert "PxI3CTargetResetAction" in source.split("logger = ")[0]
    assert "from aqpxlib.message import" not in source


def test_reset_settings_are_omitted_when_untouched() -> None:
    """Blank must leave the arguments out, not send a zero.

    An omitted reset_actions means "everything except DATA" to the library. A
    literal 0 would mean a reset that does nothing, which is not the same, so a
    blank field has to produce no argument at all.
    """
    script = Script(name="probe")
    script.devices = [device("target_fifo")]
    source = generate(script)
    assert "reset_actions" not in source
    assert "reset_pin" not in source


def test_reset_action_choices_exist_on_the_real_enum() -> None:
    from aqpxlib.i3c import PxI3CTargetResetAction

    param = next(p for p in DEVICES["target_fifo"].params if p.name == "reset_actions")
    members = {m.name for m in PxI3CTargetResetAction}
    assert set(param.choices) <= members
    # UNSPECIFIED is the library's default, not something to offer.
    assert "UNSPECIFIED" not in param.choices


def test_the_library_default_really_is_everything_except_data() -> None:
    """Pin the claim the GUI help text makes about a blank field."""
    from aqpxlib.i3c import PxI3CTargetResetAction as Action

    param = next(p for p in DEVICES["target_fifo"].params if p.name == "reset_actions")
    everything_but_data = 0
    for name in param.choices:
        if name != "DATA":
            everything_but_data |= int(getattr(Action, name))
    assert everything_but_data == 0x1F
    assert "except DATA" in param.help


@pytest.mark.parametrize("kind", ["target_fifo", "target_8bit_memory",
                                  "target_32bit_register"])
def test_the_rendered_reset_actions_are_what_the_config_takes(kind: str) -> None:
    """The OR chain the builder writes has to construct for real."""
    import aqpxlib.i3c as i3c
    from aqpxlib.i3c import PxI3CTargetResetAction as Action

    config_cls = getattr(i3c, DEVICES[kind].cls + "Config")
    config = config_cls(reset_pin=7, reset_actions=Action.MWL | Action.DATA)
    assert config.reset_pin == 7
    assert config.reset_actions == int(Action.MWL) + int(Action.DATA)


@pytest.mark.parametrize(("text", "expected"), [
    ("MWL | DATA", ["MWL", "DATA"]),
    ("data,mwl", ["MWL", "DATA"]),
    ("", None),
])
def test_reset_actions_parse_from_text(text: str, expected: list[str] | None) -> None:
    """Typed text normalises to catalog order, so the output is stable."""
    param = next(p for p in DEVICES["target_fifo"].params if p.name == "reset_actions")
    assert values.from_text(param, text) == expected


def test_reset_actions_round_trip_through_the_widget_text() -> None:
    param = next(p for p in DEVICES["target_fifo"].params if p.name == "reset_actions")
    assert values.to_text(param, ["MWL", "DATA"]) == "MWL | DATA"
    assert values.to_text(param, None) == ""


def test_an_unknown_reset_action_is_reported() -> None:
    param = next(p for p in DEVICES["target_fifo"].params if p.name == "reset_actions")
    with pytest.raises(values.ValueError_, match="NONSENSE"):
        values.from_text(param, "MWL | NONSENSE")


@pytest.mark.parametrize("kind", ["target_fifo", "target_8bit_memory"])
def test_ccc_response_values_are_emitted(kind: str) -> None:
    script = Script(name="probe")
    script.devices = [device(
        kind,
        getcaps_std_value=[0x05, 0x00],
        getstatus_usr_value=[[0x00, [0x12, 0x34]], [0x01, [0xAB]]],
        supported_group_address_count=2,
    )]
    source = generate(script)
    ast.parse(source)
    assert "getcaps_std_value=bytes([0x05, 0x00])" in source
    assert "getstatus_usr_value=[(0x00, bytes([0x12, 0x34])), (0x01, bytes([0xAB]))]" in source
    assert "supported_group_address_count=2" in source


def test_ccc_response_values_are_omitted_when_blank() -> None:
    script = Script(name="probe")
    script.devices = [device("target_fifo")]
    source = generate(script)
    for name in ("getcaps_std_value", "getstatus_usr_value", "getmxds_std_value"):
        assert name not in source


def test_fifo_only_state_is_refused_on_other_targets() -> None:
    script = _controller_script()
    script.devices.append(device("target_8bit_memory"))
    script.steps = [call("target_fifo_fill", "i3c_mem_target")]
    assert any("not available on" in p for p in validate(script))


def test_state_reads_are_emitted_as_attribute_reads() -> None:
    script = _controller_script()
    script.devices.append(device("target_fifo"))
    script.steps = [
        call("target_assigned_address", "i3c_fifo_target"),
        call("target_fifo_fill", "i3c_fifo_target"),
        call("target_fifo_capacity", "i3c_fifo_target"),
    ]
    source = generate(script)
    ast.parse(source)
    assert "assigned_address = i3c_fifo_target.assigned_address" in source
    assert "fifo_fill = i3c_fifo_target.tx_fill" in source
    assert "fifo_capacity = i3c_fifo_target.tx_capacity" in source


def test_repeated_state_reads_get_distinct_names() -> None:
    script = _controller_script()
    script.devices.append(device("target_fifo"))
    script.steps = [call("target_fifo_fill", "i3c_fifo_target") for _ in range(2)]
    source = generate(script)
    assert "fifo_fill = " in source and "fifo_fill_2 = " in source


@pytest.mark.parametrize(
    "op_key",
    [k for k, o in OPERATIONS.items() if o.category == "Target state"],
)
def test_state_properties_exist_on_the_real_class(op_key: str) -> None:
    import aqpxlib.i3c as i3c

    spec = OPERATIONS[op_key]
    for key, device_spec in DEVICES.items():
        if not (device_spec.role in spec.applies_to or key in spec.applies_to):
            continue
        cls = getattr(i3c, device_spec.cls)
        attr = inspect.getattr_static(cls, spec.method, None)
        assert isinstance(attr, property), (
            f"{device_spec.cls}.{spec.method} is not a property"
        )


# --------------------------------------------------------------------------
# Interleaved SDR read / write inside one frame
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["private_write_read", "private_read_addr"])
def test_interleaved_frames_hold_the_bus(key: str) -> None:
    """A write and a read in one frame, with exactly one STOP, at the end."""
    kinds = [k for k, _ in build_frame(key)]
    assert kinds.count("stop") == 1, "a STOP mid-frame would release the bus"
    assert kinds[-1] == "stop"
    assert "sdr_write" in kinds and "sdr_read" in kinds
    assert kinds.index("sdr_read") > kinds.index("sdr_write")


def test_interleaved_frame_generates_one_send() -> None:
    script = _controller_script()
    script.steps = [frame("private_write_read", CTRL)]
    source = generate(script)
    ast.parse(source)
    assert source.count("send_i3c_transactions(") == 1
    assert source.count("I3CSdrWrite(") == 2  # 0x7E header, then the data write
    assert source.count("I3CSdrRead(") == 1
    assert source.count("I3CStop(") == 1


def test_read_addr_writes_the_sub_address_before_reading() -> None:
    script = _controller_script()
    script.steps = [frame("private_read_addr", CTRL, address=0x08, sub_address=0x04)]
    source = generate(script)
    assert "data=bytes([0x04])" in source
    assert source.index("data=bytes([0x04])") < source.index("I3CSdrRead(")


def test_frame_body_strips_the_trailing_stop() -> None:
    """Merging a send into a frame must not drop a STOP into the middle."""
    for key in FRAMES:
        assert "stop" not in [k for k, _ in frame_body(key)][-1:], key
    assert [k for k, _ in frame_body("private_read")] == ["sdr_write", "sdr_read"]
    assert frame_body("hdr_exit") == []  # nothing but a stop to begin with


def test_arbitrary_interleaving_generates() -> None:
    """A hand-built frame alternating writes and reads must survive codegen."""
    order = ["sdr_write", "sdr_read", "sdr_write", "sdr_read", "stop"]
    script = _controller_script()
    script.steps = [Step(
        op=SEND_FRAME,
        target_var=CTRL,
        transactions=[
            Transaction(kind, {p.name: p.default for p in TRANSACTIONS[kind].params})
            for kind in order
        ],
    )]
    assert validate(script) == []
    source = generate(script)
    ast.parse(source)
    assert source.count("I3CControllerTransactionV2(") == len(order)


# --------------------------------------------------------------------------
# Composite blocks: a named send stacked as one block
# --------------------------------------------------------------------------


def _frame_of(*blocks: Transaction) -> Script:
    script = _controller_script()
    script.steps = [Step(op=SEND_FRAME, target_var=CTRL, transactions=list(blocks))]
    return script


def _composite(frame_key: str, **overrides) -> Transaction:
    params = {p.name: p.default for p in FRAMES[frame_key].params}
    params.update(overrides)
    return Transaction(composite_kind(frame_key), params)


def _primitive(kind: str, **overrides) -> Transaction:
    params = {p.name: p.default for p in TRANSACTIONS[kind].params}
    params.update(overrides)
    return Transaction(kind, params)


@pytest.mark.parametrize("key", sorted(FRAMES))
def test_every_named_send_works_as_a_block(key: str) -> None:
    script = _frame_of(_composite(key), _primitive("stop"))
    assert validate(script) == []
    ast.parse(generate(script))


def test_a_broadcast_ccc_payload_follows_the_command_in_one_write() -> None:
    """No repeated start between a broadcast CCC and its payload.

    A write of its own for the payload repeats the address phase, which is a
    repeated start back to 0x7E -- a second, payload-less CCC frame on the
    wire rather than the one that was asked for.
    """
    script = _frame_of(
        _composite("broadcast_ccc", ccc="I3C_CCC_BROADCAST_ENEC", data=[0x0B, 0x0C]),
        _primitive("stop"),
    )
    source = generate(script)
    ast.parse(source)

    assert source.count("I3CSdrWrite(") == 1
    assert source.count("address=0x7E") == 1
    assert ("data=bytes([PxI3CCommonCommandCode.I3C_CCC_BROADCAST_ENEC, 0x0B, 0x0C])"
            in source)


def test_a_broadcast_ccc_without_a_payload_is_the_command_alone() -> None:
    script = _frame_of(
        _composite("broadcast_ccc", ccc="I3C_CCC_BROADCAST_SETAASA", data=[]),
        _primitive("stop"),
    )
    source = generate(script)
    ast.parse(source)
    assert source.count("I3CSdrWrite(") == 1
    assert ("data=bytes([PxI3CCommonCommandCode.I3C_CCC_BROADCAST_SETAASA])"
            in source)


def test_a_direct_ccc_keeps_its_repeated_start() -> None:
    """The counterpart: a direct CCC does turn the bus around after the command.

    0x7E and the command go out in open drain, then a repeated start addresses
    the target in push-pull, so the two writes there are correct.
    """
    source = generate(_frame_of(
        _composite("direct_ccc_write", ccc="I3C_CCC_DIRECT_SETNEWDA", address=0x08,
                   data=[0x12]),
        _primitive("stop"),
    ))
    ast.parse(source)
    assert source.count("I3CSdrWrite(") == 2
    assert "address=0x7E" in source and "address=0x08" in source


def test_a_composite_emits_what_the_expanded_form_did() -> None:
    """Stacking a block must not change a single byte of the output."""
    for key in FRAMES:
        body = frame_body(key)
        if not body:
            continue
        composite = _frame_of(_composite(key), _primitive("stop"))
        expanded = _frame_of(
            *[Transaction(kind, params) for kind, params in body],
            _primitive("stop"),
        )
        assert generate(composite) == generate(expanded), key


def test_a_composite_contributes_no_stop_of_its_own() -> None:
    """The frame's own STOP is the only one; a mid-frame stop would release the bus."""
    script = _frame_of(
        _composite("direct_ccc"),
        _primitive("idle_time"),
        _composite("broadcast_ccc"),
        _composite("private_write"),
        _primitive("stop"),
    )
    source = generate(script)
    ast.parse(source)
    assert source.count("I3CStop(") == 1
    assert source.count("IdleTime(") == 1
    assert source.count("send_i3c_transactions(") == 1


def test_pure_stop_patterns_have_no_body_to_wrap() -> None:
    """HDR Exit and Target Reset are a stop; there is nothing to make a block of."""
    for key in ("hdr_exit", "target_reset_pattern", "empty"):
        assert frame_body(key) == [], key


def test_expanding_a_block_keeps_the_output_identical() -> None:
    block = _composite("private_read", address=0x0A, count=6)
    pieces = expand(block.kind, block.params)
    before = generate(_frame_of(block, _primitive("stop")))
    after = generate(_frame_of(
        *[Transaction(kind, params) for kind, params in pieces],
        _primitive("stop"),
    ))
    assert before == after


def test_composites_and_primitives_mix_in_one_frame() -> None:
    script = _frame_of(
        _composite("private_write"),
        _primitive("sdr_read", address=0x08, count=2),
        _primitive("stop"),
    )
    assert validate(script) == []
    source = generate(script)
    ast.parse(source)
    assert source.count("I3CSdrRead(") == 1


def test_block_info_covers_both_sorts() -> None:
    for key in FRAMES:
        info = block_info(composite_kind(key))
        assert info is not None and info.label == FRAMES[key].label
    for kind in TRANSACTIONS:
        info = block_info(kind)
        assert info is not None and info.category == "Primitives"
    assert block_info("send:nonsense") is None
    assert block_info("nonsense") is None


def test_an_unknown_block_is_reported() -> None:
    script = _frame_of(Transaction("send:nonsense", {}), _primitive("stop"))
    assert any("nonsense" in p for p in validate(script))


def test_a_composite_survives_a_project_round_trip(tmp_path: Path) -> None:
    original = _frame_of(
        _composite("direct_ccc", ccc="I3C_CCC_DIRECT_SETNEWDA"),
        _primitive("stop"),
    )
    path = tmp_path / "p.pxb.json"
    original.save(path)
    reloaded = Script.load(path)
    assert is_composite(reloaded.steps[0].transactions[0].kind)
    assert generate(reloaded) == generate(original)


# --------------------------------------------------------------------------
# Address header policy follows the STOPs in the frame
# --------------------------------------------------------------------------


def _policies(source: str) -> list[str]:
    """The address header policies emitted, in order."""
    import re

    return re.findall(r"address_header_policy=I3CAddressHeaderPolicy\.(\w+)", source)


def test_the_first_block_arbitrates_in_open_drain() -> None:
    script = _frame_of(_composite("direct_ccc"), _primitive("stop"))
    assert _policies(generate(script))[0] == "OPEN_DRAIN_FAST"


def test_a_repeated_start_stays_push_pull() -> None:
    """No STOP between two blocks means the bus was never released."""
    script = _frame_of(
        _composite("direct_ccc"),
        _composite("direct_ccc"),
        _primitive("stop"),
    )
    leading = _policies(generate(script))
    assert leading[0] == "OPEN_DRAIN_FAST"
    # The second block's header must not re-arbitrate.
    assert "OPEN_DRAIN_FAST" not in leading[1:]


def test_a_stop_between_blocks_returns_to_open_drain() -> None:
    script = _frame_of(
        _composite("direct_ccc"),
        _primitive("stop"),
        _composite("private_write"),
        _primitive("stop"),
    )
    found = _policies(generate(script))
    assert found.count("OPEN_DRAIN_FAST") == 2, found


def test_an_idle_time_does_not_reopen_the_segment() -> None:
    """A gap is not a STOP; the bus is still held."""
    script = _frame_of(
        _composite("direct_ccc"),
        _primitive("idle_time"),
        _composite("private_write"),
        _primitive("stop"),
    )
    assert _policies(generate(script)).count("OPEN_DRAIN_FAST") == 1


def test_a_disabled_stop_does_not_reopen_the_segment() -> None:
    stop = _primitive("stop")
    stop.enabled = False
    script = _frame_of(
        _composite("direct_ccc"), stop, _composite("private_write"), _primitive("stop"),
    )
    assert _policies(generate(script)).count("OPEN_DRAIN_FAST") == 1


def test_a_hand_placed_primitive_keeps_the_policy_it_was_given() -> None:
    """The field is shown on a primitive, so that choice is the user's."""
    script = _frame_of(
        _composite("direct_ccc"),
        _primitive("sdr_write", address_header_policy="OPEN_DRAIN_SLOW"),
        _primitive("stop"),
    )
    assert "OPEN_DRAIN_SLOW" in _policies(generate(script))


def test_the_pattern_matches_the_reference_script() -> None:
    """Two CCCs, a STOP, then another: ODF, PP, ODF -- as in test 3.py."""
    script = _frame_of(
        _composite("direct_ccc", ccc="I3C_CCC_DIRECT_GETBCR"),
        _composite("direct_ccc", ccc="I3C_CCC_DIRECT_GETBCR"),
        _primitive("stop"),
        _composite("private_write"),
        _primitive("stop"),
    )
    found = _policies(generate(script))
    assert found[0] == "OPEN_DRAIN_FAST"
    # The second CCC is a repeated start; only the block after the STOP
    # re-arbitrates.
    assert found.count("OPEN_DRAIN_FAST") == 2


# --------------------------------------------------------------------------
# init_bus covers dynamic addressing
# --------------------------------------------------------------------------


def test_no_flow_calls_do_daa_separately() -> None:
    """init_bus() brings the bus up and addresses it."""
    for key, flow in FLOWS.items():
        ops = [step.op for step in flow.as_script().steps]
        assert "do_daa" not in ops, f"{key} still has a separate DAA step"


def test_do_daa_is_still_available_for_an_explicit_entdaa() -> None:
    """Merging the outline rows must not remove a real API from the catalog."""
    assert "do_daa" in OPERATIONS


# --------------------------------------------------------------------------
# attach_to_bus failures are explained
# --------------------------------------------------------------------------


def test_a_controller_falls_back_to_the_one_on_the_bus() -> None:
    """Only one controller may be active, so an existing one is reused."""
    source = generate(FLOWS["bus_bringup"].as_script())
    assert "except PxAPIError:" in source
    assert "Could not attach a new controller" in source
    assert "i3c_controller = i3c_bus.get_current_controller()" in source


def test_a_target_names_the_failure_and_re_raises() -> None:
    """A bus takes many targets, so a refusal is a real problem, not a reuse."""
    source = generate(FLOWS["private_loopback"].as_script())
    ast.parse(source)
    attach = source[source.index("i3c_target.attach_to_bus"):]
    guard = attach[:attach.index("logger.info")]
    assert "except PxAPIError:" in guard
    assert "logger.error(" in guard
    assert "Could not attach %s" in guard
    assert "raise" in guard
    assert "get_current_controller" not in guard, "a target has nothing to reuse"


def test_the_target_failure_names_its_address() -> None:
    script = Script(name="probe")
    script.devices = [device("target_32bit_register", static_addr=0x4B)]
    source = generate(script)
    assert "at static address 0x4B" in source


@pytest.mark.parametrize("kind", [k for k, s in DEVICES.items() if s.role == "target"])
def test_every_target_is_guarded(kind: str) -> None:
    script = Script(name="probe")
    script.devices = [device(kind)]
    source = generate(script)
    ast.parse(source)
    assert source.count("except PxAPIError:") == 1
    assert "from aqpxlib.exceptions import PxAPIError" in source


def test_a_target_only_script_still_imports_the_error() -> None:
    """The import used to be tied to having a controller."""
    script = Script(name="probe")
    script.devices = [device("target_fifo")]
    assert "from aqpxlib.exceptions import PxAPIError" in generate(script)


def test_both_roles_are_guarded_in_one_script() -> None:
    source = generate(FLOWS["private_loopback"].as_script())
    assert source.count("except PxAPIError:") == 2


# --------------------------------------------------------------------------
# The frame log line, and the optional teardown
# --------------------------------------------------------------------------


def test_a_frame_logs_only_that_it_ran() -> None:
    """The comment above the frame already says what it is."""
    source = generate(FLOWS["private_loopback"].as_script())
    ast.parse(source)
    assert 'logger.info("Transaction Finished")' in source
    assert "format_response" not in source, "the helper has nothing left to do"
    assert "# Private write DE AD BE EF to 0x08" in source, "the comment stays"


def test_the_response_is_still_bound() -> None:
    """It is the return value, and what you would check what came back with."""
    source = generate(FLOWS["private_loopback"].as_script())
    assert "response = i3c_controller.send_i3c_transactions(txs)" in source


def test_logging_off_drops_the_frame_line_too() -> None:
    script = FLOWS["private_loopback"].as_script()
    script.log_results = False
    source = generate(script)
    ast.parse(source)
    assert "Transaction Finished" not in source


def test_the_teardown_is_optional() -> None:
    script = FLOWS["private_loopback"].as_script()
    assert script.detach_on_finish is True
    with_teardown = generate(script)
    assert "detach_from_bus()" in with_teardown
    assert "Leave the bus clean" in with_teardown

    script.detach_on_finish = False
    without = generate(script)
    ast.parse(without)
    assert "detach_from_bus()" not in without
    assert "Leave the bus clean" not in without


def test_the_teardown_choice_survives_a_round_trip(tmp_path: Path) -> None:
    script = FLOWS["private_loopback"].as_script()
    script.detach_on_finish = False
    path = tmp_path / "p.pxb.json"
    script.save(path)
    assert Script.load(path).detach_on_finish is False
    assert "detach_from_bus()" not in generate(Script.load(path))


def test_an_older_project_still_detaches() -> None:
    """The flag postdates the format, so its absence means the old behaviour."""
    script = FLOWS["private_loopback"].as_script()
    raw = json.loads(script.to_json())
    del raw["detach_on_finish"]
    assert "detach_from_bus()" in generate(Script.from_json(json.dumps(raw)))


# --------------------------------------------------------------------------
# An Idle Time belongs between transfers
# --------------------------------------------------------------------------


def test_a_frame_cannot_start_with_an_idle_time() -> None:
    script = _frame_of(_primitive("idle_time"), _composite("private_write"),
                       _primitive("stop"))
    assert any("cannot start with an Idle Time" in p for p in validate(script))


def test_a_frame_cannot_end_with_an_idle_time() -> None:
    script = _frame_of(_composite("private_write"), _primitive("stop"),
                       _primitive("idle_time"))
    assert any("cannot end with an Idle Time" in p for p in validate(script))


def test_an_idle_time_between_transfers_is_fine() -> None:
    script = _frame_of(
        _composite("private_write"),
        _primitive("idle_time"),
        _composite("private_read"),
        _primitive("stop"),
    )
    assert validate(script) == []
    assert "IdleTime(" in generate(script)


def test_a_disabled_idle_time_at_the_end_is_not_a_problem() -> None:
    """It is not emitted, so it cannot be misplaced."""
    idle = _primitive("idle_time")
    idle.enabled = False
    script = _frame_of(_composite("private_write"), _primitive("stop"), idle)
    assert validate(script) == []


def test_a_frame_of_nothing_but_an_idle_time_is_rejected() -> None:
    script = _frame_of(_primitive("idle_time"))
    assert any("Idle Time" in p for p in validate(script))


def test_two_blocks_do_not_share_a_default_list() -> None:
    """A mutable default is one object; handing it out directly would let two
    blocks edit the same list without either knowing."""
    from pxbuilder.catalog import default_params

    first = default_params(FRAMES["direct_ccc"].params)
    second = default_params(FRAMES["direct_ccc"].params)
    assert first["targets"] is not second["targets"]
    first["targets"].append({"address": 0x22, "direction": "read", "count": 1,
                             "data": []})
    assert len(second["targets"]) == 1
    # ...and the catalog itself is untouched.
    assert len(FRAMES["direct_ccc"].params[-1].default) == 1


def test_no_catalog_default_is_handed_out_by_reference() -> None:
    from pxbuilder.catalog import DEVICES as D
    from pxbuilder.catalog import OPERATIONS as O
    from pxbuilder.catalog import default_params

    for group in (FRAMES.values(), TRANSACTIONS.values(), D.values(), O.values()):
        for spec in group:
            values = default_params(spec.params)
            for param in spec.params:
                if isinstance(param.default, (list, dict)):
                    assert values[param.name] is not param.default, (
                        f"{spec.key}.{param.name} is shared"
                    )


# -- aqpxlib 0.8 -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["target_fifo", "target_8bit_memory",
                                  "target_32bit_register", "target_mctp_endpoint"])
def test_ibi_payload_size_is_emitted_when_set(kind: str) -> None:
    script = Script(name="probe")
    script.devices = [device(kind, bcr=0x06, ibi_payload_size=8)]
    source = generate(script)
    ast.parse(source)
    assert "ibi_payload_size=8" in source


def test_ibi_payload_size_is_omitted_when_blank() -> None:
    """Blank must not send a 0 -- unset and 0 mean the same to the device
    today, but an absent field is what the library sends by default."""
    script = Script(name="probe")
    script.devices = [device("target_fifo")]
    assert "ibi_payload_size" not in generate(script)


@pytest.mark.parametrize("kind", ["target_fifo", "target_8bit_memory",
                                  "target_32bit_register"])
def test_ibi_payload_size_reaches_the_real_config(kind: str) -> None:
    import aqpxlib.i3c as i3c

    config_cls = getattr(i3c, DEVICES[kind].cls + "Config")
    config = config_cls(ibi_payload_size=8)
    assert config.ibi_payload_size == 8
    config.to_protobuf()  # the field has to survive onto the wire message


def test_ibi_payload_size_state_read_generates() -> None:
    script = _controller_script()
    script.devices.append(device("target_8bit_memory"))
    script.steps = [call("target_ibi_payload_size", "i3c_mem_target")]
    source = generate(script)
    ast.parse(source)
    assert "ibi_payload_size = i3c_mem_target.ibi_payload_size" in source


def test_hdr_ddr_read_parity_is_left_out_by_default() -> None:
    """RAW is the default, so a frame saved before 0.8 must generate exactly
    what it did then."""
    script = _frame_of(_primitive("hdr_ddr_read"), _primitive("stop"))
    source = generate(script)
    assert "parity_mode" not in source
    assert "command_parity" not in source


def test_hdr_ddr_read_parity_is_emitted_when_set() -> None:
    script = _frame_of(
        _primitive("hdr_ddr_read", parity_mode="ALL_INCORRECT"),
        _primitive("hdr_ddr_write", command_parity=[0x01]),
        _primitive("stop"),
    )
    assert validate(script) == []
    source = generate(script)
    ast.parse(source)
    assert "parity_mode=I3CParityMode.ALL_INCORRECT" in source
    assert "command_parity=bytes([0x01])" in source


def test_hdr_ddr_read_parity_default_is_the_proto_zero_value() -> None:
    from aqpxlib.message import I3CDdrRead, I3CParityMode

    param = next(p for p in TRANSACTIONS["hdr_ddr_read"].params
                 if p.name == "parity_mode")
    assert I3CDdrRead().parity_mode == getattr(I3CParityMode, param.default)


def test_direct_rstact_reads_are_not_flagged() -> None:
    """RSTACT is defined as both a write and a read (aqpxlib 0.8 has both)."""
    from pxbuilder.transactions import direction_note

    assert hasattr(aqpxlib.i3c.PxI3CBaseController, "direct_rstact_read")
    assert direction_note("I3C_CCC_DIRECT_RSTACT", "read") == ""
    assert direction_note("I3C_CCC_DIRECT_RSTACT", "write") == ""
    assert direction_note("I3C_CCC_DIRECT_SETMWL", "read")
