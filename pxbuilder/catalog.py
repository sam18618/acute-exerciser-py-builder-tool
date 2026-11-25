"""Schema describing the I3C surface of aqpxlib that the builder exposes.

The GUI builds its parameter forms from these tables, and the code generator
uses them to decide how each value is rendered into Python source. Adding a new
device or operation is a matter of adding an entry here -- no GUI code changes.

The I3C surface is organised into three areas, mirroring how a test is written:

1. **Setup** -- the bus, and the controller and targets attached to it
   (:data:`DEVICES`).
2. **Send functions** -- what each device can do once it is on the bus
   (:data:`OPERATIONS`, split into the ``controller`` and ``target`` groups).
3. **Flows** -- ready-made sequences for the things people build over and over
   (:mod:`pxbuilder.flows`).

Keeping this as data (rather than introspecting aqpxlib at runtime) is
deliberate: the builder must run on machines that have no PX software or
hardware installed.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

ParamType = Literal[
    "int",           # plain decimal integer
    "hex",           # integer, rendered as 0x..
    "bool",          # True / False
    "enum",          # one of `choices`, rendered as a dotted constant
    "enum_flags",    # any number of `choices`, OR'd into a bitmask
    "bytes",         # list of byte values, rendered as [0x.., 0x..]
    "bytes_literal",  # list of byte values, rendered as bytes([0x.., ..])
    "ccc",           # a PxI3CCommonCommandCode member name
    "byte_pairs",    # list of (defining_byte, bytes) tuples
    "ccc_targets",   # repeated-start entries: address + read/write + payload
    "str",
    "int_list",      # list of ints, rendered as [0x.., ...]
    "pairs",         # list of (addr, [data]) tuples
    "addr_pairs",    # list of (old_addr, new_addr) tuples
]

#: Groups an operation can belong to. The GUI shows one submenu per group.
Group = Literal["controller", "target"]


@dataclass
class Param:
    """One editable parameter of a device or operation."""

    name: str
    label: str
    type: ParamType
    default: Any = None
    choices: tuple[str, ...] = ()
    #: Dotted path to the enum, e.g. ``PxI3CBaseController.ParityMode``. May
    #: contain ``{cls}``, which is replaced with the class of the device the
    #: call is made on -- IBI and hot-join enums live on each target class.
    enum_prefix: str = ""
    help: str = ""
    #: When False the argument is omitted from the generated call if it still
    #: holds `default` -- keeps generated code close to hand-written samples.
    always_emit: bool = False


def default_params(params: "list[Param]") -> dict[str, Any]:
    """A fresh set of values from a parameter list.

    Deep-copied: a default that is a list or a dict is one object shared by the
    whole catalog, so handing it out directly would let two blocks edit the same
    list without either knowing.
    """
    return {p.name: copy.deepcopy(p.default) for p in params}


def is_set(block: "dict[str, Any] | None") -> bool:
    """Whether an all-optional block has anything worth emitting.

    Some parts of the controller config -- the HDR timings, the dynamic address
    table -- are optional field by field, so they get no on/off switch. "Did
    the user fill anything in" is what decides whether the argument appears.
    """
    return bool(block) and any(value is not None for value in block.values())


#: The controller's preconfigured dynamic address table
#: (``PxI3CBaseControllerConfig.dyn_addr_table``). do_daa() documents this as
#: what drives address selection: with a priority table set, each new target
#: takes the next unused address from it, and once the list runs out no further
#: addresses are assigned. Every field is optional, so the block follows the
#: same "emitted once something is filled in" rule as the HDR timings.
#:
#: The table's ``entries`` -- match a target by PID mask, hand it a specific
#: address -- are not here: a row of three numbers needs an editor the GUI does
#: not have yet.
DAA_TABLE: list[Param] = [
    Param("priority_addr_table", "Priority addresses", "bytes_literal", default=None,
          help="Dynamic addresses to hand out, in order. Blank assigns "
               "sequentially from the available pool instead."),
    Param("reserved_addr_mask_l", "Reserved mask (low)", "hex", default=None,
          help="Addresses DAA must not hand out, 0x00-0x3F."),
    Param("reserved_addr_mask_h", "Reserved mask (high)", "hex", default=None,
          help="Addresses DAA must not hand out, 0x40-0x7F."),
    Param("used_addr_mask_l", "Used mask (low)", "hex", default=None,
          help="Addresses already taken, 0x00-0x3F."),
    Param("used_addr_mask_h", "Used mask (high)", "hex", default=None,
          help="Addresses already taken, 0x40-0x7F."),
]


def default_daa_table() -> "dict[str, Any]":
    """The dynamic address table, blank -- which emits nothing."""
    return default_params(DAA_TABLE)


def normalise_daa_table(block: "dict[str, Any] | None") -> "dict[str, Any]":
    """Fill in anything a project file is missing for the DAA table."""
    base = default_daa_table()
    if block:
        base.update({k: v for k, v in block.items() if k in base})
    return base


@dataclass
class DeviceSpec:
    """A device class that can be attached to the bus."""

    key: str
    label: str
    cls: str
    role: Literal["controller", "target"]
    import_from: str
    default_var: str
    params: list[Param] = field(default_factory=list)
    help: str = ""


@dataclass
class OpSpec:
    """A method call that can appear as a step in the script body."""

    key: str
    label: str
    method: str
    #: Which devices this call can be made on. Entries are either a role
    #: (``controller`` / ``target``) or a specific device key, so an operation
    #: that only exists on one target class can say so.
    applies_to: tuple[str, ...] = ("controller",)
    params: list[Param] = field(default_factory=list)
    #: Name of the variable the return value is bound to, if any.
    returns: str = ""
    #: How to describe the result in the generated log line.
    result_label: str = ""
    help: str = ""
    group: Group = "controller"
    category: str = "General"
    #: Lines to emit instead of a plain method call, for operations that are not
    #: one. Placeholders: ``{receiver}``, ``{result}`` and each parameter name.
    template: tuple[str, ...] = ()
    #: Extra names this operation needs imported from ``aqpxlib.i3c``.
    extra_imports: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Shared parameter fragments
# --------------------------------------------------------------------------

HEADER_POLICY = Param(
    "header_policy",
    "Header policy",
    "enum",
    default="OPTIMIZED",
    choices=("PUSH_PULL", "OPTIMIZED", "OPEN_DRAIN_SLOW", "OPEN_DRAIN_FAST"),
    enum_prefix="PxI3CBaseController.TransactionHeaderPolicy",
    help="How the 0x7E broadcast header is driven.",
)

STOP_TYPE = Param(
    "stop_type",
    "Stop type",
    "enum",
    default="PUSH_PULL",
    choices=("PUSH_PULL", "OPEN_DRAIN", "HDR_EXIT", "TARGET_RESET"),
    enum_prefix="PxI3CBaseController.TransactionStopType",
    help="Electrical form of the STOP condition.",
)

PARITY_MODE = Param(
    "parity_mode",
    "Parity mode",
    "enum",
    default="ALL_CORRECT",
    choices=("RAW", "FLAG", "ALL_CORRECT", "ALL_INCORRECT"),
    enum_prefix="PxI3CBaseController.ParityMode",
    help="Use ALL_INCORRECT to deliberately inject parity errors.",
)

XFER_MODE = Param(
    "mode",
    "Transfer mode",
    "enum",
    default="SDR",
    choices=("SDR", "HDR_DDR"),
    enum_prefix="PxI3CBaseController.Mode",
)


def _private_transfer_params() -> list[Param]:
    """Optional arguments shared by the private read/write transfers."""
    return [
        Param(
            "with_broadcast_hdr",
            "Send 0x7E broadcast header",
            "bool",
            default=True,
        ),
        Param(
            "header_policy",
            "Header policy",
            "enum",
            default="OPEN_DRAIN_FAST",
            choices=HEADER_POLICY.choices,
            enum_prefix=HEADER_POLICY.enum_prefix,
            help=HEADER_POLICY.help,
        ),
        STOP_TYPE,
    ]


def _common_target_params(*, with_sub_address: bool = False,
                          dcr_default: int = 0x00) -> list[Param]:
    """Constructor arguments shared by every I3C target class."""
    params = [
        Param("static_addr", "Static address", "hex", default=0x45, always_emit=True,
              help="7-bit static address, used by SETDASA / SETAASA."),
        Param("pid", "PID", "hex", default=0x92A00000000,
              help="48-bit Provisioned ID. 0x92A00000000 is the Acute default."),
        Param("bcr", "BCR", "hex", default=0x00,
              help="Bus Characteristics Register. Bit 1 must be set for the "
                   "target to be allowed to raise an IBI."),
        Param("dcr", "DCR", "hex", default=dcr_default,
              help="Device Characteristics Register."),
    ]
    if with_sub_address:
        params.append(
            Param(
                "sub_address_type",
                "Sub-address behaviour",
                "enum",
                default="NONE",
                choices=("NONE", "REPEAT", "IGNORE", "INCREMENT", "INCREMENT_WITH_LOOP"),
                enum_prefix="{cls}.SubAddressType",
                help="How the register pointer moves between accesses.",
                always_emit=True,
            ),
        )
    params += [
        Param("minimum_mwl", "Minimum MWL", "int", default=None,
              help="Leave blank to use the device default."),
        Param("minimum_mrl", "Minimum MRL", "int", default=None,
              help="Leave blank to use the device default."),
        Param("supported_group_address_count", "Group addresses", "int", default=3,
              help="How many group addresses the target accepts (DEFGRPA / SETGRPA)."),
        # New in aqpxlib 0.8. Only effective when BCR bit 2 is set; it is also
        # the third byte the target returns to GETMRL.
        Param("ibi_payload_size", "IBI payload size", "int", default=None,
              help="Maximum IBI payload in bytes, 0 for unlimited. Only used "
                   "when BCR bit 2 is set. Blank leaves it unset, which the "
                   "device treats as 0."),
        # What the target answers to the informational CCCs. Leave blank to let
        # the device decide; set them to test a controller's parsing.
        Param("getcaps_std_value", "GETCAPS value", "bytes_literal", default=None,
              help="Standard GETCAPS response."),
        Param("getcaps_usr_value", "GETCAPS user values", "byte_pairs", default=None,
              help="Per-defining-byte GETCAPS responses."),
        Param("getstatus_std_value", "GETSTATUS value", "bytes_literal", default=None,
              help="Standard GETSTATUS response."),
        Param("getstatus_usr_value", "GETSTATUS user values", "byte_pairs", default=None,
              help="Per-defining-byte GETSTATUS responses."),
        Param("getmxds_std_value", "GETMXDS value", "bytes_literal", default=None,
              help="Standard GETMXDS response."),
        Param("getmxds_usr_value", "GETMXDS user values", "byte_pairs", default=None,
              help="Per-defining-byte GETMXDS responses."),
        # What a reset does to the target, new in aqpxlib 0.7. The actions are a
        # bitmask: omitting it is not "nothing", it is everything except DATA.
        Param("reset_pin", "Reset pin (GPIO)", "int", default=None,
              help="GPIO pin the target watches as a simulated hardware reset. "
                   "Blank gives the target no reset pin."),
        Param("reset_actions", "Reset actions", "enum_flags", default=None,
              choices=("SPIKE_FILTER", "ASSIGNED_ADDRESS", "GROUP_ADDRESSES",
                       "MWL", "MRL", "DATA"),
              enum_prefix="PxI3CTargetResetAction",
              help="What a Target Reset Pattern or a GPIO reset clears. Blank "
                   "uses the library default, which is everything except DATA. "
                   "Tick DATA to wipe the target's contents as well."),
    ]
    return params


# --------------------------------------------------------------------------
# Area 1 -- Setup: bus, controller and targets
# --------------------------------------------------------------------------

DEVICES: dict[str, DeviceSpec] = {
    "controller": DeviceSpec(
        key="controller",
        label="I3C Controller",
        cls="PxI3CBaseController",
        role="controller",
        import_from="aqpxlib.i3c",
        default_var="i3c_controller",
        help=(
            "The bus controller. Only one may be active at a time -- the generated "
            "code falls back to the existing controller if attaching fails."
        ),
        params=[
            Param("enabled", "Enabled", "bool", default=True, always_emit=True,
                  help="Emitted as PxI3CBaseControllerConfig(enabled=...)."),
            Param("receive_ibi_data_amount", "IBI data to receive", "int", default=None,
                  help="Bytes of IBI payload to accept. Blank uses the library default."),
            Param("prohibit_hotjoin", "Prohibit Hot-Join", "bool", default=False,
                  help="Refuse Hot-Join requests. Off is the library default, and is "
                       "emitted only when switched on."),
        ],
    ),
    "target_32bit_register": DeviceSpec(
        key="target_32bit_register",
        label="32-Bit Register Target",
        cls="PxI3C32BitRegisterTarget",
        role="target",
        import_from="aqpxlib.i3c",
        default_var="i3c_target",
        help="Target exposing a bank of 32-bit registers.",
        params=_common_target_params(with_sub_address=True),
    ),
    "target_8bit_memory": DeviceSpec(
        key="target_8bit_memory",
        label="256-Byte Memory Target",
        cls="PxI3C8BitAddressMemoryTarget",
        role="target",
        import_from="aqpxlib.i3c",
        default_var="i3c_mem_target",
        help=(
            "A 256-byte memory with 8-bit addressing. The controller reads and "
            "writes it over the bus; the script can preload it directly."
        ),
        params=_common_target_params(),
    ),
    "target_fifo": DeviceSpec(
        key="target_fifo",
        label="FIFO Target",
        cls="PxI3CFifoTarget",
        role="target",
        import_from="aqpxlib.i3c",
        default_var="i3c_fifo_target",
        help=(
            "A FIFO rather than addressable storage: the script enqueues bytes "
            "for the controller to read, and can check the fill level afterwards."
        ),
        params=_common_target_params(),
    ),
    "target_mctp_endpoint": DeviceSpec(
        key="target_mctp_endpoint",
        label="MCTP Endpoint",
        cls="PxI3CMctpEndpoint",
        role="target",
        import_from="aqpxlib.i3c",
        default_var="i3c_mctp_endpoint",
        help=(
            "A FIFO target answering with the MCTP DCR (0xCC) -- which is how "
            "get_targets() tells it apart from a plain FIFO target. Everything "
            "the FIFO target does, plus staging a whole MCTP-over-I3C frame."
        ),
        params=_common_target_params(dcr_default=0xCC),
    ),
}


def device_matches(device_kind: str, applies_to: tuple[str, ...]) -> bool:
    """Whether an operation restricted to `applies_to` can run on `device_kind`."""
    spec = DEVICES.get(device_kind)
    if spec is None:
        return False
    return spec.role in applies_to or spec.key in applies_to


# --------------------------------------------------------------------------
# Area 2 -- Send functions
# --------------------------------------------------------------------------

OPERATIONS: dict[str, OpSpec] = {}

TARGETS_ALL = ("target",)
#: The classes with a FIFO. PxI3CMctpEndpoint subclasses PxI3CFifoTarget, so
#: everything the FIFO target can do, it can do too.
TARGETS_FIFO = ("target_fifo", "target_mctp_endpoint")


def _op(spec: OpSpec) -> None:
    OPERATIONS[spec.key] = spec


# == Controller: bus management and patterns ===============================

_op(OpSpec(
    key="init_bus",
    label="Initialize bus",
    method="init_bus",
    category="Bus / patterns",
    returns="bus_ready",
    result_label="Bus initialization",
    help="Bring the bus up. Pass a voltage to override the bus setting.",
    params=[Param("voltage", "Voltage (mV)", "int", default=None,
                  help="Leave blank to reuse the voltage already set on the bus.")],
))

_op(OpSpec(
    key="do_daa",
    label="Dynamic Address Assignment (ENTDAA)",
    method="do_daa",
    category="Bus / patterns",
    returns="daa_ok",
    result_label="DAA",
    help=(
        "Assign dynamic addresses to every target on the bus. Which addresses "
        "get handed out follows the controller's dynamic address table."
    ),
    params=[
        Param("max_assignments", "Max assignments", "int", default=None,
              help="Stop after this many addresses. Blank (or 0) means no "
                   "limit, which is the library default."),
    ],
))

_op(OpSpec(
    key="do_rstdaa",
    label="Reset Dynamic Addresses (RSTDAA)",
    method="do_rstdaa",
    category="Bus / patterns",
    returns="rstdaa_ok",
    result_label="RSTDAA",
))

_op(OpSpec(
    key="reset_bus",
    label="Reset bus",
    method="reset_bus",
    category="Bus / patterns",
))

# == Controller: I2C transfers on the I3C bus ==============================
#
# Legacy I2C is deliberately NOT part of the V2 transaction model:
# I3CControllerTransactionV2 carries no I2C payload, so these stay as the
# dedicated calls that route through send_i2c_transactions().

_op(OpSpec(
    key="i2c_write",
    label="I2C write",
    method="i2c_write",
    category="I2C Wr/Rd",
    returns="i2c_ack",
    result_label="I2C write acknowledged",
    help="Legacy I2C write to a target on the I3C bus.",
    params=[
        Param("address", "Target address", "hex", default=0x12, always_emit=True),
        Param("data", "Data", "bytes", default=[0xDE, 0xAD, 0xBE, 0xEF], always_emit=True),
    ],
))

_op(OpSpec(
    key="i2c_read",
    label="I2C read",
    method="i2c_read",
    category="I2C Wr/Rd",
    returns="i2c_data",
    result_label="I2C read",
    params=[
        Param("address", "Target address", "hex", default=0x12, always_emit=True),
        Param("count", "Byte count", "int", default=4, always_emit=True),
    ],
))

_op(OpSpec(
    key="i2c_write_addr",
    label="I2C write to sub-address",
    method="i2c_write_addr",
    category="I2C Wr/Rd",
    returns="i2c_ack",
    result_label="I2C write acknowledged",
    params=[
        Param("address", "Target address", "hex", default=0x12, always_emit=True),
        Param("sub_address", "Sub-address", "hex", default=0x00, always_emit=True),
        Param("data", "Data", "bytes", default=[0xDE, 0xAD, 0xBE, 0xEF], always_emit=True),
    ],
))

_op(OpSpec(
    key="i2c_read_addr",
    label="I2C read from sub-address",
    method="i2c_read_addr",
    category="I2C Wr/Rd",
    returns="i2c_data",
    result_label="I2C read",
    params=[
        Param("address", "Target address", "hex", default=0x12, always_emit=True),
        Param("sub_address", "Sub-address", "hex", default=0x00, always_emit=True),
        Param("count", "Byte count", "int", default=4, always_emit=True),
    ],
))

# == Target: events ========================================================

_op(OpSpec(
    key="ibi_request",
    label="IBI request",
    method="ibi_request",
    group="target",
    category="Events",
    applies_to=TARGETS_ALL,
    help=(
        "Raise an In-Band Interrupt. The controller must have enabled IBIs "
        "(ENEC) and the target's BCR bit 1 must be set, unless you skip the check."
    ),
    params=[
        Param("ibi_type", "IBI type", "enum", default="IMMEDIATE",
              choices=("IMMEDIATE", "ARBITRATED"), enum_prefix="{cls}.IbiType",
              always_emit=True,
              help="ARBITRATED waits for the bus to be free; IMMEDIATE drives at once."),
        Param("mdb_value", "MDB value", "bytes_literal", default=[0x5A],
              help="Mandatory Data Byte. Blank sends none."),
        Param("additional_data", "Additional data", "bytes_literal", default=None,
              help="Payload after the MDB. Requires the controller to allow it."),
        Param("skip_bcr_check", "Skip BCR check", "bool", default=False,
              help="Raise the IBI even when BCR says the target may not."),
    ],
))

_op(OpSpec(
    key="send_hotjoin",
    label="Hot-Join request",
    method="send_hotjoin",
    group="target",
    category="Events",
    applies_to=TARGETS_ALL,
    help="Request to join a bus that is already running. The controller answers with ENTDAA.",
    params=[
        Param("hotjoin_type", "Hot-Join type", "enum", default="IMMEDIATE",
              choices=("IMMEDIATE", "ARBITRATED"), enum_prefix="{cls}.HotJoinType",
              always_emit=True),
    ],
))

# == Target: data ==========================================================

_op(OpSpec(
    key="target_enqueue",
    label="Enqueue FIFO data",
    method="enqueue",
    group="target",
    category="Target data",
    applies_to=TARGETS_FIFO,
    help="Preload the FIFO the controller will read back.",
    params=[
        Param("data", "Data", "bytes", default=[0x01, 0x02, 0x03, 0x04], always_emit=True),
    ],
))

_op(OpSpec(
    key="target_send_mctp_packet",
    label="Send MCTP packet",
    method="send_mctp_packet",
    group="target",
    category="Target data",
    applies_to=("target_mctp_endpoint",),
    help=(
        "Stage a whole MCTP-over-I3C frame in the endpoint's TX FIFO. The "
        "library checks FIFO capacity, the frame length floor (5 bytes, 6 with "
        "SOM) and the transmission-unit ceiling before it goes anywhere."
    ),
    params=[
        Param("packet", "Packet", "bytes", default=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06],
              always_emit=True,
              help="A complete frame, PEC byte last. The default is a "
                   "placeholder of the minimum length -- replace it with the "
                   "frame you mean to send."),
        Param("skip_pec_check", "Skip the PEC check", "bool", default=True,
              always_emit=True,
              help="The PEC covers the address the controller assigns during "
                   "DAA, so the builder cannot compute it ahead of time. Leave "
                   "this on for a hand-written packet; untick it once the last "
                   "byte really is the PEC for this endpoint's address."),
        Param("announce", "Announce with an IBI", "bool", default=True,
              help="Raise an IBI (MDB 0xAE) to tell the controller a reply is "
                   "waiting. On by default in the library."),
        Param("unit", "Transmission unit", "int", default=None,
              help="The unit the controller negotiated, in bytes. Blank uses "
                   "the MCTP baseline of 64."),
    ],
))

_op(OpSpec(
    key="target_write_mem",
    label="Write target memory",
    method="write_mem",
    group="target",
    category="Target data",
    applies_to=("target_8bit_memory",),
    help="Preload the target's memory so a controller read has something to return.",
    params=[
        Param("addr", "Memory address", "hex", default=0x00, always_emit=True),
        Param("data", "Data", "bytes", default=[0xDE, 0xAD, 0xBE, 0xEF], always_emit=True),
    ],
))

_op(OpSpec(
    key="target_read_mem",
    label="Read target memory",
    method="read_mem",
    group="target",
    category="Target data",
    applies_to=("target_8bit_memory",),
    returns="mem_data",
    result_label="Target memory",
    help="Read the target's memory directly, without going over the bus.",
    params=[
        Param("addr", "Memory address", "hex", default=0x00, always_emit=True),
        Param("count", "Byte count", "int", default=4, always_emit=True),
    ],
))

# == Target: state read-back ===============================================
#
# These are properties on the target class, not calls, so they are emitted as
# plain attribute reads. They make a sample self-checking: you can see the
# address the target was actually given, or how much of the FIFO was drained.

def _state_op(key: str, label: str, attr: str, applies_to: tuple[str, ...],
              result: str, help_text: str = "") -> None:
    _op(OpSpec(
        key=key,
        label=label,
        method=attr,
        group="target",
        category="Target state",
        applies_to=applies_to,
        returns=result,
        result_label=label,
        help=help_text,
        template=("{result} = {receiver}." + attr,),
    ))


_state_op("target_assigned_address", "Assigned dynamic address", "assigned_address",
          TARGETS_ALL, "assigned_address",
          "The address the controller actually gave this target during DAA.")
_state_op("target_mwl", "Max write length (MWL)", "mwl", TARGETS_ALL, "target_mwl")
_state_op("target_mrl", "Max read length (MRL)", "mrl", TARGETS_ALL, "target_mrl")
_state_op("target_group_addresses", "Group addresses", "group_addresses",
          TARGETS_ALL, "group_addresses")
_state_op("target_ibi_payload_size", "IBI payload size", "ibi_payload_size",
          TARGETS_ALL, "ibi_payload_size",
          "The current maximum IBI payload in bytes, 0 for unlimited. None "
          "unless BCR bit 2 is set.")
_state_op("target_fifo_capacity", "FIFO capacity", "tx_capacity", TARGETS_FIFO,
          "fifo_capacity", "Total size of the target FIFO.")
_state_op("target_fifo_fill", "FIFO fill level", "tx_fill", TARGETS_FIFO,
          "fifo_fill", "Bytes still waiting in the FIFO.")
_state_op("target_total_read", "Bytes read from the target", "total_read",
          TARGETS_FIFO, "total_read")
_state_op("target_total_written", "Bytes written to the target", "total_written",
          TARGETS_FIFO, "total_written")
_state_op("target_register_value", "Register value", "register_value",
          ("target_32bit_register",), "register_value",
          "The register the controller has been reading and writing.")
_state_op("target_mctp_address", "MCTP address", "mctp_address",
          ("target_mctp_endpoint",), "mctp_address",
          "The bus address the endpoint's PEC is computed over: the assigned "
          "one after DAA, otherwise the static one.")


_op(OpSpec(
    key="target_set_register",
    label="Set register value",
    method="register_value",
    group="target",
    category="Target data",
    applies_to=("target_32bit_register",),
    help="Set the register the controller will read back. This is a property, not a call.",
    template=("{receiver}.register_value = {value}",),
    params=[
        Param("value", "Register value", "hex", default=0x12345678, always_emit=True),
    ],
))


# --------------------------------------------------------------------------
# Grouping helpers for the GUI
# --------------------------------------------------------------------------

#: Category order within each group, so the menus read the way a test is built.
CATEGORY_ORDER: dict[str, list[str]] = {
    "controller": [
        "Bus / patterns",
        "I2C Wr/Rd",
    ],
    "target": [
        "Events",
        "Target data",
        "Target state",
    ],
}

GROUP_LABELS: dict[str, str] = {
    "controller": "Controller",
    "target": "Target",
}


def groups() -> list[str]:
    """Operation groups, in menu order."""
    return ["controller", "target"]


def categories(group: str | None = None) -> list[str]:
    """Categories in `group` (or every category), in menu order."""
    present = {o.category for o in OPERATIONS.values()
               if group is None or o.group == group}
    ordered = CATEGORY_ORDER.get(group, []) if group else [
        c for g in groups() for c in CATEGORY_ORDER[g]
    ]
    seen = [c for c in ordered if c in present]
    return seen + sorted(present - set(seen))


def ops_in(category: str, group: str | None = None) -> list[OpSpec]:
    """Operations belonging to `category`, in declaration order."""
    return [
        o for o in OPERATIONS.values()
        if o.category == category and (group is None or o.group == group)
    ]


def ops_for_device(device_kind: str) -> list[OpSpec]:
    """Every operation that can be performed on `device_kind`."""
    return [o for o in OPERATIONS.values() if device_matches(device_kind, o.applies_to)]
