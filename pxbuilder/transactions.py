"""The ``I3CControllerTransactionV2`` model: primitives and frame builders.

A controller send is a *frame* -- an ordered list of transactions handed to
``send_i3c_transactions()``. Each transaction carries exactly one payload:

===============  ==========================================================
``sdr_write``    ``I3CSdrWrite``   -- address + data, SDR
``sdr_read``     ``I3CSdrRead``    -- address + byte count, SDR
``hdr_ddr_write`` ``I3CDdrWrite``  -- command + address + data, HDR-DDR
``hdr_ddr_read`` ``I3CDdrRead``    -- command + address + byte count
``stop``         ``I3CStop``       -- an explicit STOP, incl. HDR Exit
``idle_time``    ``IdleTime``      -- a gap, in nanoseconds
===============  ==========================================================

:data:`FRAMES` holds the named send functions -- Broadcast CCC, Direct CCC,
Private Wr/Rd, HDR-DDR, HDR Exit, Target Reset Pattern. Each expands into the
primitives above, so the GUI can drop in a correct sequence that the user is
then free to edit transaction by transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .catalog import Param, default_params

#: Members of ``PxI3CCommonCommandCode``, offered as a dropdown. Hard-coded so
#: the builder does not need aqpxlib installed; `tests` check it stays in sync.
CCC_CODES: tuple[str, ...] = (
    "I3C_CCC_BROADCAST_ENEC",
    "I3C_CCC_BROADCAST_DISEC",
    "I3C_CCC_BROADCAST_ENTAS0",
    "I3C_CCC_BROADCAST_ENTAS1",
    "I3C_CCC_BROADCAST_ENTAS2",
    "I3C_CCC_BROADCAST_ENTAS3",
    "I3C_CCC_BROADCAST_RSTDAA",
    "I3C_CCC_BROADCAST_ENTDAA",
    "I3C_CCC_BROADCAST_DEFTGTS",
    "I3C_CCC_BROADCAST_SETMWL",
    "I3C_CCC_BROADCAST_SETMRL",
    "I3C_CCC_BROADCAST_ENTTM",
    "I3C_CCC_BROADCAST_SETBUSCON",
    "I3C_CCC_BROADCAST_ENDXFER",
    "I3C_CCC_BROADCAST_ENTHDR0",
    "I3C_CCC_BROADCAST_ENTHDR1",
    "I3C_CCC_BROADCAST_ENTHDR2",
    "I3C_CCC_BROADCAST_ENTHDR3",
    "I3C_CCC_BROADCAST_ENTHDR4",
    "I3C_CCC_BROADCAST_ENTHDR5",
    "I3C_CCC_BROADCAST_ENTHDR6",
    "I3C_CCC_BROADCAST_ENTHDR7",
    "I3C_CCC_BROADCAST_SETXTIME",
    "I3C_CCC_BROADCAST_SETAASA",
    "I3C_CCC_BROADCAST_RSTACT",
    "I3C_CCC_BROADCAST_DEFGRPA",
    "I3C_CCC_BROADCAST_RSTGRPA",
    "I3C_CCC_BROADCAST_MLANE",
    "I3C_CCC_BROADCAST_DBGACTION",
    "I3C_CCC_BROADCAST_EXTENSION",
    "I3C_CCC_DIRECT_ENEC",
    "I3C_CCC_DIRECT_DISEC",
    "I3C_CCC_DIRECT_ENTAS0",
    "I3C_CCC_DIRECT_ENTAS1",
    "I3C_CCC_DIRECT_ENTAS2",
    "I3C_CCC_DIRECT_ENTAS3",
    "I3C_CCC_DIRECT_RSTDAA",
    "I3C_CCC_DIRECT_SETDASA",
    "I3C_CCC_DIRECT_SETNEWDA",
    "I3C_CCC_DIRECT_SETMWL",
    "I3C_CCC_DIRECT_SETMRL",
    "I3C_CCC_DIRECT_GETMWL",
    "I3C_CCC_DIRECT_GETMRL",
    "I3C_CCC_DIRECT_GETPID",
    "I3C_CCC_DIRECT_GETBCR",
    "I3C_CCC_DIRECT_GETDCR",
    "I3C_CCC_DIRECT_GETSTATUS",
    "I3C_CCC_DIRECT_GETACCCR",
    "I3C_CCC_DIRECT_ENDXFER",
    "I3C_CCC_DIRECT_SETBRGTGT",
    "I3C_CCC_DIRECT_GETMXDS",
    "I3C_CCC_DIRECT_GETCAPS",
    "I3C_CCC_DIRECT_SETROUTE",
    "I3C_CCC_DIRECT_D2DXFER",
    "I3C_CCC_DIRECT_SETXTIME",
    "I3C_CCC_DIRECT_GETXTIME",
    "I3C_CCC_DIRECT_RSTACT",
    "I3C_CCC_DIRECT_SETGRPA",
    "I3C_CCC_DIRECT_RSTGRPA",
    "I3C_CCC_DIRECT_MLANE",
    "I3C_CCC_DIRECT_DBGOPCODE",
    "I3C_CCC_DIRECT_DBGACTION",
    "I3C_CCC_DIRECT_EXTENSION",
)

BROADCAST_ADDRESS = 0x7E

#: Reserved key a builder can put in a transaction's params to have the
#: generator write a comment above it. Not a protobuf field.
NOTE_KEY = "_note"

ADDRESS_HEADER_POLICY = ("OPTIMIZED", "OPEN_DRAIN_SLOW", "OPEN_DRAIN_FAST", "PUSH_PULL")
PARITY_MODES = ("RAW", "FLAG", "ALL_CORRECT", "ALL_INCORRECT")
STOP_TYPES = ("PUSH_PULL", "OPEN_DRAIN", "HDR_EXIT", "TARGET_RESET")


def _policy(default: str, help_text: str = "") -> Param:
    return Param(
        "address_header_policy",
        "Address header policy",
        "enum",
        default=default,
        choices=ADDRESS_HEADER_POLICY,
        enum_prefix="I3CAddressHeaderPolicy",
        always_emit=True,
        help=help_text or "How the address phase is driven.",
    )


def _parity_mode() -> Param:
    return Param(
        "parity_mode",
        "Parity mode",
        "enum",
        default="ALL_CORRECT",
        choices=PARITY_MODES,
        enum_prefix="I3CParityMode",
        always_emit=True,
        help="FLAG lets you supply an explicit parity; ALL_INCORRECT injects errors.",
    )


@dataclass
class TransactionSpec:
    """One payload an ``I3CControllerTransactionV2`` can carry."""

    key: str
    label: str
    #: The field name on I3CControllerTransactionV2.
    field_name: str
    #: The protobuf message constructed for that field.
    message: str
    params: list[Param] = field(default_factory=list)
    help: str = ""


TRANSACTIONS: dict[str, TransactionSpec] = {
    "sdr_write": TransactionSpec(
        key="sdr_write",
        label="SDR write",
        field_name="sdr_write",
        message="I3CSdrWrite",
        help="Address phase then data, in SDR. A CCC is an SDR write to 0x7E.",
        params=[
            Param("address", "Address", "hex", default=BROADCAST_ADDRESS, always_emit=True,
                  help="0x7E is the broadcast address."),
            Param("data", "Data", "bytes_literal", default=[], always_emit=True,
                  help="Empty is legal -- an address-only write."),
            _policy("OPEN_DRAIN_FAST"),
            _parity_mode(),
            Param("parity", "Parity override", "bytes_literal", default=None,
                  help="Only meaningful with parity mode FLAG or RAW."),
            Param("ignore_nack", "Ignore NACK", "bool", default=True, always_emit=True,
                  help="Carry on with the frame even if the target does not ACK."),
        ],
    ),
    "sdr_read": TransactionSpec(
        key="sdr_read",
        label="SDR read",
        field_name="sdr_read",
        message="I3CSdrRead",
        help="Address phase then a read of `count` bytes, in SDR.",
        params=[
            Param("address", "Address", "hex", default=0x08, always_emit=True),
            Param("count", "Byte count", "int", default=1, always_emit=True),
            _policy("PUSH_PULL"),
            Param("ignore_nack", "Ignore NACK", "bool", default=True, always_emit=True),
        ],
    ),
    "hdr_ddr_write": TransactionSpec(
        key="hdr_ddr_write",
        label="HDR-DDR write",
        field_name="hdr_ddr_write",
        message="I3CDdrWrite",
        help="HDR double data rate write. Data length must be even.",
        params=[
            Param("command", "Command code", "hex", default=0x08, always_emit=True),
            Param("address", "Address", "hex", default=0x08, always_emit=True),
            Param("data", "Data", "bytes_literal", default=[0x12, 0x34], always_emit=True),
            _parity_mode(),
            Param("parity", "Parity override", "bytes_literal", default=None),
            Param("command_parity", "Command parity override", "bytes_literal", default=None,
                  help="Parity of the command word. Only meaningful with parity "
                       "mode FLAG or RAW."),
            Param("crc5", "CRC5 override", "int", default=None,
                  help="Leave blank for a correct CRC; set a value to inject an error."),
            Param("force", "Force", "bool", default=None,
                  help="Send even when the target state says not to."),
            Param("ignore_nack", "Ignore NACK", "bool", default=True, always_emit=True),
        ],
    ),
    "hdr_ddr_read": TransactionSpec(
        key="hdr_ddr_read",
        label="HDR-DDR read",
        field_name="hdr_ddr_read",
        message="I3CDdrRead",
        params=[
            Param("command", "Command code", "hex", default=0x08, always_emit=True),
            Param("address", "Address", "hex", default=0x08, always_emit=True),
            Param("count", "Byte count", "int", default=16, always_emit=True),
            # New in aqpxlib 0.8. RAW is the default, so it stays out of
            # the output and frames saved before 0.8 generate the same code.
            Param("parity_mode", "Parity mode", "enum", default="RAW",
                  choices=PARITY_MODES, enum_prefix="I3CParityMode",
                  help="Parity of the address and command words. "
                       "ALL_INCORRECT injects errors."),
            Param("command_parity", "Command parity override", "bytes_literal", default=None,
                  help="Only meaningful with parity mode FLAG or RAW."),
            Param("ignore_nack", "Ignore NACK", "bool", default=True, always_emit=True),
        ],
    ),
    "stop": TransactionSpec(
        key="stop",
        label="Stop",
        field_name="stop",
        message="I3CStop",
        help=(
            "An explicit STOP. HDR_EXIT drives the HDR Exit pattern and "
            "TARGET_RESET drives the Target Reset Pattern."
        ),
        params=[
            Param("type", "Stop type", "enum", default="PUSH_PULL", choices=STOP_TYPES,
                  enum_prefix="I3CStopType", always_emit=True),
        ],
    ),
    "idle_time": TransactionSpec(
        key="idle_time",
        label="Idle time",
        field_name="idle_time",
        message="IdleTime",
        help="Hold the bus idle before the next transaction.",
        params=[
            Param("idle_time_ns", "Idle time (ns)", "int", default=1000, always_emit=True),
        ],
    ),
}


# --------------------------------------------------------------------------
# Named send functions -- these expand into the primitives above
# --------------------------------------------------------------------------


@dataclass
class FrameSpec:
    """A named controller send, expressed as a sequence of transactions."""

    key: str
    label: str
    category: str
    #: Parameters of the *frame*, which its builder turns into transactions.
    params: list[Param] = field(default_factory=list)
    #: Given the frame parameters, produce ``[(transaction_key, params), ...]``.
    build: Callable[[dict[str, Any]], list[tuple[str, dict[str, Any]]]] = None  # type: ignore[assignment]
    help: str = ""
    #: Narrows `params` to the fields that matter for the values held, so a
    #: block shows a byte count for a GET and a payload for a SET.
    relevant: Callable[[dict[str, Any], list[Param]], list[Param]] | None = None
    #: Kept for projects that still reference it, but not offered in the palette.
    hidden: bool = False


#: CCCs that a controller method carries out, mapped to the operation that
#: does it.
#:
#: They are not offered inside a transaction, because there the command byte
#: alone would do nothing. They are offered on the Transactions list in the
#: outline instead, as "Add ENTDAA" and "Add RSTDAA", which add that step.
#:
#: ENTDAA cannot work as a transaction at all -- assignment is a per-target
#: handshake, where the controller reads each target's PID, BCR and DCR and
#: hands back an address, so writing the command byte and stopping there
#: addresses nobody. A broadcast RSTDAA does reach the wire, but it leaves the
#: controller's own assigned address table stale, because that table is
#: maintained by do_rstdaa().
#:
#: The *directed* RSTDAA is deliberately absent: it resets one target, aqpxlib
#: has no directed method for it, so a frame is both the only way and a correct
#: one.
CCC_VIA_OPERATION: dict[str, str] = {
    "I3C_CCC_BROADCAST_ENTDAA": "do_daa",
    "I3C_CCC_BROADCAST_RSTDAA": "do_rstdaa",
}


def ccc_via_operation(params: "dict[str, Any] | None") -> str | None:
    """The first CCC in `params` that is carried out by a method.

    Looks at the values rather than one named field, because a CCC survives
    into the expanded form as a name inside a data list -- expanding a
    Broadcast CCC block leaves ``data=["I3C_CCC_..."]`` behind. A whole block
    can be routed to the method; hand-built bytes cannot, so the caller uses
    this to tell the two apart.
    """
    def walk(value: Any) -> str | None:
        if isinstance(value, str):
            return value if value in CCC_VIA_OPERATION else None
        if isinstance(value, (list, tuple)):
            return next((found for found in map(walk, value) if found), None)
        return None

    return walk(list(params.values())) if params else None


def _ccc_param(default: str, *, direct: bool) -> Param:
    kind = "DIRECT" if direct else "BROADCAST"
    return Param(
        "ccc", "CCC", "ccc", default=default,
        choices=tuple(c for c in CCC_CODES
                      if f"_{kind}_" in c and c not in CCC_VIA_OPERATION),
        always_emit=True,
        help=f"The {kind.lower()} Common Command Code to send.",
    )


def _defaults(keys: dict[str, Any], spec_key: str) -> dict[str, Any]:
    """Fill a transaction's parameter defaults, then apply overrides."""
    spec = TRANSACTIONS[spec_key]
    params = default_params(spec.params)
    params.update(keys)
    return params


