"""Drives the GUI without a human: builds every form and every editing action.

Tkinter needs a window system; the whole module is skipped where there is none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from pxbuilder import timing as timing_spec  # noqa: E402
from pxbuilder.catalog import DEVICES, OPERATIONS, device_matches  # noqa: E402
from pxbuilder.flows import FLOWS  # noqa: E402
from pxbuilder.model import Transaction  # noqa: E402
from pxbuilder.transactions import (  # noqa: E402
    FRAMES,
    TRANSACTIONS,
    composite_kind,
    is_composite,
)


@pytest.fixture
def app():
    """A live App instance, torn down after the test."""
    from pxbuilder.gui import App

    try:
        instance = App()
    except tk.TclError as exc:  # pragma: no cover - headless CI
        pytest.skip(f"no display available: {exc}")
    instance.withdraw()
    yield instance
    instance.destroy()


def _nodes(app) -> list[str]:
    nodes = ["setup", "session", "bus", "devices"]
    for iid in ("bringup:init_bus", "transactions", "other"):
        if app.tree.exists(iid):
            nodes.append(iid)
    for i, device in enumerate(app.script.devices):
        nodes.append(f"dev:{i}")
        if DEVICES[device.kind].role == "controller":
            nodes += [f"timing:{i}", f"i2ctiming:{i}",
                      f"hdrpattern:{i}", f"hdrddr:{i}", f"daatable:{i}"]
            nodes += [f"phase:{i}:{p}" for p in timing_spec.PHASES]
    for i, step in enumerate(app.script.steps):
        if app.tree.exists(f"step:{i}"):
            nodes.append(f"step:{i}")
    return nodes


def test_every_outline_node_builds_a_form(app) -> None:
    """Selecting any node must render its editor without raising."""
    for key in FLOWS:
        app.new_from_flow(key)
        for node in _nodes(app):
            app.tree.selection_set(node)
            app.update_idletasks()
        assert app.code.get("1.0", "end").strip()


def test_adding_every_device_frame_and_operation(app) -> None:
    """Add one of everything, then confirm the preview still renders cleanly."""
    app.new_from_flow("empty")
    app._add_device("controller")
    for kind, spec in DEVICES.items():
        if spec.role == "target":
            app._add_device(kind)

    for key in FRAMES:
        app._add_send(key)
    for key, spec in OPERATIONS.items():
        target = next(
            (d.var for d in app.script.devices if device_matches(d.kind, spec.applies_to)),
            None,
        )
        assert target, f"no device for {key}"
        app._add_step(key)

    app.refresh_code()
    app.update_idletasks()
    assert app.status.cget("text") == ""
    assert "def main()" in app.code.get("1.0", "end")


def test_outline_lists_bringup_and_transaction_frames(app) -> None:
    app.new_from_flow("private_loopback")
    roots = [app.tree.item(iid, "text") for iid in app.tree.get_children("")]
    assert roots[0].startswith("1  Setup")
    assert any(text.startswith("2  Initialize the bus") for text in roots)
    assert any(text.startswith("3  Transactions") for text in roots)
    assert not any("Assign Dynamic Address" in text for text in roots), (
        "init_bus() covers addressing, so there is no separate DAA row"
    )
    frames = [i for i, step in enumerate(app.script.steps) if step.is_frame]
    assert frames
    for i in frames:
        assert app.tree.exists(f"step:{i}")
        assert app.tree.get_children(f"step:{i}") == ()


def test_empty_script_shows_a_single_bringup_placeholder(app) -> None:
    app.new_from_flow("empty")
    assert app.tree.exists("bringup:init_bus")
    assert not app.tree.exists("bringup:do_daa")
    assert app.tree.exists("transactions")


def test_adding_a_transaction_to_a_frame(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    before = len(app.script.steps[index].transactions)
    app.tree.selection_set(f"step:{index}")
    for kind in TRANSACTIONS:
        app._add_transaction(kind)
        app.tree.selection_set(f"step:{index}")
    assert len(app.script.steps[index].transactions) == before + len(TRANSACTIONS)
    app.refresh_code()
    assert app.status.cget("text") == ""


def test_add_primitive_outside_a_frame_does_nothing(app) -> None:
    app.new_from_flow("private_loopback")
    before = [len(step.transactions) for step in app.script.steps if step.is_frame]
    app.tree.selection_set("bus")
    app.build_form()
    app._add_transaction("sdr_write")
    after = [len(step.transactions) for step in app.script.steps if step.is_frame]
    assert after == before


def test_add_empty_transaction_creates_a_frame(app) -> None:
    """A new Transaction opens with its closing blocks already in place."""
    app.new_from_flow("bus_bringup")
    before = sum(1 for step in app.script.steps if step.is_frame)
    app._add_empty_frame()
    frames = [step for step in app.script.steps if step.is_frame]
    assert len(frames) == before + 1
    assert frames[-1].comment == "Transaction"
    assert [t.kind for t in frames[-1].transactions] == ["stop"], (
        "an Idle Time cannot end a frame, so a new one carries only the STOP"
    )


def test_empty_transaction_without_controller_is_refused(app, monkeypatch) -> None:
    from pxbuilder import gui

    seen: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: seen.append(a[0]))
    app.new_from_flow("empty")
    app._add_empty_frame()
    assert seen == ["No controller"]
    assert app.script.steps == []


def test_moving_a_transaction_within_its_frame(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    kinds = [t.kind for t in app.script.steps[index].transactions]
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._selected_tx = 0
    app._move(1)
    moved = [t.kind for t in app.script.steps[index].transactions]
    assert moved[:2] == [kinds[1], kinds[0]]


def test_removing_a_transaction(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    before = len(app.script.steps[index].transactions)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._selected_tx = 0
    app._remove()
    assert len(app.script.steps[index].transactions) == before - 1


def test_send_without_a_controller_is_refused(app, monkeypatch) -> None:
    from pxbuilder import gui

    seen: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: seen.append(a[0]))
    app.new_from_flow("empty")
    app._add_send("private_write")
    assert seen == ["No controller"]
    assert app.script.steps == []


def test_second_controller_is_refused(app, monkeypatch) -> None:
    from pxbuilder import gui

    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.new_from_flow("empty")
    app._add_device("controller")
    app._add_device("controller")
    assert len(app.script.devices) == 1


def test_renaming_a_device_updates_its_steps(app) -> None:
    app.new_from_flow("private_loopback")
    device = app.script.devices[0]
    app._rename_device(device, "my_ctrl")
    assert all(s.target_var == "my_ctrl" for s in app.script.steps)
    app.refresh_code()
    assert "my_ctrl" in app.code.get("1.0", "end")


def test_removing_a_device_orphans_its_steps(app) -> None:
    app.new_from_flow("private_loopback")
    app.tree.selection_set("dev:0")
    app._remove()
    assert any(s.target_var == "" for s in app.script.steps)
    app.refresh_code()
    assert app.status.cget("text")  # problems are surfaced, not swallowed


def test_duplicating_a_frame_copies_its_transactions(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    app.tree.selection_set(f"step:{index}")
    app._duplicate()
    original, copy = app.script.steps[index], app.script.steps[index + 1]
    assert [t.kind for t in copy.transactions] == [t.kind for t in original.transactions]
    # The copy must be independent, not an alias.
    copy.transactions[0].params["address"] = 0x77
    assert original.transactions[0].params["address"] != 0x77


def test_toggling_a_step_removes_it_from_the_output(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    app.tree.selection_set(f"step:{index}")
    before = app.code.get("1.0", "end").count("send_i3c_transactions(")
    app._toggle()
    app.refresh_code()
    assert app.code.get("1.0", "end").count("send_i3c_transactions(") == before - 1


@pytest.mark.parametrize("key", [k for k, f in FLOWS.items() if f.steps()])
def test_inserting_every_flow(app, key: str) -> None:
    """Insert a flow into an empty script; it must bring its devices with it."""
    app.new_from_flow("empty")
    app._insert_flow(key)
    app.refresh_code()
    assert app.status.cget("text").startswith("Inserted flow"), app.status.cget("text")
    assert "def main()" in app.code.get("1.0", "end")


def test_inserting_a_flow_reuses_existing_devices(app) -> None:
    app.new_from_flow("bus_bringup")
    before = len(app.script.devices)
    app._insert_flow("discovery")
    assert len(app.script.devices) == before
    assert all(s.target_var for s in app.script.steps)


def test_export_writes_utf8_python(app, tmp_path, monkeypatch) -> None:
    from pxbuilder import gui

    target = tmp_path / "out.py"
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: str(target))
    app.new_from_flow("discovery")
    app.export_python()
    source = target.read_text(encoding="utf-8")
    assert "I3CControllerTransactionV2(" in source
    compile(source, str(target), "exec")


# --------------------------------------------------------------------------
# Controller timing
# --------------------------------------------------------------------------


def test_controller_has_a_timing_subtree(app) -> None:
    app.new_from_flow("bus_bringup")
    assert app.tree.exists("timing:0")
    assert app.tree.exists("i2ctiming:0")
    for phase in timing_spec.PHASES:
        assert app.tree.exists(f"phase:0:{phase}")


def test_targets_have_no_timing_subtree(app) -> None:
    app.new_from_flow("bus_bringup")
    target = next(i for i, d in enumerate(app.script.devices)
                  if DEVICES[d.kind].role == "target")
    assert not app.tree.exists(f"timing:{target}")


def test_every_timing_form_renders(app) -> None:
    app.new_from_flow("bus_bringup")
    for node in ["timing:0", "i2ctiming:0"] + [
        f"phase:0:{p}" for p in timing_spec.PHASES
    ]:
        app._selection = node
        app.build_form()
        assert app.form.winfo_children(), f"{node} rendered an empty form"


def test_switching_a_phase_to_advanced_changes_the_output(app) -> None:
    app.new_from_flow("bus_bringup")
    block = app._timing_of(0)
    app._set_timing_enabled(block, True)
    app.refresh_code()
    assert "PxI3CMsgAdvancedTiming(" not in app.code.get("1.0", "end")

    app._set_phase_mode(block["phases"]["i3_c_rd_timing"], "advanced", 0, "i3_c_rd_timing")
    app.refresh_code()
    source = app.code.get("1.0", "end")
    assert "PxI3CMsgAdvancedTiming(" in source
    assert "(advanced)" in app.tree.item("phase:0:i3_c_rd_timing", "text")
    assert app.status.cget("text") == ""


def test_the_outline_marks_timing_off_only_when_it_is(app) -> None:
    app.new_from_flow("bus_bringup")
    assert "(off)" not in app.tree.item("timing:0", "text"), "timing is on by default"
    app._set_timing_enabled(app._timing_of(0), False)
    assert "(off)" in app.tree.item("timing:0", "text")


def _checkbuttons(parent) -> dict[str, object]:
    """Every Checkbutton under `parent`, keyed by the label it shows."""
    found: dict[str, object] = {}
    stack = [parent]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if isinstance(widget, ttk.Checkbutton):
            found[str(widget.cget("text"))] = widget
    return found


def test_ticking_a_reset_action_reaches_the_output(app) -> None:
    """The flag editor is the only multi-select form the GUI has."""
    app.new_from_flow("discovery")
    index = next(i for i, d in enumerate(app.script.devices)
                 if DEVICES[d.kind].role == "target")
    app.tree.selection_set(f"dev:{index}")
    app.build_form()

    boxes = _checkbuttons(app.form)
    assert "DATA" in boxes, "a target form does not offer the reset actions"
    boxes["DATA"].invoke()

    assert app.script.devices[index].params["reset_actions"] == ["DATA"]
    app.refresh_code()
    assert "reset_actions=PxI3CTargetResetAction.DATA" in app.code.get("1.0", "end")


def test_unticking_every_reset_action_goes_back_to_the_default(app) -> None:
    """Nothing ticked has to clear the argument, not emit an empty bitmask."""
    app.new_from_flow("discovery")
    index = next(i for i, d in enumerate(app.script.devices)
                 if DEVICES[d.kind].role == "target")
    app.tree.selection_set(f"dev:{index}")
    app.build_form()

    boxes = _checkbuttons(app.form)
    boxes["MWL"].invoke()
    assert app.script.devices[index].params["reset_actions"] == ["MWL"]
    boxes["MWL"].invoke()

    assert app.script.devices[index].params["reset_actions"] is None
    app.refresh_code()
    assert "reset_actions" not in app.code.get("1.0", "end")


def test_i2c_timing_toggle_reaches_the_output(app) -> None:
    app.new_from_flow("bus_bringup")
    i2c = app._i2c_timing_of(0)
    app._set_i2c_enabled(i2c, True)
    app.refresh_code()
    assert "i2_c_timing=PxI2CTimingConfig(" in app.code.get("1.0", "end")


# --------------------------------------------------------------------------
# Interleaving inside a frame
# --------------------------------------------------------------------------


def test_transactions_land_before_the_trailing_stop(app) -> None:
    """Adding to a frame must not put blocks after it has already ended."""
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    for kind in ("sdr_write", "sdr_read"):
        app.tree.selection_set(f"step:{index}")
        app._add_transaction(kind)
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == ["sdr_write", "sdr_read", "stop"]


def test_adding_after_a_selected_transaction(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_send("private_write")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._selected_tx = 0
    app._add_transaction("sdr_read")
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == [composite_kind("private_write"), "sdr_read", "stop"]


def test_merging_a_send_interleaves_without_a_stop(app) -> None:
    """Named sends stack as blocks; the frame still closes with one STOP."""
    app.new_from_flow("bus_bringup")
    app._add_send("private_write")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app._merge_send("private_read")

    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == [
        composite_kind("private_write"),
        composite_kind("private_read"),
        "stop",
    ]
    app.refresh_code()
    source = app.code.get("1.0", "end")
    assert source.count("send_i3c_transactions(") == 1
    assert source.count("I3CStop(") == 1


def test_the_broadcast_header_defaults_on(app) -> None:
    """Every named send starts with its 0x7E header, wherever it lands."""
    app.new_from_flow("bus_bringup")
    app._add_send("private_write")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app._merge_send("private_read")

    for transaction in app.script.steps[index].transactions:
        if "with_broadcast_hdr" in transaction.params:
            assert transaction.params["with_broadcast_hdr"] is True
    app.apply()
    assert app.code.get("1.0", "end").count("address=0x7E") == 2


def test_the_broadcast_header_can_be_turned_off_per_block(app) -> None:
    """It is a field on the block, so a repeated start is still expressible."""
    app.new_from_flow("bus_bringup")
    app._add_send("private_write")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app._merge_send("private_read")
    app.script.steps[index].transactions[1].params["with_broadcast_hdr"] = False
    app.apply()
    assert app.code.get("1.0", "end").count("address=0x7E") == 1


def test_merging_a_ccc_keeps_its_own_header(app) -> None:
    """A CCC needs its 0x7E + CCC header even mid-frame."""
    app.new_from_flow("bus_bringup")
    app._add_send("private_write")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app._merge_send("direct_ccc")
    app.refresh_code()
    source = app.code.get("1.0", "end")
    assert source.count("address=0x7E") == 2  # the write's header, then the CCC
    assert source.count("I3CStop(") == 1


def test_merge_outside_a_frame_does_nothing(app) -> None:
    app.new_from_flow("bus_bringup")
    before = len(app.script.steps)
    app.tree.selection_set("bus")
    app._merge_send("private_read")
    assert len(app.script.steps) == before


# --------------------------------------------------------------------------
# Scratch-style send workspace
# --------------------------------------------------------------------------


def _first_frame(app) -> int:
    return next(i for i, step in enumerate(app.script.steps) if step.is_frame)


def test_selecting_a_send_opens_the_block_workspace(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    workspace = app._block_workspace
    assert workspace is not None
    assert workspace.step_index == index
    assert workspace.hat is not None
    assert len(workspace.blocks) == len(app.script.steps[index].transactions)


def test_non_frame_step_keeps_the_form(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, step in enumerate(app.script.steps) if not step.is_frame)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    assert app._block_workspace is None
    assert app.form.winfo_children()


def test_palette_primitive_adds_a_transaction(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    before = [t.kind for t in app.script.steps[index].transactions]
    app._block_workspace.palette_click("sdr_read")
    app.build_form()
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert len(kinds) == len(before) + 1
    assert kinds[-1] == "stop"
    assert "sdr_read" in kinds[:-1]


def test_palette_named_send_merges_without_a_stop(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_send("private_write")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    assert app._block_workspace is not None
    app._block_workspace.palette_click("private_read")
    app.build_form()
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds.count("stop") == 1
    assert composite_kind("private_read") in kinds
    app.refresh_code()
    assert "I3CSdrRead(" in app.code.get("1.0", "end")


def test_inline_field_updates_the_model_and_preview(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    transactions = app.script.steps[index].transactions
    target = next(
        j for j, transaction in enumerate(transactions)
        if transaction.kind == "sdr_write" and transaction.params.get("address") != 0x7E
    )
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._block_workspace.blocks[target].invoke_select()
    app._block_workspace.blocks[target].set_param_text("address", "0x0A")
    assert app.script.steps[index].transactions[target].params["address"] == 0x0A
    app.refresh_code()
    assert "0x0A" in app.code.get("1.0", "end")


def test_clicking_a_stack_block_selects_it_in_the_workspace(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._block_workspace.blocks[1].invoke_select()
    assert app.tree.selection()[0] == f"step:{index}"
    assert app._block_workspace.selected_tx == 1
    assert app._selected_tx == 1


def test_switching_tx_selection_reuses_the_workspace(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    first = app._block_workspace
    first.blocks[0].invoke_select()
    assert app._block_workspace is first
    assert first.selected_tx == 0


def test_empty_transaction_accepts_named_patterns(app) -> None:
    """A pattern lands before the closing blocks, not after them."""
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._block_workspace.palette_click("private_write")
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == [composite_kind("private_write"), "stop"]


def test_dragging_a_block_reorders_it(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    before = [t.kind for t in app.script.steps[index].transactions]
    assert len(before) >= 3
    app._block_workspace.reorder_block(0, 2)
    after = [t.kind for t in app.script.steps[index].transactions]
    assert after == [before[1], before[0], *before[2:]]
    assert app._selected_tx == 1


def test_drop_palette_inserts_at_position(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._block_workspace.drop_palette("sdr_read", 0)
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds[0] == "sdr_read"


def test_reorder_no_op_when_dropped_on_self(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    before = [t.kind for t in app.script.steps[index].transactions]
    app._block_workspace.reorder_block(1, 1)
    app._block_workspace.reorder_block(1, 2)
    assert [t.kind for t in app.script.steps[index].transactions] == before


# --------------------------------------------------------------------------
# Preview follows the outline selection
# --------------------------------------------------------------------------


def _all_nodes(app, parent: str = "") -> list[str]:
    found: list[str] = []
    for child in app.tree.get_children(parent):
        found.append(child)
        found += _all_nodes(app, child)
    return found


def _source(app) -> list[str]:
    return app.code.get("1.0", "end-1c").splitlines()


def test_every_outline_node_resolves_to_code(app) -> None:
    """Selecting anything must land the preview somewhere, never nowhere."""
    for key in FLOWS:
        app.new_from_flow(key)
        if not app.script.devices:
            continue
        for node in _all_nodes(app):
            app.tree.selection_set(node)
            app.build_form()
            span = app._anchor_for(app._code_target())
            if span is None:
                # Only an empty container may resolve to nothing -- there is
                # genuinely no code under it to show.
                assert not app.tree.get_children(node), f"{key}/{node}"
                continue
            assert span[0] <= span[1], f"{key}/{node}"


def test_selection_highlights_the_matching_lines(app) -> None:
    app.new_from_flow("private_loopback")
    lines = _source(app)
    for node, expected in [
        ("bus", "# Configure the I3C bus"),
        ("dev:0", "# I3C Controller"),
        ("step:0", "# Bring the bus up"),
    ]:
        app.tree.selection_set(node)
        app.build_form()
        start, _end = app._anchor_for(app._code_target())
        assert lines[start - 1].strip().startswith(expected), node
        assert app.code.tag_ranges("selected"), f"{node} left the preview unmarked"


def test_ranges_do_not_overlap_between_siblings(app) -> None:
    """Each step owns its own lines, so the highlight is unambiguous."""
    app.new_from_flow("discovery")
    spans = []
    for i in range(len(app.script.steps)):
        span = app._anchors.get(f"step:{i}")
        if span:
            spans.append(span)
    spans.sort()
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end < start, f"step ranges overlap: {spans}"


def test_container_rows_span_their_children(app) -> None:
    app.new_from_flow("private_loopback")
    setup = app._anchor_for("setup")
    for child in ("session", "bus", "devices"):
        span = app._anchor_for(child)
        assert setup[0] <= span[0] and span[1] <= setup[1], child


def test_selecting_a_block_narrows_to_that_transaction(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    whole = app._anchor_for(app._code_target())

    seen = []
    for j in range(len(app.script.steps[index].transactions)):
        app._selected_tx = j
        app._sync_code_selection()
        span = app._anchor_for(app._code_target())
        assert whole[0] <= span[0] and span[1] <= whole[1], "block outside its frame"
        seen.append(span)
    assert len(set(seen)) == len(seen), "blocks share a range"


def test_timing_phases_anchor_once_the_block_is_on(app) -> None:
    app.new_from_flow("bus_bringup")
    app._set_timing_enabled(app._timing_of(0), True)
    app.refresh_code()
    lines = _source(app)
    for phase in timing_spec.PHASES:
        span = app._anchors.get(f"phase:0:{phase}")
        assert span is not None, phase
        assert lines[span[0] - 1].strip().startswith(phase), phase


def test_a_switched_off_block_falls_back_to_its_parent(app) -> None:
    """A block with no code of its own shows the controller instead of nothing."""
    app.new_from_flow("bus_bringup")
    app._set_i2c_enabled(app._i2c_timing_of(0), False)
    app.apply()
    assert "i2ctiming:0" not in app._anchors
    assert app._anchor_for("i2ctiming:0") == app._anchor_for("dev:0")


def test_anchors_survive_an_edit(app) -> None:
    app.new_from_flow("private_loopback")
    app.tree.selection_set("bus")
    app.build_form()
    app.script.bus.voltage = 1800
    app.refresh_code()
    start, _ = app._anchor_for("bus")
    assert any(
        "i3c_bus.voltage = 1800" in line
        for line in _source(app)[start - 1:start + 6]
    )


def test_anchors_are_cleared_when_generation_fails(app) -> None:
    app.new_from_flow("private_loopback")
    app.script.steps[0].target_var = "nope"
    app.refresh_code()
    assert app._anchors == {}
    assert not app.code.tag_ranges("selected")
    assert app.status.cget("text")


# --------------------------------------------------------------------------
# The standalone workspace window
# --------------------------------------------------------------------------


def _first_frame(app) -> int:
    return next(i for i, s in enumerate(app.script.steps) if s.is_frame)


def test_opening_and_closing_the_workspace_window(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()

    app.open_frame_editor(index)
    editor = app._frame_editor
    assert editor is not None and editor.winfo_exists()
    assert editor.workspace is not None
    assert len(editor.workspace.blocks) == len(app.script.steps[index].transactions)

    editor.close()
    assert app._frame_editor is None


def test_the_window_mirrors_the_main_preview(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app.open_frame_editor(index)
    editor = app._frame_editor

    assert editor.code.get("1.0", "end-1c") == app.code.get("1.0", "end-1c")
    assert editor.code.tag_ranges("selected")


def test_edits_reach_the_window(app) -> None:
    """The window shows the same transactions, not a copy of them."""
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app.open_frame_editor(index)
    editor = app._frame_editor

    before = len(app.script.steps[index].transactions)
    app._selected_tx = 0
    app._add_transaction("sdr_read")
    app.refresh_code()

    assert len(app.script.steps[index].transactions) == before + 1
    assert len(editor.workspace.blocks) == before + 1
    assert editor.code.get("1.0", "end-1c") == app.code.get("1.0", "end-1c")


def test_opening_a_second_frame_reuses_the_window(app) -> None:
    app.new_from_flow("private_loopback")
    frames = [i for i, s in enumerate(app.script.steps) if s.is_frame]
    app.open_frame_editor(frames[0])
    first = app._frame_editor
    app.open_frame_editor(frames[1])
    assert app._frame_editor is first, "a second window would fight for the same edits"
    assert first.step_index == frames[1]


def test_the_window_closes_when_its_frame_goes(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.open_frame_editor(index)
    app._selected_tx = None
    app.tree.selection_set(f"step:{index}")
    app._remove()
    app.refresh_code()
    assert app._frame_editor is None


def test_a_new_script_closes_the_window(app) -> None:
    app.new_from_flow("private_loopback")
    app.open_frame_editor(_first_frame(app))
    assert app._frame_editor is not None
    app.new_from_flow("bus_bringup")
    assert app._frame_editor is None


def test_opening_without_a_transaction_selected_says_so(app, monkeypatch) -> None:
    from pxbuilder import gui

    seen: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: seen.append(a[0]))
    app.new_from_flow("private_loopback")
    app.tree.selection_set("bus")
    app.build_form()
    app.open_frame_editor()
    assert seen == ["No Transaction selected"]
    assert app._frame_editor is None


def test_double_clicking_a_transaction_opens_the_window(app) -> None:
    app.new_from_flow("private_loopback")
    index = _first_frame(app)
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._on_tree_double_click()
    assert app._frame_editor is not None
    assert app._frame_editor.step_index == index


def test_double_clicking_a_plain_step_does_nothing(app) -> None:
    app.new_from_flow("private_loopback")
    app.tree.selection_set("step:0")
    app.build_form()
    app._on_tree_double_click()
    assert app._frame_editor is None


# --------------------------------------------------------------------------
# Composite blocks in the workspace
# --------------------------------------------------------------------------


def test_named_sends_stack_as_single_blocks(app) -> None:
    """The mockup's shape: one block per send, then the closing blocks."""
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    for key in ("direct_ccc", "broadcast_ccc", "private_write"):
        app._block_workspace.palette_click(key)
        app.build_form()

    assert [t.kind for t in app.script.steps[index].transactions] == [
        composite_kind("direct_ccc"),
        composite_kind("broadcast_ccc"),
        composite_kind("private_write"),
        "stop",
    ]
    assert len(app._block_workspace.blocks) == 4


