"""Area 3 -- the sequences people build over and over.

A flow is a named bundle of devices and steps. The GUI uses them two ways:

* **New from flow** replaces the script with the flow -- a worked example.
* **Insert flow** appends the flow's steps to the current script, adding any
  device the flow needs that is not already on the bus.

Because a flow is just devices and steps, everything it produces stays fully
editable afterwards: the frames it drops in can be opened and their individual
transactions tweaked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .catalog import DEVICES, OPERATIONS, default_params
from .model import SEND_FRAME, Device, Script, Step, Transaction
from .transactions import FRAMES, build_frame


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def device(kind: str, **overrides: Any) -> Device:
    """Build a device with the catalog defaults, then apply `overrides`."""
    spec = DEVICES[kind]
    params = default_params(spec.params)
    params.update(overrides)
    return Device(kind=kind, var=spec.default_var, params=params)


def call(op: str, target: str, comment: str = "", **overrides: Any) -> Step:
    """A direct method call step."""
    spec = OPERATIONS[op]
    params = default_params(spec.params)
    params.update(overrides)
    return Step(op=op, target_var=target, params=params, comment=comment)


def frame(kind: str, target: str, comment: str = "", **overrides: Any) -> Step:
    """A controller send, expanded from a named frame into its transactions."""
    spec = FRAMES[kind]
    label = comment or spec.label
    transactions = [
        Transaction(kind=tx_kind, params=tx_params)
        for tx_kind, tx_params in build_frame(kind, overrides)
    ]
    return Step(op=SEND_FRAME, target_var=target, transactions=transactions, comment=label)


@dataclass
class Flow:
    """A reusable bundle of devices and steps."""

    key: str
    label: str
    description: str
    #: Devices the flow needs. Matched against the script by kind, not by name.
    devices: list[Device] = field(default_factory=list)
    #: Built lazily so each use gets its own step objects.
    steps: Callable[[], list[Step]] = field(default_factory=lambda: (lambda: []))

    def as_script(self) -> Script:
        """The flow on its own, as a complete script."""
        script = Script(name=self.key, description=self.description)
        script.devices = [Device(d.kind, d.var, dict(d.params)) for d in self.devices]
        script.steps = self.steps()
        return script


CTRL = "i3c_controller"
TGT = "i3c_target"
FIFO = "i3c_fifo_target"
MEM = "i3c_mem_target"


# --------------------------------------------------------------------------
# The flows
# --------------------------------------------------------------------------

FLOWS: dict[str, Flow] = {}


def _flow(flow: Flow) -> None:
    FLOWS[flow.key] = flow


_flow(Flow(
    key="bus_bringup",
    label="Bus bring-up",
    description=(
        "Clear any dynamic addresses left over, then bring the bus up. "
        "init_bus() addresses the targets, so RSTDAA has to come first -- after "
        "it, it would undo the addressing it was meant to make room for."
    ),
    devices=[device("controller"), device("target_32bit_register", static_addr=0x45)],
    steps=lambda: [
        call("do_rstdaa", CTRL, "Clear any dynamic addresses left from a previous run"),
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
    ],
))

_flow(Flow(
    key="setdasa",
    label="Address by SETDASA",
    description="Assign a dynamic address from the target's static address, without ENTDAA.",
    devices=[device("controller"), device("target_32bit_register", static_addr=0x45)],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("direct_ccc", CTRL, "SETDASA: static 0x45 becomes dynamic 0x08",
              ccc="I3C_CCC_DIRECT_SETDASA", addresses=[0x45], data=[0x08 << 1]),
    ],
))

_flow(Flow(
    key="setaasa",
    label="Address by SETAASA",
    description="Tell every target to adopt its static address as its dynamic address.",
    devices=[device("controller"), device("target_32bit_register", static_addr=0x45)],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("broadcast_ccc", CTRL, "SETAASA",
              ccc="I3C_CCC_BROADCAST_SETAASA", data=[]),
    ],
))

_flow(Flow(
    key="discovery",
    label="Target discovery (GETPID / GETBCR / GETDCR)",
    description="Bring the bus up, then read each target's identity registers.",
    devices=[device("controller"), device("target_32bit_register", static_addr=0x45)],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("direct_ccc", CTRL, "GETPID from 0x08",
              ccc="I3C_CCC_DIRECT_GETPID", addresses=[0x08], count=6),
        frame("direct_ccc", CTRL, "GETBCR from 0x08",
              ccc="I3C_CCC_DIRECT_GETBCR", addresses=[0x08], count=1),
        frame("direct_ccc", CTRL, "GETDCR from 0x08",
              ccc="I3C_CCC_DIRECT_GETDCR", addresses=[0x08], count=1),
    ],
))

_flow(Flow(
    key="private_loopback",
    label="Private write then read back",
    description="Write a pattern to a register target over SDR, then read it back.",
    devices=[
        device("controller"),
        device("target_32bit_register", static_addr=0x45, sub_address_type="INCREMENT"),
    ],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("private_write", CTRL, "Private write DE AD BE EF to 0x08",
              address=0x08, data=[0xDE, 0xAD, 0xBE, 0xEF]),
        frame("private_read", CTRL, "Read the same four bytes back",
              address=0x08, count=4),
    ],
))

_flow(Flow(
    key="interleaved",
    label="Interleaved write / read in one frame",
    description=(
        "Write and read in a single frame, with no STOP between them. The bus "
        "is held across each turnaround, so the whole exchange is one frame."
    ),
    devices=[
        device("controller"),
        device("target_32bit_register", static_addr=0x45, sub_address_type="INCREMENT"),
    ],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("private_write_read", CTRL,
              "Write four bytes then read them back without releasing the bus",
              address=0x08, data=[0xDE, 0xAD, 0xBE, 0xEF], count=4),
        frame("private_read_addr", CTRL,
              "Point at sub-address 0x04, then read from it",
              address=0x08, sub_address=0x04, count=4),
    ],
))

_flow(Flow(
    key="hdr_ddr_loopback",
    label="HDR-DDR write then read back",
    description="An HDR-DDR write and read pair, each frame closed with an HDR Exit.",
    devices=[
        device("controller"),
        device("target_32bit_register", static_addr=0x45, sub_address_type="INCREMENT"),
    ],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("hdr_ddr_write", CTRL, "HDR-DDR write",
              command=0x08, address=0x08, data=[0x12, 0x34, 0x12, 0x34]),
        frame("hdr_ddr_read", CTRL, "HDR-DDR read back",
              command=0x08, address=0x08, count=4),
    ],
))

_flow(Flow(
    key="ibi",
    label="IBI (controller enables, target raises)",
    description=(
        "Enable in-band interrupts with ENEC, then have the target raise one. "
        "The target's BCR must have bit 1 set for the IBI to be allowed."
    ),
    devices=[
        device("controller"),
        device("target_32bit_register", static_addr=0x45, bcr=0x06),
    ],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("broadcast_ccc", CTRL, "ENEC: allow IBI, controller request and hot-join",
              ccc="I3C_CCC_BROADCAST_ENEC", data=[0x0B]),
        call("ibi_request", TGT, "Target raises an IBI carrying MDB 0xAA",
             ibi_type="IMMEDIATE", mdb_value=[0xAA], additional_data=[0x10, 0x30, 0x50]),
    ],
))

_flow(Flow(
    key="hotjoin",
    label="Hot-Join (target joins a running bus)",
    description="A target asks to join a bus that is already up; the controller answers with ENTDAA.",
    devices=[
        device("controller"),
        device("target_32bit_register", static_addr=0x45, bcr=0x06),
    ],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up"),
        frame("broadcast_ccc", CTRL, "ENEC: allow hot-join",
              ccc="I3C_CCC_BROADCAST_ENEC", data=[0x08]),
        call("send_hotjoin", TGT, "Target requests to join"),
    ],
))

_flow(Flow(
    key="i2c_legacy",
    label="Legacy I2C on the I3C bus",
    description=(
        "I2C transfers to a legacy target. These do not go through "
        "I3CControllerTransactionV2 -- the V2 message carries no I2C payload."
    ),
    devices=[device("controller"), device("target_32bit_register", static_addr=0x12)],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        call("i2c_write", CTRL, "I2C write to 0x12",
             address=0x12, data=[0xDE, 0xAD, 0xBE, 0xEF]),
        call("i2c_read", CTRL, "I2C read back from 0x12", address=0x12, count=4),
    ],
))

_flow(Flow(
    key="fifo_target",
    label="FIFO target preloaded, controller reads",
    description=(
        "Preload the target FIFO, read it out over a private read, then check "
        "what the FIFO has left."
    ),
    devices=[device("controller"), device("target_fifo", static_addr=0x45)],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        call("target_assigned_address", FIFO, "Confirm the address the target was given"),
        call("target_fifo_capacity", FIFO, "How big the FIFO is"),
        call("target_enqueue", FIFO, "Preload the FIFO", data=[0x01, 0x02, 0x03, 0x04]),
        call("target_fifo_fill", FIFO, "Bytes waiting before the read"),
        frame("private_read", CTRL, "Read the FIFO contents", address=0x08, count=4),
        call("target_fifo_fill", FIFO, "Bytes left after the read"),
        call("target_total_read", FIFO, "Bytes the controller has taken in total"),
    ],
))

_flow(Flow(
    key="memory_target",
    label="256-byte memory target, write and read back",
    description=(
        "Preload the 256-byte memory directly, have the controller read it over "
        "the bus, then write to it and check the memory changed."
    ),
    devices=[device("controller"), device("target_8bit_memory", static_addr=0x45)],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        call("target_assigned_address", MEM, "Confirm the address the target was given"),
        call("target_write_mem", MEM, "Preload memory at 0x00",
             addr=0x00, data=[0xDE, 0xAD, 0xBE, 0xEF]),
        frame("private_read", CTRL, "Controller reads it back over the bus",
              address=0x08, count=4),
        frame("private_write", CTRL, "Controller writes a new pattern",
              address=0x08, data=[0x11, 0x22, 0x33, 0x44]),
        call("target_read_mem", MEM, "Read the memory directly to confirm the write",
             addr=0x00, count=4),
    ],
))

_flow(Flow(
    key="patterns",
    label="HDR Exit and Target Reset patterns",
    description="Drive the two bus patterns on their own, each as its own frame.",
    devices=[device("controller")],
    steps=lambda: [
        call("init_bus", CTRL, "Bring the bus up and address the targets"),
        frame("hdr_exit", CTRL, "HDR Exit pattern"),
        frame("target_reset_pattern", CTRL, "Target Reset pattern"),
    ],
))

_flow(Flow(
    key="empty",
    label="Empty bus",
    description="Just the bus, nothing attached.",
))


def flow_labels() -> list[tuple[str, str]]:
    """`(key, label)` for every flow, in declaration order."""
    return [(key, flow.label) for key, flow in FLOWS.items()]


def missing_devices(script: Script, flow: Flow) -> list[Device]:
    """Devices the flow needs that `script` does not already have."""
    present = {d.kind for d in script.devices}
    return [
        Device(d.kind, script.unique_var(d.var), dict(d.params))
        for d in flow.devices
        if d.kind not in present
    ]
