"""Scratch-style block workspace for a controller send frame.

The outline tree stays the source of navigation; this widget is the editor for
``send_frame`` steps. Palette chips call back into the app to add a primitive
or merge a named send. Stack blocks edit ``Transaction.params`` in place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import tkinter as tk
from tkinter import ttk

from . import values
from .catalog import Param
from .model import Step, Transaction
from .transactions import (
    FRAMES,
    default_direct_target,
    TRANSACTIONS,
    block_info,
    composite_frame,
    frame_categories,
    frames_in,
    is_composite,
)

# Scratch-like fills (body, darker edge).
_BLOCK_COLORS: dict[str, tuple[str, str]] = {
    "sdr_write": ("#3F6E96", "#305676"),
    "sdr_read": ("#3F6E96", "#305676"),
    "hdr_ddr_write": ("#6E5B96", "#564776"),
    "hdr_ddr_read": ("#6E5B96", "#564776"),
    # Stop and Idle close a frame rather than carrying data, so they read as
    # furniture rather than as another transfer.
    "stop": ("#6E6E6E", "#565656"),
    "idle_time": ("#6E6E6E", "#565656"),
}

#: Composite blocks take their colour from the category they came from.
#:
#: Muted rather than vivid, and dark enough that the white bold labels clear
#: 4.5:1 against every fill -- the previous saturated set ran as low as 1.6:1
#: on the amber. Hues stay at least 20 degrees apart so the categories are
#: still told apart at a glance.
_CATEGORY_COLORS: dict[str, tuple[str, str]] = {
    "Direct CCC": ("#8A6A1E", "#6B5116"),
    "Broadcast CCC": ("#A75A33", "#834526"),
    "Private Wr/Rd": ("#4C7A42", "#3A5F33"),
    "HDR-DDR": ("#6E5B96", "#564776"),
    "Patterns": ("#6E6E6E", "#565656"),
    "Primitives": ("#3F6E96", "#305676"),
}
_FRAME_FILL, _FRAME_EDGE = "#4F5A63", "#3C454C"
_SNIPPET_FILL, _SNIPPET_EDGE = "#A2536B", "#813F54"
#: The line marking where a dragged block would land.
_DROP_LINE = "#00A2FF"
_DROP_LINE_H = 5
_PALETTE_BG = "#E4E4E6"
_STACK_BG = "#D8D8DB"
_NOTCH_X = 18
_NOTCH_W = 16
_NOTCH_H = 7
_CAP_TOP = 14
_CAP_BOT = 12
_RAIL = 16
_FONT = ("Segoe UI", 9, "bold")
_SMALL = ("Segoe UI", 8)
_DRAG_THRESHOLD = 6
_INPUTS = (tk.Entry, ttk.Entry, ttk.Combobox, tk.Checkbutton, ttk.Checkbutton)


@dataclass
class BlockCallbacks:
    """App hooks the workspace talks to. Kept narrow so this module stays UI."""

    add_primitive: Callable[[str], None]
    merge_frame: Callable[[str], None]
    select_node: Callable[[str], None]
    on_change: Callable[[], None]
    on_status: Callable[[str], None]
    refresh_tree: Callable[[], None]
    #: Move the block at ``src`` so it sits at insert-before index ``dest``.
    reorder: Callable[[int, int], None]
    #: Drop a palette key (primitive or named send) at insert-before ``dest``.
    insert_at: Callable[[int, str], None]
    #: Delete the block at the given index.
    delete_at: Callable[[int], None]
    #: Copy the block at the given index, placing the copy after it.
    duplicate_at: Callable[[int], None]
    #: Break the named send at the given index into its primitives.
    expand_at: Callable[[int], None]


def _mix(hex_color: str, toward: str = "#D8D8DB", amount: float = 0.45) -> str:
    """Fade ``hex_color`` toward ``toward`` (used for disabled blocks)."""
    def parts(value: str) -> tuple[int, int, int]:
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)

    r, g, b = parts(hex_color)
    tr, tg, tb = parts(toward)
    return (
        f"#{int(r + (tr - r) * amount):02X}"
        f"{int(g + (tg - g) * amount):02X}"
        f"{int(b + (tb - b) * amount):02X}"
    )


#: Widest a dropdown is allowed to get, in characters.
_DROPDOWN_MAX = 32
_DROPDOWN_MIN = 10


def dropdown_width(choices: tuple[str, ...] | list[str]) -> int:
    """Characters wide enough for the longest choice.

    Measured rather than fixed: the CCC list runs to 27 characters
    (I3C_CCC_BROADCAST_SETBUSCON) and a hard-coded width silently clipped it.
    """
    if not choices:
        return _DROPDOWN_MIN
    longest = max(len(str(choice)) for choice in choices)
    # One spare column so the value never sits flush against the arrow.
    return max(_DROPDOWN_MIN, min(_DROPDOWN_MAX, longest + 1))


def _colors(kind: str, *, enabled: bool = True) -> tuple[str, str]:
    if is_composite(kind):
        spec = FRAMES.get(composite_frame(kind))
        category = spec.category if spec else ""
        fill, edge = _CATEGORY_COLORS.get(category, ("#888888", "#666666"))
    else:
        fill, edge = _BLOCK_COLORS.get(kind, ("#888888", "#666666"))
    if enabled:
        return fill, edge
    return _mix(fill), _mix(edge)


def structure_key(step: Step) -> tuple:
    """Identity of the stack, used to decide whether to rebuild.

    The visible field names are part of it, not just the kinds: a Direct CCC
    shows a byte count or a payload depending on the command it holds, so
    changing the command has to redraw the block.
    """
    shape = []
    for transaction in step.transactions:
        info = block_info(transaction.kind, transaction.params)
        fields = tuple(p.name for p in info.params) if info else ()
        shape.append((transaction.kind, fields))
    return (id(step), tuple(shape))


def _draw_top(canvas: tk.Canvas, width: int, color: str, *, hat: bool) -> None:
    canvas.delete("shape")
    height = _CAP_TOP
    if hat:
        points = [
            0, height, 0, 8, 8, 0, width - 8, 0, width, 8, width, height,
        ]
    else:
        points = [
            0, height, 0, 0,
            _NOTCH_X, 0, _NOTCH_X, _NOTCH_H,
            _NOTCH_X + _NOTCH_W, _NOTCH_H, _NOTCH_X + _NOTCH_W, 0,
            width, 0, width, height,
        ]
    canvas.create_polygon(points, fill=color, outline=color, tags="shape")


def _draw_bottom(canvas: tk.Canvas, width: int, color: str) -> None:
    canvas.delete("shape")
    height = _CAP_BOT
    points = [
        0, 0, width, 0, width, height - _NOTCH_H,
        _NOTCH_X + _NOTCH_W, height - _NOTCH_H, _NOTCH_X + _NOTCH_W, height,
        _NOTCH_X, height, _NOTCH_X, height - _NOTCH_H,
        0, height - _NOTCH_H,
    ]
    canvas.create_polygon(points, fill=color, outline=color, tags="shape")


class _Cap(tk.Canvas):
    """Top or bottom puzzle-piece cap that redraws when the block is resized."""

    def __init__(self, parent: tk.Widget, color: str, *, kind: str) -> None:
        height = _CAP_TOP if kind == "top" or kind == "hat" else _CAP_BOT
        super().__init__(
            parent, height=height, highlightthickness=0, borderwidth=0,
            background=_STACK_BG,
        )
        self._color = color
        self._kind = kind
        self.bind("<Configure>", self._redraw)

    def recolor(self, color: str) -> None:
        self._color = color
        self._redraw()

    def _redraw(self, _event: object = None) -> None:
        width = max(self.winfo_width(), 80)
        if self._kind == "hat":
            _draw_top(self, width, self._color, hat=True)
        elif self._kind == "top":
            _draw_top(self, width, self._color, hat=False)
        else:
            _draw_bottom(self, width, self._color)


class _InlineField:
    """One Param editor sitting on a colored block."""

    def __init__(
        self,
        parent: tk.Widget,
        param: Param,
        value: Any,
        fill: str,
        setter: Callable[[Any], None],
        on_status: Callable[[str], None],
        on_change: Callable[[], None],
    ) -> None:
        self.param = param
        #: The input this field draws, so callers (and tests) can inspect it.
        self.widget: tk.Widget | None = None
        self._var: tk.Variable
        row = tk.Frame(parent, bg=fill)
        row.pack(side=tk.LEFT, padx=3, pady=2)
        tk.Label(
            row, text=param.label, bg=fill, fg="white", font=_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 3))

        if param.type == "bool":
            self._var = tk.BooleanVar(value=bool(value))

            def commit(*_a: object) -> None:
                setter(bool(self._var.get()))
                on_status("")
                on_change()

            self._var.trace_add("write", commit)
            check = tk.Checkbutton(
                row, variable=self._var, bg=fill, activebackground=fill,
                fg="white", selectcolor=_mix(fill, "#FFFFFF", 0.25),
                highlightthickness=0, bd=0,
            )
            check.pack(side=tk.LEFT)
            self.widget = check
            return

        if param.type in ("enum", "ccc"):
            self._var = tk.StringVar(value="" if value is None else str(value))

            def commit(*_a: object) -> None:
                setter(self._var.get())
                on_status("")
                on_change()

            self._var.trace_add("write", commit)
            box = ttk.Combobox(
                row, textvariable=self._var, values=list(param.choices),
                state="readonly", width=dropdown_width(param.choices),
                font=_SMALL,
            )
            box.pack(side=tk.LEFT)
            self.widget = box
            return

        self._var = tk.StringVar(value=values.to_text(param, value))
        widths = {
            "hex": 8, "int": 6, "bytes": 16, "bytes_literal": 16, "int_list": 16,
        }
        entry = tk.Entry(
            row, textvariable=self._var, width=widths.get(param.type, 12),
            font=_SMALL, relief=tk.FLAT, bg="white",
        )
        entry.pack(side=tk.LEFT)
        self.widget = entry

        def commit(*_a: object) -> None:
            try:
                setter(values.from_text(param, self._var.get()))
                entry.configure(fg="black")
                on_status("")
            except values.ValueError_ as exc:
                entry.configure(fg="#c00")
                on_status(str(exc))
                return
            on_change()

        self._var.trace_add("write", commit)

    def set_text(self, text: str) -> None:
        """Drive the widget from tests or a programmatic edit."""
        self._var.set(text)


class _Chip(tk.Label):
    """A clickable control inside a block.

    StackBlock rebinds <Button-1> over its whole subtree so any part of a block
    can start a drag. That replaces, rather than adds to, an existing binding,
    so controls that own their click are marked with this type and skipped.
    """


class _TargetRows(tk.Frame):
    """The repeated-start entries of a Direct CCC: address, direction, payload.

    A row per target, a minus to drop one and a plus to add another. The
    direction defaults from the command but stays editable -- driving a read
    where the spec says write is a thing an exerciser is for -- and the
    generator notes any mismatch in the output.
    """

    def __init__(
        self,
        parent: tk.Widget,
        transaction: Transaction,
        fill: str,
        edge: str,
        on_status: Callable[[str], None],
        on_change: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg=fill)
        self.transaction = transaction
        self._fill = fill
        self._edge = edge
        self._on_status = on_status
        self._on_change = on_change
        self.rows: list[tk.Frame] = []
        self._redraw()

    @property
    def entries(self) -> list[dict[str, Any]]:
        entries = self.transaction.params.setdefault("targets", [])
        if not isinstance(entries, list):
            entries = []
            self.transaction.params["targets"] = entries
        return entries

    def _redraw(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self.rows = []
        for index, entry in enumerate(self.entries):
            self.rows.append(self._build_row(index, entry))

        add = _Chip(
            self, text="+  add target", bg=_mix(self._fill, "#FFFFFF", 0.2),
            fg="white", font=_SMALL, padx=6, pady=2, cursor="hand2",
        )
        add.pack(anchor="w", pady=(2, 0))
        add.bind("<Button-1>", lambda _e: self._add())

    def _build_row(self, index: int, entry: dict[str, Any]) -> tk.Frame:
        row = tk.Frame(self, bg=self._fill)
        row.pack(fill=tk.X, pady=1)

        tk.Label(row, text="Addr", bg=self._fill, fg="white", font=_SMALL).pack(
            side=tk.LEFT)
        address = tk.StringVar(value=values.format_hex(entry.get("address")))
        addr_entry = tk.Entry(row, textvariable=address, width=6, font=_SMALL,
                              relief=tk.FLAT, bg="white")
        addr_entry.pack(side=tk.LEFT, padx=(2, 6))

        def set_address(*_a: object) -> None:
            try:
                entry["address"] = values.parse_int(address.get(), label="Address")
                addr_entry.configure(fg="black")
                self._on_status("")
            except values.ValueError_ as exc:
                addr_entry.configure(fg="#B00020")
                self._on_status(str(exc))
                return
            self._on_change()

        address.trace_add("write", set_address)

        direction = tk.StringVar(value=entry.get("direction", "write"))
        box = ttk.Combobox(row, textvariable=direction, values=["write", "read"],
                           state="readonly", width=6, font=_SMALL)
        box.pack(side=tk.LEFT, padx=(0, 6))

        def set_direction(*_a: object) -> None:
            if entry.get("direction") == direction.get():
                return
            entry["direction"] = direction.get()
            # The payload field differs by direction, so the rows are redrawn.
            self._redraw()
            self._on_change()

        direction.trace_add("write", set_direction)

        if entry.get("direction") == "read":
            self._payload_field(row, entry, "Count", "count", width=5)
        else:
            self._payload_field(row, entry, "Data", "data", width=16)

        # A white-outlined box on a pale fill, so it reads as a button rather
        # than as part of the block's text. The glyph is the block's own edge
        # colour, which clears 5:1 against the pale interior.
        pale = _mix(self._fill, "#FFFFFF", 0.75)
        remove = _Chip(
            row, text="−", bg=pale, fg=self._edge, font=_FONT,
            padx=7, pady=1, cursor="hand2",
            highlightbackground="#FFFFFF", highlightcolor="#FFFFFF",
            highlightthickness=1, bd=0,
        )
        remove.pack(side=tk.RIGHT, padx=(4, 2))
        remove.bind("<Button-1>", lambda _e, i=index: self._remove(i))
        return row

    def _payload_field(
        self, row: tk.Frame, entry: dict[str, Any], label: str, key: str, *, width: int,
    ) -> None:
        tk.Label(row, text=label, bg=self._fill, fg="white", font=_SMALL).pack(
            side=tk.LEFT)
        is_count = key == "count"
        text = (str(entry.get("count", 1)) if is_count
                else values.format_int_list(entry.get("data")))
        var = tk.StringVar(value=text)
        field = tk.Entry(row, textvariable=var, width=width, font=_SMALL,
                         relief=tk.FLAT, bg="white")
        field.pack(side=tk.LEFT, padx=(2, 6))

        def commit(*_a: object) -> None:
            try:
                if is_count:
                    entry["count"] = values.parse_int(var.get(), label=label)
                else:
                    entry["data"] = values.parse_int_list(var.get(), label=label)
                field.configure(fg="black")
                self._on_status("")
            except values.ValueError_ as exc:
                field.configure(fg="#B00020")
                self._on_status(str(exc))
                return
            self._on_change()

        var.trace_add("write", commit)

    def _add(self) -> None:
        ccc = str(self.transaction.params.get("ccc", ""))
        self.entries.append(default_direct_target(ccc))
        self._redraw()
        self._on_change()

    def _remove(self, index: int) -> None:
        if len(self.entries) <= 1:
            self._on_status("A Direct CCC needs at least one target.")
            return
        del self.entries[index]
        self._redraw()
        self._on_change()


class StackBlock(tk.Frame):
    """One transaction as a puzzle-piece block with inline fields."""

    def __init__(
        self,
        parent: tk.Widget,
        transaction: Transaction,
        index: int,
        *,
        selected: bool,
        callbacks: BlockCallbacks,
        on_select: Callable[[int], None],
        on_press: Callable[[int, tk.Event], None],
        on_menu: Callable[[int, tk.Event], None],
        on_motion: Callable[[tk.Event], None],
        on_release: Callable[[tk.Event], None],
    ) -> None:
        super().__init__(parent, bg=_STACK_BG, highlightthickness=2)
        self.transaction = transaction
        self.index = index
        self._callbacks = callbacks
        self._on_select = on_select
        self._press = on_press
        self._menu = on_menu
        self._motion = on_motion
        self._release = on_release
        self._selected = selected
        self._fields: dict[str, _InlineField] = {}
        self._target_rows: _TargetRows | None = None
        self._extras: tk.Frame | None = None
        info = block_info(transaction.kind, transaction.params)
        self._label_text = info.label if info else transaction.kind
        self._params = info.params if info else []
        self._fill, self._edge = _colors(transaction.kind, enabled=transaction.enabled)
        self.configure(highlightbackground=self._highlight_color())

        self._top = _Cap(self, self._fill, kind="top")
        self._top.pack(fill=tk.X)
        self._body = tk.Frame(self, bg=self._fill)
        self._body.pack(fill=tk.X)
        self._title = tk.Label(
            self._body, text=self._title_text(), bg=self._fill, fg="white",
            font=_FONT, anchor="w", cursor="fleur",
        )
        self._title.pack(fill=tk.X, padx=8, pady=(0, 2))
        self._primary = tk.Frame(self._body, bg=self._fill)
        self._primary.pack(fill=tk.X, padx=6, pady=(0, 4))
        for param in self._params:
            if param.always_emit:
                self._add_field(self._primary, param)
        self._bot = _Cap(self, self._fill, kind="bottom")
        self._bot.pack(fill=tk.X)
        if selected:
            self._show_extras()
        self._bind_select(self)

    def _title_text(self) -> str:
        mark = "" if self.transaction.enabled else "  (disabled)"
        return self._label_text + mark

    def _highlight_color(self) -> str:
        return "#FFFFFF" if self._selected else self._edge

    def _add_field(self, parent: tk.Widget, param: Param) -> None:
        if param.type == "ccc_targets":
            # A repeating editor needs the block's width, not an inline slot.
            rows = _TargetRows(
                self._body, self.transaction, self._fill, self._edge,
                self._callbacks.on_status, self._callbacks.on_change,
            )
            rows.pack(fill=tk.X, padx=6, pady=(0, 4))
            self._target_rows = rows
            return
        field = _InlineField(
            parent, param, self.transaction.params.get(param.name, param.default),
            self._fill,
            lambda v, name=param.name: self.transaction.params.__setitem__(name, v),
            self._callbacks.on_status, self._callbacks.on_change,
        )
        self._fields[param.name] = field

    def _show_extras(self) -> None:
        if self._extras is not None:
            return
        extras = tk.Frame(self._body, bg=self._fill)
        extras.pack(fill=tk.X, padx=6, pady=(0, 4))

        chrome = tk.Frame(extras, bg=self._fill)
        chrome.pack(fill=tk.X, pady=(2, 2))
        enabled_var = tk.BooleanVar(value=self.transaction.enabled)

        def set_enabled(*_a: object) -> None:
            self.transaction.enabled = bool(enabled_var.get())
            self._callbacks.refresh_tree()

        enabled_var.trace_add("write", set_enabled)
        tk.Checkbutton(
            chrome, text="Enabled", variable=enabled_var, bg=self._fill,
            activebackground=self._fill, fg="white",
            selectcolor=_mix(self._fill, "#FFFFFF", 0.25),
            highlightthickness=0, bd=0, font=_SMALL,
        ).pack(side=tk.LEFT)
        tk.Label(chrome, text="Comment", bg=self._fill, fg="white",
                 font=_SMALL).pack(side=tk.LEFT, padx=(10, 3))
        comment_var = tk.StringVar(value=self.transaction.comment)

        def set_comment(*_a: object) -> None:
            self.transaction.comment = comment_var.get()
            self._callbacks.refresh_tree()

        comment_var.trace_add("write", set_comment)
        tk.Entry(
            chrome, textvariable=comment_var, width=22, font=_SMALL,
            relief=tk.FLAT, bg="white",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        optional = [p for p in self._params if not p.always_emit]
        if optional:
            row = tk.Frame(extras, bg=self._fill)
            row.pack(fill=tk.X)
            for param in optional:
                self._add_field(row, param)
        self._extras = extras
        self._bind_select(extras)

    def _hide_extras(self) -> None:
        if self._extras is None:
            return
        self._extras.destroy()
        self._extras = None
        for param in self._params:
            if not param.always_emit:
                self._fields.pop(param.name, None)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.configure(highlightbackground=self._highlight_color())
        if selected:
            self._show_extras()
        else:
            self._hide_extras()

    def set_enabled(self, enabled: bool) -> None:
        self._fill, self._edge = _colors(self.transaction.kind, enabled=enabled)
        self.configure(highlightbackground=self._highlight_color())
        self._top.recolor(self._fill)
        self._bot.recolor(self._fill)
        self._body.configure(bg=self._fill)
        self._title.configure(bg=self._fill, text=self._title_text())
        self._primary.configure(bg=self._fill)
        self._recolor_tree(self._primary, self._fill)
        if self._extras is not None:
            self._recolor_tree(self._extras, self._fill)

    def _recolor_tree(self, widget: tk.Widget, fill: str) -> None:
        if isinstance(widget, (tk.Frame, tk.Label, tk.Checkbutton)):
            try:
                widget.configure(bg=fill)
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            if isinstance(child, (tk.Entry, ttk.Combobox, ttk.Entry)):
                continue
            self._recolor_tree(child, fill)

    def set_param_text(self, name: str, text: str) -> None:
        self._fields[name].set_text(text)

    def invoke_select(self) -> None:
        self._on_select(self.index)

    def _on_click(self, _event: object = None) -> None:
        self._on_select(self.index)

    def _on_press(self, event: tk.Event) -> None:
        self._press(self.index, event)

    def _on_menu(self, event: tk.Event) -> str:
        self._menu(self.index, event)
        return "break"

    def _bind_select(self, widget: tk.Widget) -> None:
        if isinstance(widget, _Chip):
            # It handles its own click; rebinding here would replace that.
            widget.bind("<Button-3>", self._on_menu)
            return
        if isinstance(widget, _INPUTS):
            widget.bind("<FocusIn>", self._on_click)
            widget.bind("<Button-3>", self._on_menu)
            return
        widget.bind("<Button-1>", self._on_press)
        widget.bind("<B1-Motion>", self._motion)
        widget.bind("<ButtonRelease-1>", self._release)
        widget.bind("<Button-3>", self._on_menu)
        for child in widget.winfo_children():
            self._bind_select(child)


class FrameHat(tk.Frame):
    """C-block wrapper: hat, left rail, mouth for stack blocks, closer bar."""

    def __init__(
        self,
        parent: tk.Widget,
        step: Step,
        *,
        selected: bool,
        on_select: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg=_STACK_BG, highlightthickness=2)
        self.step = step
        self._on_select = on_select
        self._selected = selected
        self._fill = _FRAME_FILL if step.enabled else _mix(_FRAME_FILL)
        self.configure(highlightbackground=self._highlight_color())

        self._hat_cap = _Cap(self, self._fill, kind="hat")
        self._hat_cap.pack(fill=tk.X)
        self._hat_body = tk.Frame(self, bg=self._fill)
        self._hat_body.pack(fill=tk.X)
        self._hat_label = tk.Label(
            self._hat_body, text="send i3c transactions",
            bg=self._fill, fg="white", font=_FONT, anchor="w",
        )
        self._hat_label.pack(fill=tk.X, padx=10, pady=(0, 6))

        mid = tk.Frame(self, bg=_STACK_BG)
        mid.pack(fill=tk.BOTH, expand=True)
        self._rail = tk.Frame(mid, bg=self._fill, width=_RAIL)
        self._rail.pack(side=tk.LEFT, fill=tk.Y)
        self._rail.pack_propagate(False)
        self.mouth = tk.Frame(mid, bg=_STACK_BG)
        self.mouth.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._closer_body = tk.Frame(self, bg=self._fill, height=10)
        self._closer_body.pack(fill=tk.X)
        self._closer_body.pack_propagate(False)
        self._closer = _Cap(self, self._fill, kind="bottom")
        self._closer.pack(fill=tk.X)
        self._bind_select(self._hat_cap)
        self._bind_select(self._hat_body)
        self._bind_select(self._rail)
        self._bind_select(self._closer_body)
        self._bind_select(self._closer)

    def _highlight_color(self) -> str:
        return "#FFFFFF" if self._selected else _FRAME_EDGE

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.configure(highlightbackground=self._highlight_color())

    def set_enabled(self, enabled: bool) -> None:
        self._fill = _FRAME_FILL if enabled else _mix(_FRAME_FILL)
        self.configure(highlightbackground=self._highlight_color())
        self._hat_cap.recolor(self._fill)
        self._hat_body.configure(bg=self._fill)
        self._hat_label.configure(bg=self._fill)
        self._rail.configure(bg=self._fill)
        self._closer_body.configure(bg=self._fill)
        self._closer.recolor(self._fill)

    def _on_click(self, _event: object = None) -> None:
        self._on_select()

    def _bind_select(self, widget: tk.Widget) -> None:
        widget.bind("<Button-1>", self._on_click)
        for child in widget.winfo_children():
            self._bind_select(child)


class _PaletteChip(tk.Frame):
    """A palette mini-block: click to add, or drag onto the stack to place it."""

    def __init__(
        self,
        parent: tk.Widget,
        key: str,
        label: str,
        fill: str,
        edge: str,
        on_press: Callable[[str, str, str, tk.Event], None],
        on_motion: Callable[[tk.Event], None],
        on_release: Callable[[tk.Event], None],
    ) -> None:
        super().__init__(parent, bg=fill, highlightbackground=edge, highlightthickness=1,
                         cursor="fleur")
        self._key = key
        text = tk.Label(
            self, text=label, bg=fill, fg="white", font=_SMALL, anchor="w",
            padx=6, pady=3, cursor="fleur",
        )
        text.pack(fill=tk.X)
        for widget in (self, text):
            widget.bind("<Button-1>", lambda e: on_press(key, label, fill, e))
            widget.bind("<B1-Motion>", on_motion)
            widget.bind("<ButtonRelease-1>", on_release)


class BlockWorkspace(ttk.Frame):
    """Palette + stacked C-block editor for one ``send_frame`` step."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        step: Step,
        step_index: int,
        selected_tx: int | None,
        controllers: list[str],
        callbacks: BlockCallbacks,
    ) -> None:
        super().__init__(parent)
        self.step = step
        self.step_index = step_index
        self.selected_tx = selected_tx
        self._ready = False
        refresh = callbacks.refresh_tree
        self.callbacks = BlockCallbacks(
            add_primitive=callbacks.add_primitive,
            merge_frame=callbacks.merge_frame,
            select_node=callbacks.select_node,
            on_change=callbacks.on_change,
            on_status=callbacks.on_status,
            refresh_tree=lambda: refresh() if self._ready else None,
            reorder=lambda src, dest: callbacks.reorder(src, dest) if self._ready else None,
            insert_at=lambda dest, key: callbacks.insert_at(dest, key) if self._ready else None,
            delete_at=callbacks.delete_at,
            duplicate_at=callbacks.duplicate_at,
            expand_at=callbacks.expand_at,
        )
        self.blocks: list[StackBlock] = []
        self.hat: FrameHat | None = None
        self._category = "Private Wr/Rd"
        self._drag: dict[str, Any] | None = None
        self._ghost: tk.Toplevel | None = None
        self._drop_bar: tk.Frame | None = None

        # Snapshot of the stack as built. Comparing the live step against itself
        # would always match, so a structural change would never force a rebuild.
        self.built_structure = structure_key(step)
        self._build_header(controllers)
        body = tk.Frame(self, bg=_STACK_BG)
        body.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._build_palette(body)
        self._build_stack(body)
        self.apply_selection(selected_tx)
        self._bind_block_keys()
        self._stack_canvas.bind(
            "<Button-3>", lambda e: self._on_block_right_click(None, e))
        self._ready = True

    def structure_key(self) -> tuple:
        """The stack as it stands now, which may differ from what was built."""
        return structure_key(self.step)

    def _build_header(self, controllers: list[str]) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Sent by", width=10).pack(side=tk.LEFT)
        target = tk.StringVar(value=self.step.target_var)
        box = ttk.Combobox(
            header, textvariable=target, values=controllers, state="readonly", width=16,
        )
        box.pack(side=tk.LEFT, padx=(0, 8))

        def set_target(*_a: object) -> None:
            self.step.target_var = target.get()
            self.callbacks.on_change()

        target.trace_add("write", set_target)

        enabled = tk.BooleanVar(value=self.step.enabled)

        def set_enabled(*_a: object) -> None:
            self.step.enabled = bool(enabled.get())
            self.callbacks.refresh_tree()

        enabled.trace_add("write", set_enabled)
        ttk.Checkbutton(header, text="Enabled", variable=enabled).pack(side=tk.LEFT)

        ttk.Label(header, text="Comment").pack(side=tk.LEFT, padx=(10, 3))
        comment = tk.StringVar(value=self.step.comment)

        def set_comment(*_a: object) -> None:
            self.step.comment = comment.get()
            self.callbacks.refresh_tree()

        comment.trace_add("write", set_comment)
        ttk.Entry(header, textvariable=comment).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_palette(self, body: tk.Frame) -> None:
        rail = tk.Frame(body, bg=_PALETTE_BG, width=230)
        rail.pack(side=tk.LEFT, fill=tk.Y)
        rail.pack_propagate(False)
        tk.Label(
            rail, text="Blocks", bg=_PALETTE_BG, font=_FONT, anchor="w",
        ).pack(fill=tk.X, padx=6, pady=(6, 2))

        cats = tk.Frame(rail, bg=_PALETTE_BG)
        cats.pack(fill=tk.X, padx=4)
        self._cat_buttons: dict[str, tk.Label] = {}
        for name, color in self._category_colors():
            label = tk.Label(
                cats, text=name, bg=color, fg="white", font=_SMALL,
                padx=4, pady=3, anchor="w",
            )
            label.pack(fill=tk.X, pady=1)
            label.bind("<Button-1>", lambda _e, n=name: self._show_category(n))
            self._cat_buttons[name] = label

        holder = tk.Frame(rail, bg=_PALETTE_BG)
        holder.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._palette_canvas = tk.Canvas(
            holder, bg=_PALETTE_BG, highlightthickness=0, borderwidth=0,
        )
        scroll = ttk.Scrollbar(holder, orient=tk.VERTICAL,
                               command=self._palette_canvas.yview)
        self._palette_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._palette_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._palette_inner = tk.Frame(self._palette_canvas, bg=_PALETTE_BG)
        self._palette_window = self._palette_canvas.create_window(
            (0, 0), window=self._palette_inner, anchor="nw",
        )
        self._palette_inner.bind(
            "<Configure>",
            lambda _e: self._palette_canvas.configure(
                scrollregion=self._palette_canvas.bbox("all")),
        )
        self._palette_canvas.bind(
            "<Configure>",
            lambda e: self._palette_canvas.itemconfigure(
                self._palette_window, width=e.width),
        )
        self._show_category(self._category)

    @staticmethod
    def _category_colors() -> list[tuple[str, str]]:
        mapping = {
            "Primitives": "#4C97FF",
            "Broadcast CCC": "#CF8B17",
            "Direct CCC": "#CF8B17",
            "Private Wr/Rd": "#4C97FF",
            "HDR-DDR": "#9966FF",
            "Patterns": "#FFAB19",
        }
        names = ["Primitives", *frame_categories()]
        return [(name, mapping.get(name, _SNIPPET_FILL)) for name in names]

    def _show_category(self, name: str) -> None:
        self._category = name
        for child in self._palette_inner.winfo_children():
            child.destroy()
        for cat, button in self._cat_buttons.items():
            button.configure(relief=tk.SOLID if cat == name else tk.FLAT,
                             bd=2 if cat == name else 0)
        if name == "Primitives":
            tk.Label(
                self._palette_inner, text="Drag onto the stack, or click to add",
                bg=_PALETTE_BG, fg="#555", font=_SMALL, anchor="w",
            ).pack(fill=tk.X, pady=(0, 4))
            for key, spec in TRANSACTIONS.items():
                fill, edge = _colors(key)
                _PaletteChip(
                    self._palette_inner, key, spec.label, fill, edge,
                    self._palette_press, self._on_drag_motion, self._on_drag_release,
                ).pack(fill=tk.X, pady=2)
            return
        tk.Label(
            self._palette_inner, text="Drag onto the stack, or click to merge (no STOP)",
            bg=_PALETTE_BG, fg="#555", font=_SMALL, anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))
        for spec in frames_in(name):
            _PaletteChip(
                self._palette_inner, spec.key, spec.label, _SNIPPET_FILL, _SNIPPET_EDGE,
                self._palette_press, self._on_drag_motion, self._on_drag_release,
            ).pack(fill=tk.X, pady=2)

    def _build_stack(self, body: tk.Frame) -> None:
        holder = tk.Frame(body, bg=_STACK_BG)
        holder.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._stack_canvas = tk.Canvas(
            holder, bg=_STACK_BG, highlightthickness=0, borderwidth=0,
        )
        scroll = ttk.Scrollbar(holder, orient=tk.VERTICAL,
                               command=self._stack_canvas.yview)
        self._stack_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._stack_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._stack_inner = tk.Frame(self._stack_canvas, bg=_STACK_BG)
        self._stack_window = self._stack_canvas.create_window(
            (0, 0), window=self._stack_inner, anchor="nw",
        )
        self._stack_inner.bind(
            "<Configure>",
            lambda _e: self._stack_canvas.configure(
                scrollregion=self._stack_canvas.bbox("all")),
        )
        self._stack_canvas.bind(
            "<Configure>",
            lambda e: self._stack_canvas.itemconfigure(
                self._stack_window, width=e.width),
        )
        self._stack_canvas.bind("<Enter>", self._bind_wheel)
        self._rebuild_blocks()

    def _bind_wheel(self, _event: object = None) -> None:
        self._stack_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._stack_canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"),
        )
        self._stack_canvas.bind("<Leave>", lambda _e: self._stack_canvas.unbind_all("<MouseWheel>"))

    def _rebuild_blocks(self) -> None:
        for child in self._stack_inner.winfo_children():
            child.destroy()
        self.blocks = []
        self.hat = FrameHat(
            self._stack_inner, self.step,
            selected=self.selected_tx is None,
            on_select=self._select_hat,
        )
        self.hat.pack(fill=tk.X, padx=8, pady=8)
        if not self.step.transactions:
            tk.Label(
                self.hat.mouth, text="(empty — click a palette block)",
                bg=_STACK_BG, fg="#666", font=_SMALL, anchor="w",
            ).pack(fill=tk.X, padx=4, pady=8)
            return
        for index, transaction in enumerate(self.step.transactions):
            block = StackBlock(
                self.hat.mouth, transaction, index,
                selected=index == self.selected_tx,
                callbacks=self.callbacks,
                on_select=self._select_block,
                on_press=self._block_press,
                on_menu=self._on_block_right_click,
                on_motion=self._on_drag_motion,
                on_release=self._on_drag_release,
            )
            block.pack(fill=tk.X, padx=2, pady=0)
            self.blocks.append(block)

    def _select_hat(self) -> None:
        self.callbacks.select_node(f"step:{self.step_index}")

    def _select_block(self, index: int) -> None:
        self.callbacks.select_node(f"tx:{self.step_index}:{index}")

    def _take_key_focus(self) -> None:
        """Put the keyboard on the stack, so Delete reaches the block keys.

        Tk sends a key press to the focused widget and its bind tags, never to
        an ancestor frame, so the <Delete> binding only fires while the stack
        canvas itself holds the focus -- and clicking a block, which is a frame
        that takes no focus, left it wherever it was.

        An inline field is bound separately, so this runs only for a click on
        the block itself, where claiming the focus is what was meant. It is
        deliberately not called from apply_selection, which also runs on every
        rebuild and would pull the focus out of a field being typed into.
        """
        self._stack_canvas.focus_set()

    def _block_press(self, index: int, event: tk.Event) -> None:
        self._select_block(index)
        self._take_key_focus()
        spec = TRANSACTIONS.get(self.step.transactions[index].kind)
        label = spec.label if spec else self.step.transactions[index].kind
        fill, _edge = _colors(self.step.transactions[index].kind)
        self._drag = {
            "kind": "stack", "index": index, "key": "", "label": label, "fill": fill,
            "x": event.x_root, "y": event.y_root, "active": False,
        }

    def _palette_press(self, key: str, label: str, fill: str, event: tk.Event) -> None:
        self._drag = {
            "kind": "palette", "index": -1, "key": key, "label": label, "fill": fill,
            "x": event.x_root, "y": event.y_root, "active": False,
        }

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        dx = event.x_root - self._drag["x"]
        dy = event.y_root - self._drag["y"]
        if not self._drag["active"]:
            if dx * dx + dy * dy < _DRAG_THRESHOLD * _DRAG_THRESHOLD:
                return
            self._drag["active"] = True
            self._show_ghost(self._drag["label"], self._drag["fill"])
            self.bind_all("<B1-Motion>", self._on_drag_motion)
            self.bind_all("<ButtonRelease-1>", self._on_drag_release)
        self._move_ghost(event.x_root, event.y_root)
        # A palette chip only lands if it is released over the stack, so the
        # line must not promise a drop that would not happen.
        if self._drag["kind"] == "palette" and not self._pointer_over_stack(
            event.x_root, event.y_root
        ):
            self._hide_drop_bar()
            return
        self._show_drop_bar(self.drop_index(event.y_root))

    def _on_drag_release(self, event: tk.Event) -> None:
        drag = self._drag
        self._clear_drag()
        if drag is None:
            return
        if not drag["active"]:
            if drag["kind"] == "palette":
                self.palette_click(drag["key"])
            return
        dest = self.drop_index(event.y_root)
        if drag["kind"] == "stack":
            self.reorder_block(drag["index"], dest)
            return
        if self._pointer_over_stack(event.x_root, event.y_root):
            self.drop_palette(drag["key"], dest)

    def drop_index(self, y_root: int) -> int:
        """Insert-before index for a pointer Y, using each block's midpoint."""
        for index, block in enumerate(self.blocks):
            try:
                mid = block.winfo_rooty() + block.winfo_height() // 2
            except tk.TclError:
                continue
            if y_root < mid:
                return index
        return len(self.blocks)

    def _pointer_over_stack(self, x_root: int, y_root: int) -> bool:
        target = self.hat.mouth if self.hat is not None else self._stack_canvas
        try:
            left, top = target.winfo_rootx(), target.winfo_rooty()
            return (left <= x_root <= left + target.winfo_width()
                    and top <= y_root <= top + target.winfo_height())
        except tk.TclError:
            return False

    def _show_drop_bar(self, dest: int) -> None:
        """Draw the insertion line where the dragged block would land."""
        if self.hat is None:
            return
        if self._drop_bar is None or not self._drop_bar.winfo_exists():
            self._drop_bar = tk.Frame(
                self.hat.mouth, bg=_DROP_LINE, height=_DROP_LINE_H,
            )
            self._drop_bar.pack_propagate(False)
        self._drop_bar.pack_forget()
        if dest < len(self.blocks):
            self._drop_bar.pack(fill=tk.X, padx=2, pady=1, before=self.blocks[dest])
        else:
            self._drop_bar.pack(fill=tk.X, padx=2, pady=1)

    def _hide_drop_bar(self) -> None:
        if self._drop_bar is not None and self._drop_bar.winfo_exists():
            self._drop_bar.pack_forget()

    def drop_bar_visible(self) -> bool:
        """Whether the insertion line is currently shown."""
        return (
            self._drop_bar is not None
            and self._drop_bar.winfo_exists()
            and bool(self._drop_bar.winfo_manager())
        )

    def _show_ghost(self, label: str, fill: str) -> None:
        ghost = tk.Toplevel(self)
        ghost.overrideredirect(True)
        ghost.attributes("-topmost", True)
        try:
            ghost.attributes("-alpha", 0.8)
        except tk.TclError:
            pass
        tk.Label(ghost, text=label, bg=fill, fg="white", font=_FONT,
                 padx=10, pady=4).pack()
        self._ghost = ghost

    def _move_ghost(self, x_root: int, y_root: int) -> None:
        if self._ghost is None:
            return
        try:
            self._ghost.geometry(f"+{x_root + 12}+{y_root + 8}")
        except tk.TclError:
            pass

    def _clear_drag(self) -> None:
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        self._hide_drop_bar()
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except tk.TclError:
                pass
            self._ghost = None
        self._drag = None

    # -- keyboard and context menu ---------------------------------------

    def _bind_block_keys(self) -> None:
        """Delete removes the selected block while the stack has focus."""
        for widget in (self, self._stack_canvas):
            widget.bind("<Delete>", self._on_delete_key)
        self._stack_canvas.configure(takefocus=True)

    def _on_delete_key(self, _event: object = None) -> str | None:
        """Remove the selected block, unless a field has the focus.

        Typing Delete inside an address or data field must edit the text, not
        throw the block away.
        """
        focus = self.focus_get()
        if isinstance(focus, _INPUTS):
            return None
        if self.selected_tx is None:
            return None
        self.delete_block(self.selected_tx)
        return "break"

    def block_menu(self, index: int | None) -> tk.Menu:
        """The menu for a stacked block, or for the stack itself."""
        menu = tk.Menu(self, tearoff=False)
        if index is not None and 0 <= index < len(self.blocks):
            transaction = self.step.transactions[index]
            if is_composite(transaction.kind):
                menu.add_command(label="Expand into primitives",
                                 command=lambda: self.callbacks.expand_at(index))
                menu.add_separator()
            menu.add_command(label="Duplicate",
                             command=lambda: self.callbacks.duplicate_at(index))
            menu.add_command(label="Delete", accelerator="Del",
                             command=lambda: self.delete_block(index))
            menu.add_separator()
            menu.add_cascade(label="Insert before",
                             menu=self._insert_menu(menu, index))
            menu.add_cascade(label="Insert after",
                             menu=self._insert_menu(menu, index + 1))
        else:
            menu.add_cascade(label="Insert", menu=self._insert_menu(menu, None))
        return menu

    def _insert_menu(self, parent: tk.Menu, dest: int | None) -> tk.Menu:
        """A palette in menu form, inserting at `dest` (None means the end)."""
        menu = tk.Menu(parent, tearoff=False)
        primitives = tk.Menu(menu, tearoff=False)
        for key, spec in TRANSACTIONS.items():
            primitives.add_command(
                label=spec.label,
                command=lambda k=key, d=dest: self._insert(k, d),
            )
        menu.add_cascade(label="Primitives", menu=primitives)
        for category in frame_categories():
            entries = frames_in(category)
            if not entries:
                continue
            sub = tk.Menu(menu, tearoff=False)
            for spec in entries:
                sub.add_command(
                    label=spec.label,
                    command=lambda k=spec.key, d=dest: self._insert(k, d),
                )
            menu.add_cascade(label=category, menu=sub)
        return menu

    def _insert(self, key: str, dest: int | None) -> None:
        if dest is None:
            self.palette_click(key)
        else:
            self.callbacks.insert_at(dest, key)

    def _on_block_right_click(self, index: int | None, event: tk.Event) -> None:
        """Select what was clicked, then offer what can be done to it."""
        if index is not None:
            self._select_block(index)
        menu = self.block_menu(index)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def delete_block(self, index: int) -> None:
        """Remove the block at `index`."""
        self.callbacks.delete_at(index)

    def reorder_block(self, src: int, dest: int) -> None:
        """Move the stacked block at ``src`` to insert-before index ``dest``."""
        self.callbacks.reorder(src, dest)

    def drop_palette(self, key: str, dest: int) -> None:
        """Place a palette block at insert-before index ``dest``."""
        self.callbacks.insert_at(dest, key)

    def apply_selection(self, selected_tx: int | None) -> None:
        """Highlight the hat or a child without destroying inline editors."""
        self.selected_tx = selected_tx
        if self.hat is not None:
            self.hat.set_selected(selected_tx is None)
            self.hat.set_enabled(self.step.enabled)
        for index, block in enumerate(self.blocks):
            block.set_selected(index == selected_tx)
            block.set_enabled(self.step.transactions[index].enabled)

    def palette_click(self, key: str) -> None:
        """Activate a palette chip (primitives add; named sends merge)."""
        if key in TRANSACTIONS:
            self.callbacks.add_primitive(key)
            return
        if key in FRAMES:
            self.callbacks.merge_frame(key)
            return
        raise KeyError(key)