def test_the_preview_waits_for_apply(app) -> None:
    """Adding a block marks the preview stale; Apply is what rebuilds it."""
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app.apply()
    before = app.code.get("1.0", "end").count("I3CControllerTransactionV2(")

    app._block_workspace.palette_click("private_write")
    app.build_form()
    assert app._pending, "the edit should have marked the preview stale"
    assert app.code.get("1.0", "end").count("I3CControllerTransactionV2(") == before

    app.apply()
    assert not app._pending
    assert app.code.get("1.0", "end").count("I3CControllerTransactionV2(") > before


def test_a_reused_workspace_is_rebuilt_when_the_stack_changes(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    assert app._reuse_block_workspace(index), "nothing changed, so it should reuse"

    app.script.steps[index].transactions.append(
        Transaction("sdr_read", {p.name: p.default
                                 for p in TRANSACTIONS["sdr_read"].params})
    )
    assert not app._reuse_block_workspace(index), "a changed stack must force a rebuild"


def test_expanding_a_block_keeps_the_generated_code(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_send("private_read")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app.refresh_code()
    before = app.code.get("1.0", "end")

    app._selected_tx = 0
    app._expand_block()
    app.refresh_code()

    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == ["sdr_write", "sdr_read", "stop"]
    assert app.code.get("1.0", "end") == before, "expanding changed the output"


def test_expanding_a_primitive_says_so(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._selected_tx = 0          # the closing STOP
    app._expand_block()
    assert "already a primitive" in app.status.cget("text")


def test_composite_blocks_survive_a_project_round_trip(app, tmp_path) -> None:
    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    path = tmp_path / "p.pxb.json"
    app.script.save(path)
    app.refresh_code()
    before = app.code.get("1.0", "end")

    app.project_path = None
    from pxbuilder.model import Script

    app.script = Script.load(path)
    app.refresh_all()
    app.refresh_code()
    assert is_composite(app.script.steps[index].transactions[0].kind)
    assert app.code.get("1.0", "end") == before


# --------------------------------------------------------------------------
# Dropdowns are wide enough for what they hold
# --------------------------------------------------------------------------


def _every_choice_list():
    """Every set of dropdown choices the GUI can show, with where it came from."""
    from pxbuilder import timing as t
    from pxbuilder.catalog import DEVICES as D, OPERATIONS as O
    from pxbuilder.transactions import FRAMES as F, TRANSACTIONS as T

    groups = [
        ("transaction", [spec.params for spec in T.values()]),
        ("frame", [spec.params for spec in F.values()]),
        ("device", [spec.params for spec in D.values()]),
        ("operation", [spec.params for spec in O.values()]),
        ("timing", [t.SCALARS, t.I2C_TIMING]),
        ("phase", [t.phase_params(phase) for phase in t.PHASES]),
    ]
    for origin, param_lists in groups:
        for params in param_lists:
            for param in params:
                if param.type in ("enum", "ccc") and param.choices:
                    yield f"{origin}:{param.name}", param.choices


def test_no_dropdown_clips_its_longest_choice() -> None:
    from pxbuilder.block_view import dropdown_width

    for where, choices in _every_choice_list():
        longest = max(len(c) for c in choices)
        assert dropdown_width(choices) >= longest, (
            f"{where} clips {max(choices, key=len)!r}"
        )


def test_the_ccc_dropdown_fits_the_longest_command() -> None:
    """I3C_CCC_BROADCAST_SETBUSCON is 27 characters; a fixed 22 clipped it."""
    from pxbuilder.block_view import dropdown_width
    from pxbuilder.transactions import CCC_CODES

    assert dropdown_width(CCC_CODES) >= max(len(c) for c in CCC_CODES)


def test_dropdown_width_stays_sane_at_the_edges() -> None:
    from pxbuilder.block_view import dropdown_width

    assert dropdown_width([]) >= 10
    assert dropdown_width(["A"]) >= 10           # never absurdly narrow
    assert dropdown_width(["X" * 200]) <= 40     # never absurdly wide


def test_the_rendered_ccc_box_is_wide_enough(app) -> None:
    """Check the real widget, not just the helper."""
    app.new_from_flow("bus_bringup")
    app._add_send("broadcast_ccc")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()

    block = app._block_workspace.blocks[0]
    field = block._fields["ccc"]
    box = field.widget
    assert int(box.cget("width")) >= len(app.script.steps[index].transactions[0].params["ccc"])
    longest = max(box.cget("values"), key=len)
    assert int(box.cget("width")) >= len(longest), f"{longest} would be clipped"


# --------------------------------------------------------------------------
# Right-click menu on the outline
# --------------------------------------------------------------------------


def _labels(app, node: str) -> list[str]:
    """The entries a right-click on `node` would offer."""
    menu = app._context_menu(node)
    last = menu.index("end")
    if last is None:
        return []
    return [
        "---" if menu.type(i) == "separator" else menu.entrycget(i, "label")
        for i in range(last + 1)
    ]


def _invoke(app, node: str, label: str) -> None:
    """Click one entry of the menu for `node`."""
    menu = app._context_menu(node)
    for i in range(menu.index("end") + 1):
        if menu.type(i) != "separator" and menu.entrycget(i, "label") == label:
            menu.invoke(i)
            return
    raise AssertionError(f"{label!r} not in {_labels(app, node)}")


def test_the_transactions_list_offers_the_two_daa_cccs(app) -> None:
    """They are not sends, so this is where they are offered instead."""
    app.new_from_flow("empty")
    labels = _labels(app, "transactions")
    assert "Add ENTDAA" in labels
    assert "Add RSTDAA" in labels
    # The ordinary add entries are still there underneath.
    assert "Add Transaction" in labels


@pytest.mark.parametrize(("label", "op", "method"), [
    ("Add ENTDAA", "do_daa", "do_daa()"),
    ("Add RSTDAA", "do_rstdaa", "do_rstdaa()"),
])
def test_adding_a_daa_from_the_transactions_list(
    app, label: str, op: str, method: str,
) -> None:
    app.new_from_flow("empty")
    app._add_device("controller")
    _invoke(app, "transactions", label)

    assert [s.op for s in app.script.steps] == [op]
    app.refresh_code()
    source = app.code.get("1.0", "end")
    assert method in source
    assert "send_i3c_transactions" not in source
    assert app.status.cget("text") == ""


def test_a_daa_row_sits_in_the_transactions_list(app) -> None:
    app.new_from_flow("empty")
    app._add_device("controller")
    _invoke(app, "transactions", "Add ENTDAA")

    index = next(i for i, st in enumerate(app.script.steps) if st.op == "do_daa")
    assert app.tree.parent(f"step:{index}") == "transactions"
    assert not app.tree.exists("other"), "it should not have made an Other section"


def test_a_daa_row_interleaves_with_the_sends_around_it(app) -> None:
    """It can go anywhere in the list, and keeps the position it was added at."""
    app.new_from_flow("empty")
    app._add_device("controller")
    app._add_send("broadcast_ccc")

    # Adding from a selected row inserts after it, so this lands in the middle.
    app.tree.selection_set("step:0")
    app.build_form()
    _invoke(app, "transactions", "Add RSTDAA")
    app._add_send("private_read")

    assert [st.op for st in app.script.steps][1] == "do_rstdaa"
    rows = app.tree.get_children("transactions")
    assert rows == ("step:0", "step:1", "step:2"), rows
    assert app.tree.item("step:1", "text").startswith(
        OPERATIONS["do_rstdaa"].label)


def test_the_daa_cccs_are_not_in_a_transaction_ccc_menu(app) -> None:
    """Inside a transaction the command byte alone would address nobody."""
    from pxbuilder.transactions import CCC_VIA_OPERATION

    app.new_from_flow("empty")
    app._add_device("controller")
    app._add_send("broadcast_ccc")
    step = next(s for s in app.script.steps if s.is_frame)
    choices = next(p for p in FRAMES["broadcast_ccc"].params
                   if p.name == "ccc").choices
    assert not [c for c in choices if c in CCC_VIA_OPERATION]
    assert step.transactions[0].params["ccc"] not in CCC_VIA_OPERATION


def test_every_outline_node_offers_a_menu(app) -> None:
    for key in FLOWS:
        app.new_from_flow(key)
        for node in _all_nodes(app) + [""]:
            assert _labels(app, node), f"{key}/{node} offered nothing"


def test_anything_can_be_added_from_anywhere(app) -> None:
    """The add entries are always there, including on blank space."""
    app.new_from_flow("private_loopback")
    for node in ("", "setup", "bus", "transactions", "dev:0", "step:0"):
        labels = _labels(app, node)
        assert "Add device" in labels
        assert "Add Transaction" in labels
        assert "Insert flow" in labels


def test_a_controller_cannot_be_duplicated_from_the_menu(app) -> None:
    """Only one controller may be active, so the entry is not offered."""
    app.new_from_flow("private_loopback")
    assert "Duplicate" not in _labels(app, "dev:0")
    assert "Duplicate" in _labels(app, "dev:1")


def test_the_enable_entry_names_what_it_will_do(app) -> None:
    app.new_from_flow("private_loopback")
    assert "Disable" in _labels(app, "step:0")
    app.tree.selection_set("step:0")
    _invoke(app, "step:0", "Disable")
    assert not app.script.steps[0].enabled
    assert "Enable" in _labels(app, "step:0")


def test_removing_a_device_from_the_menu(app) -> None:
    app.new_from_flow("private_loopback")
    before = len(app.script.devices)
    _invoke(app, "dev:1", "Remove")
    assert len(app.script.devices) == before - 1


def test_removing_a_step_from_the_menu(app) -> None:
    app.new_from_flow("private_loopback")
    before = len(app.script.steps)
    _invoke(app, "step:0", "Remove")
    assert len(app.script.steps) == before - 1


def test_duplicating_a_step_from_the_menu(app) -> None:
    app.new_from_flow("private_loopback")
    before = len(app.script.steps)
    _invoke(app, "step:0", "Duplicate")
    assert len(app.script.steps) == before + 1


def test_moving_a_step_from_the_menu(app) -> None:
    app.new_from_flow("private_loopback")
    first, second = app.script.steps[0].op, app.script.steps[1].op
    _invoke(app, "step:0", "Move down")
    assert [s.op for s in app.script.steps[:2]] == [second, first]


def test_a_bringup_placeholder_offers_to_add_its_step(app) -> None:
    """The row is a placeholder when the step is missing; the menu creates it."""
    app.new_from_flow("empty")
    app._add_device("controller")
    assert app.tree.exists("bringup:init_bus")
    _invoke(app, "bringup:init_bus", "Add Initialize bus")
    assert [s.op for s in app.script.steps] == ["init_bus"]


def test_a_frame_offers_its_own_window(app) -> None:
    app.new_from_flow("private_loopback")
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    assert "Open in its own window" in _labels(app, f"step:{index}")
    _invoke(app, f"step:{index}", "Open in its own window")
    assert app._frame_editor is not None


def test_a_plain_step_offers_no_window(app) -> None:
    app.new_from_flow("private_loopback")
    assert "Open in its own window" not in _labels(app, "step:0")


def test_the_timing_menu_toggles_the_block(app) -> None:
    app.new_from_flow("bus_bringup")
    assert "Switch off" in _labels(app, "timing:0"), "timing is on by default"
    _invoke(app, "timing:0", "Switch off")
    assert not app._timing_of(0)["enabled"]
    assert "Switch on" in _labels(app, "timing:0")
    _invoke(app, "timing:0", "Switch on")
    assert app._timing_of(0)["enabled"]


def test_the_phase_menu_switches_mode(app) -> None:
    app.new_from_flow("bus_bringup")
    app._set_timing_enabled(app._timing_of(0), True)
    node = "phase:0:i3_c_rd_timing"
    assert any("(current)" in label for label in _labels(app, node))
    _invoke(app, node, "Use advanced timing")
    assert app._timing_of(0)["phases"]["i3_c_rd_timing"]["mode"] == "advanced"
    app.refresh_code()
    assert "PxI3CMsgAdvancedTiming(" in app.code.get("1.0", "end")


def test_right_click_selects_the_row_it_acts_on(app) -> None:
    """Right-clicking must not act on whatever was selected before."""
    app.new_from_flow("private_loopback")
    app.tree.selection_set("bus")
    app.build_form()

    class _Event:
        y = 0
        x_root = 0
        y_root = 0

    event = _Event()
    event.y = app.tree.bbox("dev:1")[1] + 2 if app.tree.bbox("dev:1") else 0
    app.update_idletasks()
    bbox = app.tree.bbox("dev:1")
    if not bbox:
        pytest.skip("tree rows are not laid out in a withdrawn window")
    event.y = bbox[1] + 2
    assert app.tree.identify_row(event.y) == "dev:1"


def test_renaming_a_device_from_the_menu(app, monkeypatch) -> None:
    from pxbuilder import gui

    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "renamed_ctrl")
    app.new_from_flow("private_loopback")
    _invoke(app, "dev:0", "Rename...")
    assert app.script.devices[0].var == "renamed_ctrl"
    app.refresh_code()
    assert "renamed_ctrl" in app.code.get("1.0", "end")


def test_cancelling_a_rename_changes_nothing(app, monkeypatch) -> None:
    from pxbuilder import gui

    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: None)
    app.new_from_flow("private_loopback")
    before = app.script.devices[0].var
    _invoke(app, "dev:0", "Rename...")
    assert app.script.devices[0].var == before


def test_editing_a_comment_from_the_menu(app, monkeypatch) -> None:
    from pxbuilder import gui

    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "my note")
    app.new_from_flow("private_loopback")
    _invoke(app, "step:0", "Edit comment...")
    assert app.script.steps[0].comment == "my note"
    app.refresh_code()
    assert "# my note" in app.code.get("1.0", "end")