def _build_broadcast_ccc(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """0x7E + CCC + payload in one write, then STOP.

    The payload of a broadcast CCC follows the command byte in the same write.
    Giving it a write of its own would repeat the address phase, which is a
    repeated start back to 0x7E -- a second, payload-less CCC frame on the
    wire, not the one that was asked for.
    """
    return [
        ("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS,
             "data": [p["ccc"], *list(p.get("data") or [])],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")),
        ("stop", _defaults({"type": "PUSH_PULL"}, "stop")),
    ]


def _build_direct_ccc_write(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """0x7E + CCC in open drain, then a write to the target, then STOP."""
    return [
        ("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": [p["ccc"]],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")),
        ("sdr_write", _defaults(
            {"address": p["address"], "data": list(p.get("data") or []),
             "address_header_policy": "PUSH_PULL"}, "sdr_write")),
        ("stop", _defaults({"type": "PUSH_PULL"}, "stop")),
    ]


def _build_direct_ccc_read(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """0x7E + CCC in open drain, then a read from the target, then STOP."""
    return [
        ("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": [p["ccc"]],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")),
        ("sdr_read", _defaults(
            {"address": p["address"], "count": p["count"],
             "address_header_policy": "PUSH_PULL"}, "sdr_read")),
        ("stop", _defaults({"type": "PUSH_PULL"}, "stop")),
    ]


#: Direct CCCs the spec defines in both directions, so neither is flagged.
#: Direct RSTACT writes the reset action, and reads back the action or the
#: reset recovery time (aqpxlib 0.8 has direct_rstact and direct_rstact_read).
BIDIRECTIONAL_CCCS = ("I3C_CCC_DIRECT_RSTACT",)


def is_read_ccc(ccc: str) -> bool:
    """Whether a CCC reads from the target rather than writing to it."""
    return "_GET" in ccc


def default_direct_target(ccc: str = "") -> dict[str, Any]:
    """One repeated-start entry: an address and what to do at it."""
    return {
        "address": 0x08,
        "direction": "read" if is_read_ccc(ccc) else "write",
        "count": 1,
        "data": [0x12],
    }


def direction_note(ccc: str, direction: str) -> str:
    """A warning when an entry contradicts the command, or "" when it agrees.

    The direction is editable on purpose -- driving a read where the spec says
    write is a thing an exerciser is for -- so a mismatch is annotated rather
    than refused.
    """
    if not ccc or ccc in BIDIRECTIONAL_CCCS:
        return ""
    expected = "read" if is_read_ccc(ccc) else "write"
    if direction == expected:
        return ""
    return (
        f"NOT TO SPEC: {ccc} is a {expected} command, "
        f"but this target is set to {direction}."
    )


def _build_direct_ccc(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """0x7E carrying the CCC and any bytes after it, then one entry per target.

    Each entry is a repeated start to an address, reading or writing. The
    direction normally follows the command; where it does not, the entry is
    tagged so the generated code says so.
    """
    ccc = str(p.get("ccc", ""))
    header = [ccc, *(p.get("defining_bytes") or [])]
    frame: list[tuple[str, dict[str, Any]]] = [
        ("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": header,
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")),
    ]
    for entry in p.get("targets") or []:
        note = direction_note(ccc, entry.get("direction", "write"))
        if entry.get("direction") == "read":
            params = _defaults(
                {"address": entry.get("address", 0x08),
                 "count": entry.get("count", 1),
                 "address_header_policy": "PUSH_PULL"}, "sdr_read")
            kind = "sdr_read"
        else:
            params = _defaults(
                {"address": entry.get("address", 0x08),
                 "data": list(entry.get("data") or []),
                 "address_header_policy": "PUSH_PULL"}, "sdr_write")
            kind = "sdr_write"
        if note:
            params[NOTE_KEY] = note
        frame.append((kind, params))
    frame.append(("stop", _defaults({"type": "PUSH_PULL"}, "stop")))
    return frame


def _build_private_write(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Optional 0x7E header, then the data write, then STOP."""
    frame: list[tuple[str, dict[str, Any]]] = []
    if p.get("with_broadcast_hdr", True):
        frame.append(("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": [],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")))
    frame.append(("sdr_write", _defaults(
        {"address": p["address"], "data": list(p["data"]),
         "address_header_policy": "PUSH_PULL"}, "sdr_write")))
    frame.append(("stop", _defaults({"type": "PUSH_PULL"}, "stop")))
    return frame


def _build_private_read(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Optional 0x7E header, then the data read, then STOP."""
    frame: list[tuple[str, dict[str, Any]]] = []
    if p.get("with_broadcast_hdr", True):
        frame.append(("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": [],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")))
    frame.append(("sdr_read", _defaults(
        {"address": p["address"], "count": p["count"],
         "address_header_policy": "PUSH_PULL"}, "sdr_read")))
    frame.append(("stop", _defaults({"type": "PUSH_PULL"}, "stop")))
    return frame


def _build_hdr_ddr_write(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("hdr_ddr_write", _defaults(
            {"command": p["command"], "address": p["address"],
             "data": list(p["data"])}, "hdr_ddr_write")),
        ("stop", _defaults({"type": "HDR_EXIT"}, "stop")),
    ]


def _build_hdr_ddr_read(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("hdr_ddr_read", _defaults(
            {"command": p["command"], "address": p["address"],
             "count": p["count"]}, "hdr_ddr_read")),
        ("stop", _defaults({"type": "HDR_EXIT"}, "stop")),
    ]


def _build_hdr_exit(_p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("stop", _defaults({"type": "HDR_EXIT"}, "stop"))]


def _build_target_reset(_p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("stop", _defaults({"type": "TARGET_RESET"}, "stop"))]


def _build_private_write_read(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Write, then read back in the same frame -- a repeated start, no STOP between."""
    frame: list[tuple[str, dict[str, Any]]] = []
    if p.get("with_broadcast_hdr", True):
        frame.append(("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": [],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")))
    frame.append(("sdr_write", _defaults(
        {"address": p["address"], "data": list(p["data"]),
         "address_header_policy": "PUSH_PULL"}, "sdr_write")))
    frame.append(("sdr_read", _defaults(
        {"address": p["address"], "count": p["count"],
         "address_header_policy": "PUSH_PULL"}, "sdr_read")))
    frame.append(("stop", _defaults({"type": "PUSH_PULL"}, "stop")))
    return frame


def _build_private_read_addr(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Write a sub-address, then read from it without releasing the bus."""
    frame: list[tuple[str, dict[str, Any]]] = []
    if p.get("with_broadcast_hdr", True):
        frame.append(("sdr_write", _defaults(
            {"address": BROADCAST_ADDRESS, "data": [],
             "address_header_policy": "OPEN_DRAIN_FAST"}, "sdr_write")))
    frame.append(("sdr_write", _defaults(
        {"address": p["address"], "data": [p["sub_address"]],
         "address_header_policy": "PUSH_PULL"}, "sdr_write")))
    frame.append(("sdr_read", _defaults(
        {"address": p["address"], "count": p["count"],
         "address_header_policy": "PUSH_PULL"}, "sdr_read")))
    frame.append(("stop", _defaults({"type": "PUSH_PULL"}, "stop")))
    return frame


def _build_empty(_p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("stop", _defaults({"type": "PUSH_PULL"}, "stop"))]


FRAMES: dict[str, FrameSpec] = {
    "broadcast_ccc": FrameSpec(
        key="broadcast_ccc",
        label="Broadcast CCC",
        category="Broadcast CCC",
        help="0x7E carrying the CCC in open drain, then the payload in push-pull.",
        params=[
            _ccc_param("I3C_CCC_BROADCAST_ENEC", direct=False),
            Param("data", "Payload", "bytes", default=[0x0B],
                  help="Blank for a CCC that takes no data."),
        ],
        build=_build_broadcast_ccc,
    ),
    "direct_ccc": FrameSpec(
        key="direct_ccc",
        label="Direct CCC",
        category="Direct CCC",
        help=(
            "0x7E carries the CCC and any bytes after it, then a repeated start "
            "to each target. Each entry reads or writes; the direction follows "
            "the command unless you change it, and a mismatch is noted in the "
            "generated code rather than refused."
        ),
        params=[
            _ccc_param("I3C_CCC_DIRECT_GETBCR", direct=True),
            Param("defining_bytes", "Data after the CCC", "bytes", default=[],
                  always_emit=True,
                  help="Sent on 0x7E straight after the command byte."),
            Param("targets", "Targets", "ccc_targets",
                  default=[default_direct_target("I3C_CCC_DIRECT_GETBCR")],
                  always_emit=True,
                  help="One repeated start each. Use + to add another."),
        ],
        build=_build_direct_ccc,
    ),
    # Superseded by "direct_ccc", kept so saved projects still open.
    "direct_ccc_write": FrameSpec(
        key="direct_ccc_write",
        label="Direct CCC write",
        category="Direct CCC",
        hidden=True,
        params=[
            _ccc_param("I3C_CCC_DIRECT_SETNEWDA", direct=True),
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("data", "Payload", "bytes", default=[0x12]),
        ],
        build=_build_direct_ccc_write,
    ),
    "direct_ccc_read": FrameSpec(
        key="direct_ccc_read",
        label="Direct CCC read",
        category="Direct CCC",
        hidden=True,
        params=[
            _ccc_param("I3C_CCC_DIRECT_GETBCR", direct=True),
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            # Hex, like every other number in a CCC frame -- the frames mix
            # addresses, command codes and payloads, and one base throughout
            # is what keeps "10" from meaning two things in one form.
            Param("count", "Byte count", "hex", default=1, always_emit=True),
        ],
        build=_build_direct_ccc_read,
    ),
    "private_write": FrameSpec(
        key="private_write",
        label="Private write",
        category="Private Wr/Rd",
        params=[
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("data", "Data", "bytes", default=[0xDE, 0xAD, 0xBE, 0xEF], always_emit=True),
            Param("with_broadcast_hdr", "Send 0x7E header first", "bool", default=True),
        ],
        build=_build_private_write,
    ),
    "private_read": FrameSpec(
        key="private_read",
        label="Private read",
        category="Private Wr/Rd",
        params=[
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("count", "Byte count", "int", default=4, always_emit=True),
            Param("with_broadcast_hdr", "Send 0x7E header first", "bool", default=True),
        ],
        build=_build_private_read,
    ),
    "private_write_read": FrameSpec(
        key="private_write_read",
        label="Private write then read (repeated start)",
        category="Private Wr/Rd",
        help=(
            "Write and read interleaved in one frame. There is no STOP between "
            "them, so the bus is held across the turnaround."
        ),
        params=[
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("data", "Data to write", "bytes", default=[0xDE, 0xAD], always_emit=True),
            Param("count", "Bytes to read", "int", default=4, always_emit=True),
            Param("with_broadcast_hdr", "Send 0x7E header first", "bool", default=True),
        ],
        build=_build_private_write_read,
    ),
    "private_read_addr": FrameSpec(
        key="private_read_addr",
        label="Private read from sub-address",
        category="Private Wr/Rd",
        help=(
            "Write a sub-address then read from it in the same frame -- the "
            "usual way to address a register bank."
        ),
        params=[
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("sub_address", "Sub-address", "hex", default=0x00, always_emit=True),
            Param("count", "Bytes to read", "int", default=4, always_emit=True),
            Param("with_broadcast_hdr", "Send 0x7E header first", "bool", default=True),
        ],
        build=_build_private_read_addr,
    ),
    "hdr_ddr_write": FrameSpec(
        key="hdr_ddr_write",
        label="HDR-DDR write",
        category="HDR-DDR",
        params=[
            Param("command", "Command code", "hex", default=0x08, always_emit=True),
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("data", "Data", "bytes", default=[0x12, 0x34, 0x12, 0x34], always_emit=True),
        ],
        build=_build_hdr_ddr_write,
    ),
    "hdr_ddr_read": FrameSpec(
        key="hdr_ddr_read",
        label="HDR-DDR read",
        category="HDR-DDR",
        params=[
            Param("command", "Command code", "hex", default=0x08, always_emit=True),
            Param("address", "Target address", "hex", default=0x08, always_emit=True),
            Param("count", "Byte count", "int", default=16, always_emit=True),
        ],
        build=_build_hdr_ddr_read,
    ),
    "hdr_exit": FrameSpec(
        key="hdr_exit",
        label="HDR Exit",
        category="Patterns",
        help="A frame carrying nothing but an HDR Exit stop.",
        build=_build_hdr_exit,
    ),
    "target_reset_pattern": FrameSpec(
        key="target_reset_pattern",
        label="Target Reset Pattern",
        category="Patterns",
        help="A frame carrying nothing but a Target Reset stop.",
        build=_build_target_reset,
    ),
    "empty": FrameSpec(
        key="empty",
        label="Empty frame (build by hand)",
        category="Patterns",
        help="Just a STOP -- add the transactions you want yourself.",
        build=_build_empty,
    ),
}


def frame_categories() -> list[str]:
    """Frame categories, in menu order."""
    order = ["Broadcast CCC", "Direct CCC", "Private Wr/Rd", "HDR-DDR", "Patterns"]
    present = {f.category for f in FRAMES.values()}
    seen = [c for c in order if c in present]
    return seen + sorted(present - set(seen))


def frames_in(category: str) -> list[FrameSpec]:
    """Frames belonging to `category` that the palette should offer."""
    return [f for f in FRAMES.values() if f.category == category and not f.hidden]


def build_frame(key: str, params: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Expand a named send into ``[(transaction_key, params), ...]``."""
    spec = FRAMES[key]
    values = default_params(spec.params)
    values.update(params or {})
    return spec.build(values)


def frame_body(key: str, params: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """A named send with its trailing STOP removed.

    Used when merging one send into an existing frame: the frame already ends
    with its own stop, and a stop in the middle would break the turnaround the
    caller is trying to build.
    """
    body = build_frame(key, params)
    while body and body[-1][0] == "stop":
        body = body[:-1]
    return body


# --------------------------------------------------------------------------
# Composite blocks
# --------------------------------------------------------------------------
#
# A named send can sit in a frame as a single block rather than as the
# primitives it expands to: the stack then reads "Direct CCC, Broadcast CCC,
# Private Wr/Rd, Stop" instead of a dozen sdr_writes. The primitives are still
# available, and a composite can be expanded into them when a single bit needs
# tuning.

COMPOSITE_PREFIX = "send:"


def composite_kind(frame_key: str) -> str:
    """The ``Transaction.kind`` that stands for a whole named send."""
    return f"{COMPOSITE_PREFIX}{frame_key}"


def is_composite(kind: str) -> bool:
    """Whether `kind` is a named send rather than a protobuf payload."""
    return kind.startswith(COMPOSITE_PREFIX)


def composite_frame(kind: str) -> str:
    """The FRAMES key behind a composite kind."""
    return kind[len(COMPOSITE_PREFIX):]


@dataclass
class BlockInfo:
    """What a workspace needs to draw one block, composite or primitive."""

    label: str
    params: list[Param]
    category: str


def block_info(kind: str, params: dict[str, Any] | None = None) -> BlockInfo | None:
    """Describe `kind`, whichever sort of block it is.

    Passing the block's `params` lets a spec hide fields that do not apply to
    the values held -- a Direct CCC shows a byte count or a payload, not both.
    """
    if is_composite(kind):
        spec = FRAMES.get(composite_frame(kind))
        if spec is None:
            return None
        fields = spec.params
        if params is not None and spec.relevant is not None:
            fields = spec.relevant(params, spec.params)
        return BlockInfo(spec.label, fields, spec.category)
    spec = TRANSACTIONS.get(kind)
    if spec is None:
        return None
    return BlockInfo(spec.label, spec.params, "Primitives")


def expand(kind: str, params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The primitives a block stands for.

    A composite drops its own trailing STOP: the frame carries one of its own,
    and a stop in the middle would release the bus.
    """
    if not is_composite(kind):
        return [(kind, params)]
    return frame_body(composite_frame(kind), params)