# --------------------------------------------------------------------------
# Apply gates the preview
# --------------------------------------------------------------------------


def test_a_fresh_script_is_already_applied(app) -> None:
    """Loading a flow is not an edit; its code is shown at once."""
    app.new_from_flow("private_loopback")
    assert not app._pending
    assert "send_i3c_transactions(" in app.code.get("1.0", "end")
    assert str(app.apply_button.cget("state")) == "disabled"


def test_editing_a_field_does_not_rebuild_the_preview(app) -> None:
    app.new_from_flow("private_loopback")
    app.apply()
    before = app.code.get("1.0", "end")

    app.script.bus.voltage = 1800
    app._schedule_refresh()
    assert app._pending
    assert app.code.get("1.0", "end") == before

    app.apply()
    assert "i3c_bus.voltage = 1800" in app.code.get("1.0", "end")


def test_the_apply_button_follows_the_pending_state(app) -> None:
    app.new_from_flow("private_loopback")
    app.apply()
    assert str(app.apply_button.cget("state")) == "disabled"
    assert app.code_title.cget("text") == "Generated code"

    app._add_device("target_fifo")
    assert str(app.apply_button.cget("state")) == "normal"
    assert "not applied" in app.code_title.cget("text")

    app.apply()
    assert str(app.apply_button.cget("state")) == "disabled"
    assert app.code_title.cget("text") == "Generated code"


def test_selecting_a_row_navigates_without_applying(app) -> None:
    """Browsing the outline must not sneak an unapplied change into the code."""
    app.new_from_flow("private_loopback")
    app.apply()
    app.script.bus.voltage = 1800
    app._schedule_refresh()
    stale = app.code.get("1.0", "end")

    for node in ("bus", "dev:0", "step:0"):
        app.tree.selection_set(node)
        app.build_form()
        assert app.code.get("1.0", "end") == stale
        assert app._pending
    # ...but it still scrolls and bands, so navigation keeps working.
    assert app.code.tag_ranges("selected")


def test_removing_a_step_waits_for_apply(app) -> None:
    app.new_from_flow("private_loopback")
    app.apply()
    before = app.code.get("1.0", "end")
    app.tree.selection_set("step:0")
    app._selected_tx = None
    app._remove()
    assert app.code.get("1.0", "end") == before
    app.apply()
    assert app.code.get("1.0", "end") != before


def test_the_workspace_window_mirrors_the_apply_state(app) -> None:
    app.new_from_flow("private_loopback")
    app.apply()
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    app.open_frame_editor(index)
    editor = app._frame_editor
    assert str(editor.apply_button.cget("state")) == "disabled"

    app._add_device("target_fifo")
    assert str(editor.apply_button.cget("state")) == "normal"
    assert "not applied" in editor.code_title.cget("text")

    app.apply()
    assert str(editor.apply_button.cget("state")) == "disabled"


def test_apply_from_the_workspace_window(app) -> None:
    app.new_from_flow("private_loopback")
    app.apply()
    index = next(i for i, s in enumerate(app.script.steps) if s.is_frame)
    app.open_frame_editor(index)
    editor = app._frame_editor

    app.script.bus.voltage = 1800
    app._schedule_refresh()
    editor.apply_button.invoke()
    assert not app._pending
    assert "i3c_bus.voltage = 1800" in editor.code.get("1.0", "end")


def test_exporting_uses_the_script_not_the_stale_preview(app, tmp_path, monkeypatch) -> None:
    """Export must never ship what the preview happens to be showing."""
    from pxbuilder import gui

    target = tmp_path / "out.py"
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: str(target))
    app.new_from_flow("private_loopback")
    app.apply()
    app.script.bus.voltage = 1800
    app._schedule_refresh()

    app.export_python()
    assert "i3c_bus.voltage = 1800" in target.read_text(encoding="utf-8")


def test_the_bus_defaults(app) -> None:
    """The values a new script starts with."""
    app.new_from_flow("empty")
    assert app.script.bus.voltage == 3300
    assert app.script.bus.pullup_resistance == 500
    app._add_device("controller")
    app.apply()
    source = app.code.get("1.0", "end")
    assert "i3c_bus.voltage = 3300" in source
    assert "i3c_bus.pullup_resistance = 500" in source


# --------------------------------------------------------------------------
# Direct CCC: one block that follows its command
# --------------------------------------------------------------------------


def test_only_one_direct_ccc_is_offered(app) -> None:
    from pxbuilder.transactions import frames_in

    labels = [spec.label for spec in frames_in("Direct CCC")]
    assert labels == ["Direct CCC"]


def test_a_new_target_takes_the_direction_of_the_command(app) -> None:
    from pxbuilder.transactions import default_direct_target

    assert default_direct_target("I3C_CCC_DIRECT_GETBCR")["direction"] == "read"
    assert default_direct_target("I3C_CCC_DIRECT_SETNEWDA")["direction"] == "write"

    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    block = app.script.steps[index].transactions[0]
    block.params["ccc"] = "I3C_CCC_DIRECT_GETBCR"
    block.params["targets"] = [default_direct_target("I3C_CCC_DIRECT_GETBCR")]
    app.apply()
    assert "I3CSdrRead(" in app.code.get("1.0", "end")


def test_each_target_entry_gets_its_own_repeated_start(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    block = app.script.steps[index].transactions[0]
    block.params["ccc"] = "I3C_CCC_DIRECT_GETBCR"
    block.params["targets"] = [
        {"address": a, "direction": "read", "count": 1, "data": []}
        for a in (0x08, 0x09, 0x0A)
    ]
    app.apply()

    source = app.code.get("1.0", "end")
    assert source.count("I3CSdrRead(") == 3
    for address in ("0x08", "0x09", "0x0A"):
        assert f"address={address}" in source
    assert source.count("address=0x7E") == 1, "one header for the whole frame"
    assert source.count("I3CStop(") == 1


def test_a_direction_against_the_command_is_noted_in_the_code(app) -> None:
    """The exerciser is for driving what the spec says you should not."""
    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    block = app.script.steps[index].transactions[0]
    block.params["ccc"] = "I3C_CCC_DIRECT_GETBCR"
    block.params["targets"] = [
        {"address": 0x08, "direction": "read", "count": 1, "data": []},
        {"address": 0x09, "direction": "write", "count": 1, "data": [0xAA]},
    ]
    app.apply()

    source = app.code.get("1.0", "end")
    assert "NOT TO SPEC" in source
    assert "is a read command, but this target is set to write" in source
    assert source.count("NOT TO SPEC") == 1, "only the offending entry is flagged"
    assert app.status.cget("text") == "", "it is a note, not a refusal"


def test_data_after_the_ccc_rides_on_the_header(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    block = app.script.steps[index].transactions[0]
    block.params["ccc"] = "I3C_CCC_DIRECT_SETNEWDA"
    block.params["defining_bytes"] = [0x02, 0x03]
    app.apply()
    assert (
        "data=bytes([PxI3CCommonCommandCode.I3C_CCC_DIRECT_SETNEWDA, 0x02, 0x03])"
        in app.code.get("1.0", "end")
    )


def test_old_projects_using_the_split_blocks_still_open(app, tmp_path) -> None:
    """direct_ccc_read / _write are gone from the palette but must still load."""
    from pxbuilder.model import Script
    from pxbuilder.transactions import composite_kind

    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    app.script.steps[index].transactions[0] = Transaction(
        composite_kind("direct_ccc_read"),
        {"ccc": "I3C_CCC_DIRECT_GETPID", "address": 0x08, "count": 6},
    )
    path = tmp_path / "old.pxb.json"
    app.script.save(path)

    app.script = Script.load(path)
    app.refresh_all()
    assert app.status.cget("text") == ""
    assert "I3CSdrRead(" in app.code.get("1.0", "end")


# --------------------------------------------------------------------------
# Deleting and inserting blocks
# --------------------------------------------------------------------------


def _workspace(app, key: str = "private_write"):
    app.new_from_flow("bus_bringup")
    app._add_send(key)
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    return index, app._block_workspace


def test_delete_removes_the_selected_block(app) -> None:
    index, workspace = _workspace(app)
    before = len(app.script.steps[index].transactions)
    workspace.apply_selection(0)
    app._selected_tx = 0
    workspace._on_delete_key()
    assert len(app.script.steps[index].transactions) == before - 1


def test_clicking_a_block_claims_the_key_focus(app, monkeypatch) -> None:
    """Otherwise Delete never reaches the handler at all.

    Tk delivers a key press to the focused widget and its bind tags, not to an
    ancestor, and a block is a frame that takes no focus -- so selecting one
    has to move the focus onto the widget that carries the binding.
    """
    _index, workspace = _workspace(app)
    taken: list[bool] = []
    monkeypatch.setattr(workspace._stack_canvas, "focus_set",
                        lambda: taken.append(True))

    workspace.blocks[0].event_generate("<Button-1>", x=2, y=2)
    workspace.update()

    assert taken, "a block click must claim the focus"
    assert workspace._stack_canvas.bind("<Delete>"), (
        "and the widget it claims it for must be the one handling Delete")


def test_delete_acts_with_the_focus_on_the_stack(app, monkeypatch) -> None:
    """The other half of the click: that focus is one the handler accepts.

    A generated key press is not delivered at all in a window this suite never
    maps, so the two halves are pinned either side of Tk's dispatch: the click
    claims the focus for the bound widget above, and the handler acts on that
    focus here.
    """
    index, workspace = _workspace(app)
    before = len(app.script.steps[index].transactions)
    app._selected_tx = 0
    workspace.apply_selection(0)
    monkeypatch.setattr(workspace, "focus_get", lambda: workspace._stack_canvas)

    assert workspace._on_delete_key() == "break", "the key must be consumed"
    assert len(app.script.steps[index].transactions) == before - 1


def test_delete_does_nothing_with_no_block_selected(app) -> None:
    index, workspace = _workspace(app)
    before = len(app.script.steps[index].transactions)
    app._selected_tx = None
    workspace.apply_selection(None)
    workspace._on_delete_key()
    assert len(app.script.steps[index].transactions) == before


def test_delete_is_not_bound_on_the_input_fields(app) -> None:
    """Tk routes Delete to the focused Entry, so it edits text rather than
    reaching the workspace. Nothing of ours may intercept it there."""
    index, workspace = _workspace(app)
    field = workspace.blocks[0]._fields.get("address")
    assert field is not None and field.widget is not None
    assert not field.widget.bind("<Delete>"), "a field must keep its own Delete"
    assert workspace.bind("<Delete>"), "the workspace handles Delete"


def test_delete_defers_when_a_field_has_the_focus(app, monkeypatch) -> None:
    """Belt and braces for the case where the event does reach the workspace."""
    index, workspace = _workspace(app)
    before = len(app.script.steps[index].transactions)
    app._selected_tx = 0
    workspace.apply_selection(0)

    field = workspace.blocks[0]._fields["address"]
    monkeypatch.setattr(workspace, "focus_get", lambda: field.widget)
    assert workspace._on_delete_key() is None
    assert len(app.script.steps[index].transactions) == before


def test_the_block_menu_offers_what_applies(app) -> None:
    index, workspace = _workspace(app)

    def labels(menu):
        last = menu.index("end")
        return [] if last is None else [
            "---" if menu.type(i) == "separator" else menu.entrycget(i, "label")
            for i in range(last + 1)
        ]

    composite = labels(workspace.block_menu(0))
    assert "Expand into primitives" in composite
    assert "Delete" in composite and "Duplicate" in composite
    assert "Insert before" in composite and "Insert after" in composite

    stop = labels(workspace.block_menu(len(workspace.blocks) - 1))
    assert "Expand into primitives" not in stop, "a primitive has nothing to expand"

    empty = labels(workspace.block_menu(None))
    assert empty == ["Insert"]


def test_deleting_from_the_block_menu(app) -> None:
    index, workspace = _workspace(app)
    before = len(app.script.steps[index].transactions)
    workspace.delete_block(0)
    assert len(app.script.steps[index].transactions) == before - 1
    assert "Deleted" in app.status.cget("text")


def test_duplicating_from_the_block_menu(app) -> None:
    index, workspace = _workspace(app)
    kinds = [t.kind for t in app.script.steps[index].transactions]
    workspace.callbacks.duplicate_at(0)
    after = [t.kind for t in app.script.steps[index].transactions]
    assert after == [kinds[0], kinds[0], *kinds[1:]]


def test_inserting_before_and_after_from_the_block_menu(app) -> None:
    index, workspace = _workspace(app)
    workspace._insert("sdr_read", 0)
    assert app.script.steps[index].transactions[0].kind == "sdr_read"

    workspace = app._block_workspace
    workspace._insert("idle_time", 1)
    assert app.script.steps[index].transactions[1].kind == "idle_time"


def test_expanding_from_the_block_menu(app) -> None:
    index, workspace = _workspace(app, "private_read")
    app.apply()
    before = app.code.get("1.0", "end")
    workspace.callbacks.expand_at(0)
    app.apply()
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == ["sdr_write", "sdr_read", "stop"]
    assert app.code.get("1.0", "end") == before


def test_deleting_the_last_block_clears_the_selection(app) -> None:
    index, workspace = _workspace(app)
    while app.script.steps[index].transactions:
        app._delete_block_at(0)
    assert app._selected_tx is None
    app.apply()
    assert "no transactions" in app.status.cget("text")


# --------------------------------------------------------------------------
# Block colours stay readable
# --------------------------------------------------------------------------


def _white_contrast(hex_colour: str) -> float:
    def channel(index: int) -> float:
        value = int(hex_colour[index:index + 2], 16) / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5))
    return 1.05 / (luminance + 0.05)


def test_every_block_fill_carries_white_text() -> None:
    """Labels are white and bold at 9pt, so each fill needs 4.5:1."""
    from pxbuilder import block_view as bv

    fills = {
        **{k: v[0] for k, v in bv._BLOCK_COLORS.items()},
        **{k: v[0] for k, v in bv._CATEGORY_COLORS.items()},
        "frame": bv._FRAME_FILL,
        "snippet": bv._SNIPPET_FILL,
    }
    for name, fill in fills.items():
        assert _white_contrast(fill) >= 4.5, (
            f"{name} {fill} is {_white_contrast(fill):.2f}:1 against white"
        )


def test_the_categories_stay_told_apart() -> None:
    """Muted is fine; indistinguishable is not."""
    import colorsys

    from pxbuilder import block_view as bv

    def hue(hex_colour: str) -> float:
        rgb = tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
        h, _s, _v = colorsys.rgb_to_hsv(*rgb)
        return h * 360

    coloured = {
        name: fill for name, (fill, _edge) in bv._CATEGORY_COLORS.items()
        if name != "Patterns"
    }
    names = list(coloured)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            gap = abs(hue(coloured[first]) - hue(coloured[second]))
            gap = min(gap, 360 - gap)
            assert gap >= 20, f"{first} and {second} are only {gap:.0f} degrees apart"


def test_the_session_form_offers_the_teardown_switch(app) -> None:
    app.new_from_flow("private_loopback")
    app.tree.selection_set("session")
    app.build_form()
    labels = [
        child.cget("text") for child in app.form.winfo_children()
        if child.winfo_class() == "TCheckbutton"
    ]
    assert any("Detach every device" in text for text in labels)


def test_turning_the_teardown_off_from_the_form(app) -> None:
    app.new_from_flow("private_loopback")
    app.apply()
    assert "detach_from_bus()" in app.code.get("1.0", "end")

    app.script.detach_on_finish = False
    app._schedule_refresh()
    app.apply()
    assert "detach_from_bus()" not in app.code.get("1.0", "end")


def test_an_idle_time_is_refused_where_it_cannot_sit(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()

    app._add_transaction("idle_time")
    assert [t.kind for t in app.script.steps[index].transactions] == ["stop"]
    assert "between two transfers" in app.status.cget("text")


def test_an_idle_time_lands_between_two_transfers(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    for kind in ("sdr_write", "sdr_read"):
        app.tree.selection_set(f"step:{index}")
        app.build_form()
        app._add_transaction(kind)

    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._add_transaction("idle_time")
    kinds = [t.kind for t in app.script.steps[index].transactions]
    assert kinds == ["sdr_write", "idle_time", "sdr_read", "stop"]
    app.apply()
    assert app.status.cget("text") == ""


def test_one_transfer_is_still_not_enough_for_an_idle(app) -> None:
    app.new_from_flow("bus_bringup")
    app._add_empty_frame()
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._add_transaction("sdr_write")

    app.tree.selection_set(f"step:{index}")
    app.build_form()
    app._add_transaction("idle_time")
    assert "idle_time" not in [t.kind for t in app.script.steps[index].transactions]
    assert "only one" in app.status.cget("text")


# --------------------------------------------------------------------------
# The repeating target editor
# --------------------------------------------------------------------------


def _direct_ccc(app):
    app.new_from_flow("bus_bringup")
    app._add_send("direct_ccc")
    index = len(app.script.steps) - 1
    app.tree.selection_set(f"step:{index}")
    app.build_form()
    block = app._block_workspace.blocks[0]
    assert block._target_rows is not None, "the entries need their own editor"
    return index, block._target_rows


def test_the_editor_shows_a_row_per_target(app) -> None:
    index, rows = _direct_ccc(app)
    assert len(rows.rows) == len(rows.entries) == 1


def test_the_plus_adds_a_target(app) -> None:
    index, rows = _direct_ccc(app)
    rows._add()
    rows._add()
    assert len(rows.entries) == 3
    assert len(rows.rows) == 3
    app.apply()
    assert app.code.get("1.0", "end").count("address_header_policy") >= 4


def test_a_new_row_follows_the_command(app) -> None:
    index, rows = _direct_ccc(app)
    rows.transaction.params["ccc"] = "I3C_CCC_DIRECT_SETNEWDA"
    rows._add()
    assert rows.entries[-1]["direction"] == "write"


def test_the_minus_removes_a_target(app) -> None:
    index, rows = _direct_ccc(app)
    rows._add()
    rows._remove(0)
    assert len(rows.entries) == 1


def test_the_last_target_cannot_be_removed(app) -> None:
    index, rows = _direct_ccc(app)
    rows._remove(0)
    assert len(rows.entries) == 1
    assert "at least one target" in app.status.cget("text")


def test_switching_a_row_to_read_swaps_its_payload_field(app) -> None:
    index, rows = _direct_ccc(app)
    rows.entries[0]["direction"] = "write"
    rows._redraw()
    labels = _row_labels(rows.rows[0])
    assert "Data" in labels and "Count" not in labels

    rows.entries[0]["direction"] = "read"
    rows._redraw()
    labels = _row_labels(rows.rows[0])
    assert "Count" in labels and "Data" not in labels


def _row_labels(row) -> list[str]:
    return [
        child.cget("text") for child in row.winfo_children()
        if child.winfo_class() == "Label"
    ]


def test_the_entries_reach_the_generated_code(app) -> None:
    index, rows = _direct_ccc(app)
    rows.entries[0].update(address=0x11, direction="write", data=[0xCA, 0xFE])
    rows._add()
    rows.entries[1].update(address=0x22, direction="read", count=3)
    app.apply()

    source = app.code.get("1.0", "end")
    assert "address=0x11" in source and "bytes([0xCA, 0xFE])" in source
    assert "address=0x22" in source and "count=3" in source


def test_the_remove_button_is_a_boxed_light_chip(app) -> None:
    """It should read as a button, not as part of the block's text."""
    index, rows = _direct_ccc(app)
    row = rows.rows[0]
    remove = [
        child for child in row.winfo_children()
        if child.winfo_class() == "Label" and child.cget("text") == "−"
    ]
    assert remove, "no remove control on the row"
    button = remove[0]
    assert int(button.cget("highlightthickness")) >= 1, "no outline"
    assert str(button.cget("highlightbackground")).upper() == "#FFFFFF", "not white"
    assert str(button.cget("background")) != str(rows._fill), "not a light fill"


def test_the_remove_glyph_stays_readable_on_its_chip(app) -> None:
    index, rows = _direct_ccc(app)
    button = [
        child for child in rows.rows[0].winfo_children()
        if child.winfo_class() == "Label" and child.cget("text") == "−"
    ][0]
    assert _contrast(str(button.cget("foreground")),
                     str(button.cget("background"))) >= 4.5


def _contrast(first: str, second: str) -> float:
    def luminance(colour: str) -> float:
        def channel(index: int) -> float:
            value = int(colour[index:index + 2], 16) / 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# --------------------------------------------------------------------------
# The chips must respond to an actual click
# --------------------------------------------------------------------------


def _chip(parent, text: str):
    found = [
        child for child in parent.winfo_children()
        if child.winfo_class() == "Label" and child.cget("text") == text
    ]
    assert found, f"no {text!r} control"
    return found[0]


def _click(widget) -> None:
    widget.event_generate("<Button-1>", x=2, y=2)
    widget.update()


def test_clicking_add_target_adds_one(app) -> None:
    """Through the binding, not by calling the method: the block rebinds
    <Button-1> across its subtree, which once replaced this handler."""
    index, rows = _direct_ccc(app)
    before = len(rows.entries)
    _click(_chip(rows, "+  add target"))
    assert len(rows.entries) == before + 1
    # The rows are redrawn, so fetch the chip again before clicking twice.
    _click(_chip(rows, "+  add target"))
    assert len(rows.entries) == before + 2


def test_clicking_minus_removes_that_row(app) -> None:
    index, rows = _direct_ccc(app)
    _click(_chip(rows, "+  add target"))
    rows.entries[0]["address"] = 0x11
    rows.entries[1]["address"] = 0x22
    rows._redraw()
    rows.update()  # event_generate is not delivered to unmapped widgets

    _click(_chip(rows.rows[0], "−"))
    assert [e["address"] for e in rows.entries] == [0x22]


def test_a_chip_click_does_not_start_a_block_drag(app) -> None:
    index, rows = _direct_ccc(app)
    workspace = app._block_workspace
    _click(_chip(rows, "+  add target"))
    assert workspace._drag is None, "the click was taken as a drag on the block"


def test_the_chips_keep_their_own_binding(app) -> None:
    """Guard the guard: the block's press handler must not have replaced it."""
    index, rows = _direct_ccc(app)
    for chip in (_chip(rows, "+  add target"), _chip(rows.rows[0], "−")):
        binding = chip.bind("<Button-1>") or ""
        assert "_on_press" not in binding, "the block rebound this chip"
        assert binding, "the chip lost its click"
