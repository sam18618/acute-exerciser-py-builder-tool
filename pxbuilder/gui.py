"""Tkinter front end for PX Py Builder.

Layout is a three-pane window: an outline of the script on the left, a form for
whatever is selected in the middle, and a live preview of the generated Python
on the right. Every edit re-renders the preview, so the code is the feedback.

The outline mirrors the three areas of the catalog: **Setup** (bus, controller,
targets), **Steps** (what each device sends), and **Flows** (ready-made
sequences, reachable from the toolbar and the File menu).

The outline lists Setup, bus initialisation, and Transactions. ``+ Transaction``
adds an empty frame; Private write / read and other patterns are added inside it
from the Scratch-style workspace.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from . import timing as timing_spec
from . import values
from .block_view import BlockCallbacks, BlockWorkspace, structure_key
from .catalog import (
    DAA_TABLE,
    DEVICES,
    GROUP_LABELS,
    OPERATIONS,
    Param,
    categories,
    default_params,
    device_matches,
    groups,
    is_set,
    normalise_daa_table,
    ops_in,
)
from .codegen import CodeGenError, generate, generate_with_anchors, validate
from .flows import FLOWS, missing_devices
from .model import SEND_FRAME, Device, Script, Step, Transaction
from .transactions import (
    CCC_VIA_OPERATION,
    FRAMES,
    block_info,
    composite_frame,
    composite_kind,
    expand,
    TRANSACTIONS,
    build_frame,
    frame_body,
    frame_categories,
    frames_in,
    is_composite,
)

#: Operations that live in the Transactions list even though they are not
#: frames: each is added from there, and is a whole controller procedure.
DAA_OPS = frozenset(CCC_VIA_OPERATION.values())

#: Blocks that close a frame rather than carrying a transfer. New blocks are
#: inserted before them, and a new Transaction starts with them.
#: Blocks that close a frame rather than carrying a transfer. New blocks are
#: inserted before them, and a new Transaction starts with one.
CLOSING_BLOCKS = ("stop",)

MONO = ("Consolas", 10)
SMALL = ("Segoe UI", 8)
DEBOUNCE_MS = 250



KEYWORDS = (
    "import", "from", "def", "return", "with", "as", "try", "except",
    "if", "for", "in", "None", "True", "False", "not", "is",
)


def configure_code_tags(widget: tk.Text) -> None:
    """Give a code view the shared colour scheme.

    Shared by the main preview and the standalone workspace window, so the two
    cannot drift apart.
    """
    widget.tag_configure("kw", foreground="#569cd6")
    widget.tag_configure("str", foreground="#ce9178")
    widget.tag_configure("comment", foreground="#6a9955")
    widget.tag_configure("num", foreground="#b5cea8")
    # Bands marking the code for whatever is selected. These set a background
    # only, so the syntax tags keep colouring the text.
    widget.tag_configure("selected", background="#0e3a5f")
    widget.tag_configure("selected_edge", background="#2b7fd4")


def apply_syntax(widget: tk.Text) -> None:
    """Re-tag the whole buffer."""
    for tag in ("kw", "str", "comment", "num"):
        widget.tag_remove(tag, "1.0", tk.END)
    for lineno, line in enumerate(widget.get("1.0", tk.END).splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            widget.tag_add("comment", f"{lineno}.{len(line) - len(stripped)}", f"{lineno}.end")
            continue
        _tag_line(widget, lineno, line)


def _tag_line(widget: tk.Text, lineno: int, line: str) -> None:
    col = 0
    in_string = False
    quote = ""
    token_start = 0
    while col < len(line):
        char = line[col]
        if in_string:
            if char == quote:
                widget.tag_add("str", f"{lineno}.{token_start}", f"{lineno}.{col + 1}")
                in_string = False
            col += 1
            continue
        if char in "\"'":
            in_string, quote, token_start = True, char, col
            col += 1
            continue
        if char.isalpha() or char == "_":
            start = col
            while col < len(line) and (line[col].isalnum() or line[col] == "_"):
                col += 1
            if line[start:col] in KEYWORDS:
                widget.tag_add("kw", f"{lineno}.{start}", f"{lineno}.{col}")
            continue
        if char.isdigit():
            start = col
            while col < len(line) and (line[col].isalnum() or line[col] == "."):
                col += 1
            widget.tag_add("num", f"{lineno}.{start}", f"{lineno}.{col}")
            continue
        col += 1
    if in_string:
        widget.tag_add("str", f"{lineno}.{token_start}", f"{lineno}.end")


def band(widget: tk.Text, span: tuple[int, int] | None) -> None:
    """Mark `span` as the selected lines and scroll it into view."""
    widget.tag_remove("selected", "1.0", tk.END)
    widget.tag_remove("selected_edge", "1.0", tk.END)
    if span is None:
        return
    last = int(widget.index("end-1c").split(".")[0])
    start, end = max(1, span[0]), min(span[1], last)
    if start > end:
        return
    widget.tag_add("selected", f"{start}.0", f"{end}.end")
    # A brighter first line gives the eye somewhere to land in a long block.
    widget.tag_add("selected_edge", f"{start}.0", f"{start}.end")
    # Show the end first so that, when the block is taller than the pane,
    # scrolling back to the start leaves the beginning on screen.
    widget.see(f"{end}.0")
    widget.see(f"{start}.0")



class FrameEditor(tk.Toplevel):
    """A window of its own for composing one send frame.

    The middle pane keeps its embedded workspace for quick edits; this is where
    a frame gets real room -- a full-height palette and stack on the left, the
    generated code on the right, scrolled to the frame being edited.

    It owns no state: the blocks it shows are the same ``Step.transactions`` the
    outline edits, and every change goes back through the app's callbacks.
    """

    def __init__(self, app: "App", step_index: int) -> None:
        super().__init__(app)
        self.app = app
        # Track the frame itself, not its position: removing an earlier step
        # would otherwise slide a different frame under the same index and the
        # window would silently start editing that one instead.
        self._step: Step = app.script.steps[step_index]
        self.workspace: BlockWorkspace | None = None
        self._structure: tuple | None = None

        self.geometry("1180x780")
        self.minsize(880, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<F5>", lambda _e: self.app.apply())

        # Stacked for the same reason as the main window: the block stack wants
        # the full width, and the code reads fine underneath it.
        pane = ttk.PanedWindow(self, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._pane = pane

        left = ttk.Frame(pane, height=460)
        pane.add(left, weight=3)
        self._holder = ttk.Frame(left)
        self._holder.pack(fill=tk.BOTH, expand=True)

        right = ttk.Frame(pane, height=280)
        pane.add(right, weight=2)
        code_head = ttk.Frame(right)
        code_head.pack(fill=tk.X)
        self.code_title = ttk.Label(code_head, text="Generated code",
                                     font=("Segoe UI", 10, "bold"))
        self.code_title.pack(side=tk.LEFT)
        self.apply_button = ttk.Button(code_head, text="Apply  (F5)",
                                       command=self.app.apply)
        self.apply_button.pack(side=tk.RIGHT)
        self.code = tk.Text(right, wrap=tk.NONE, font=MONO, state=tk.DISABLED,
                            background="#1e1e1e", foreground="#d4d4d4",
                            insertbackground="#d4d4d4")
        scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.code.yview)
        self.code.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.code.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        configure_code_tags(self.code)

        self.status = ttk.Label(self, text="", anchor="w", foreground="#a00")
        self.status.pack(fill=tk.X, padx=8, pady=(0, 4))

        # ttk decides the split from the panes' requested sizes, which the
        # block stack can dominate. Place it deliberately once the window has
        # a real size, giving the workspace the larger share.
        self.after(60, self._place_sash)

        self.rebuild()

    def _place_sash(self) -> None:
        """Split the workspace and the code roughly 60/40 on first show."""
        self.update_idletasks()
        height = self._pane.winfo_height()
        if height > 200:
            self._pane.sashpos(0, int(height * 0.6))

    def update_apply_state(self, pending: bool) -> None:
        """Mirror the main window's Apply state."""
        if not self.winfo_exists():
            return
        self.apply_button.configure(state=tk.NORMAL if pending else tk.DISABLED)
        self.code_title.configure(
            text="Generated code  -  not applied" if pending else "Generated code",
            foreground="#a60" if pending else "",
        )

    # -- lifecycle -------------------------------------------------------

    @property
    def step_index(self) -> int:
        """Where the frame currently sits, or -1 once it is gone."""
        for index, step in enumerate(self.app.script.steps):
            if step is self._step:
                return index
        return -1

    @property
    def step(self) -> Step | None:
        """The frame being edited, or None once it has gone."""
        return self._step if self.step_index >= 0 and self._step.is_frame else None

    def retarget(self, step_index: int) -> None:
        """Point the window at a different frame."""
        self._step = self.app.script.steps[step_index]
        self.rebuild()

    def close(self) -> None:
        self.app._frame_editor = None
        self.destroy()

    def rebuild(self) -> None:
        """Build the workspace from scratch, following a structural change."""
        step = self.step
        if step is None:
            self.close()
            return
        self.title(f"Transaction - {step.comment or 'Controller send'}")
        for child in self._holder.winfo_children():
            child.destroy()
        self.workspace = BlockWorkspace(
            self._holder,
            step=step,
            step_index=self.step_index,
            selected_tx=self.app._selected_tx,
            controllers=self.app._controller_vars(),
            callbacks=self.app._block_callbacks(),
        )
        self.workspace.pack(fill=tk.BOTH, expand=True)
        self._structure = structure_key(step)
        self.sync()

    def sync(self) -> None:
        """Follow the app: refresh the code view and the block selection."""
        step = self.step
        if step is None:
            self.close()
            return
        if structure_key(step) != self._structure:
            self.rebuild()
            return
        if self.workspace is not None:
            self.workspace.apply_selection(self.app._selected_tx)

        self.code.configure(state=tk.NORMAL)
        self.code.delete("1.0", tk.END)
        self.code.insert("1.0", self.app.code.get("1.0", "end-1c"))
        apply_syntax(self.code)
        self.code.configure(state=tk.DISABLED)
        band(self.code, self.app._anchor_for(self.app._code_target()))
        self.status.configure(text=self.app.status.cget("text"),
                              foreground=self.app.status.cget("foreground"))
        self.update_apply_state(self.app._pending)



class App(tk.Tk):
    """The builder window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("PX Py Builder - I3C")
        self.geometry("1320x940")
        self.minsize(940, 700)

        self.script: Script = FLOWS["private_loopback"].as_script()
        self.project_path: Path | None = None
        self._selection: str = ""
        self._debounce: str | None = None
        #: A confirmation to keep showing until the next problem or notice.
        #: Without this, the refresh_code() that follows an action would wipe it.
        self._notice: str = ""
        self._block_workspace: BlockWorkspace | None = None
        #: The standalone workspace window, when one is open.
        self._frame_editor: FrameEditor | None = None
        #: Block index inside the selected Transaction, or None for the frame itself.
        self._selected_tx: int | None = None
        #: outline node id -> (first line, last line) in the generated source,
        #: refreshed on every render so the preview can follow the selection.
        self._anchors: dict[str, tuple[int, int]] = {}
        #: True when the script has moved on from what the preview shows.
        self._pending: bool = False
        self._preserve_tx_selection: bool = False
        #: Insert-before index when a palette chip is dropped on the stack.
        self._drop_at: int | None = None

        self._build_menu()
        self._build_layout()
        self.refresh_all()

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        new_menu = tk.Menu(file_menu, tearoff=False)
        for key, flow in FLOWS.items():
            new_menu.add_command(label=flow.label, command=lambda k=key: self.new_from_flow(k))
        file_menu.add_cascade(label="New from flow", menu=new_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Open project...", accelerator="Ctrl+O",
                              command=self.open_project)
        file_menu.add_command(label="Save project", accelerator="Ctrl+S",
                              command=self.save_project)
        file_menu.add_command(label="Save project as...", command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export Python...", accelerator="Ctrl+E",
                              command=self.export_python)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Open Transaction workspace...", accelerator="Ctrl+W",
                              command=self.open_frame_editor)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self.bind_all("<Control-o>", lambda _e: self.open_project())
        self.bind_all("<Control-s>", lambda _e: self.save_project())
        self.bind_all("<Control-e>", lambda _e: self.export_python())
        self.bind_all("<Control-w>", lambda _e: self.open_frame_editor())
        self.bind_all("<F5>", lambda _e: self.apply())

    def _build_layout(self) -> None:
        outer = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # -- left: outline ---------------------------------------------
        left = ttk.Frame(outer, width=370)
        outer.add(left, weight=0)

        ttk.Label(left, text="Script outline", font=("Segoe UI", 9, "bold")).pack(
            anchor="w", pady=(0, 4)
        )
        # Packed before the tree so the toolbar always keeps its rows; the tree
        # then takes whatever height is left.
        btns = ttk.Frame(left)
        btns.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        grid = [
            ("+ Device", self._add_device_menu),
            ("+ Transaction", self._add_empty_frame),
            ("+ Step", self._add_step_menu),
            ("Duplicate", self._duplicate),
            ("Remove", self._remove),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
            ("Enable/disable", self._toggle),
        ]
        for i, (text, command) in enumerate(grid):
            ttk.Button(btns, text=text, width=15, command=command).grid(
                row=i // 3, column=i % 3, padx=1, pady=1, sticky="ew"
            )
        ttk.Button(btns, text="Insert flow...", command=self._insert_flow_menu).grid(
            row=2, column=2, padx=1, pady=1, sticky="ew"
        )
        for col in range(3):
            btns.columnconfigure(col, weight=1)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # The editor and the code sit one above the other: blocks and their
        # fields need width far more than the code does, and a stacked split
        # gives both the full pane rather than a third of it each.
        stack = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        outer.add(stack, weight=1)
        self._stack = stack

        # -- top: property form ----------------------------------------
        middle = ttk.Frame(stack, height=430)
        stack.add(middle, weight=3)

        self.form_title = ttk.Label(middle, text="", font=("Segoe UI", 10, "bold"))
        self.form_title.pack(anchor="w")
        self.form_help = ttk.Label(middle, text="", wraplength=700, foreground="#555")
        self.form_help.pack(anchor="w", pady=(0, 6))
        # Keep the help text wrapping to whatever width the pane actually has.
        middle.bind(
            "<Configure>",
            lambda e: self.form_help.configure(wraplength=max(280, e.width - 24)),
        )

        canvas_holder = ttk.Frame(middle)
        canvas_holder.pack(fill=tk.BOTH, expand=True)
        self._form_canvas = tk.Canvas(canvas_holder, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(canvas_holder, orient=tk.VERTICAL,
                               command=self._form_canvas.yview)
        self._form_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._form_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.form = ttk.Frame(self._form_canvas)
        self._form_window = self._form_canvas.create_window((0, 0), window=self.form,
                                                            anchor="nw")
        self.form.bind(
            "<Configure>",
            lambda _e: self._form_canvas.configure(
                scrollregion=self._form_canvas.bbox("all")),
        )
        self._form_canvas.bind("<Configure>", self._on_form_canvas_configure)
        for widget in (self._form_canvas, self.form):
            widget.bind("<Enter>", lambda _e: self._bind_form_wheel(True))
            widget.bind("<Leave>", lambda _e: self._bind_form_wheel(False))

        # -- bottom: generated code ------------------------------------
        right = ttk.Frame(stack, height=300)
        stack.add(right, weight=2)

        header = ttk.Frame(right)
        header.pack(fill=tk.X)
        self.code_title = ttk.Label(header, text="Generated code",
                                     font=("Segoe UI", 10, "bold"))
        self.code_title.pack(side=tk.LEFT)
        ttk.Button(header, text="Export .py", command=self.export_python).pack(side=tk.RIGHT)
        ttk.Button(header, text="Copy", command=self._copy_code).pack(side=tk.RIGHT, padx=4)
        self.apply_button = ttk.Button(header, text="Apply  (F5)", command=self.apply)
        self.apply_button.pack(side=tk.RIGHT, padx=4)

        self.code = tk.Text(right, wrap=tk.NONE, font=MONO, state=tk.DISABLED,
                            background="#1e1e1e", foreground="#d4d4d4",
                            insertbackground="#d4d4d4")
        code_y = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.code.yview)
        code_x = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=self.code.xview)
        self.code.configure(yscrollcommand=code_y.set, xscrollcommand=code_x.set)
        code_x.pack(side=tk.BOTTOM, fill=tk.X)
        code_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.code.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        configure_code_tags(self.code)

        self.status = ttk.Label(self, text="", anchor="w", foreground="#a00")
        self.status.pack(fill=tk.X, padx=8, pady=(0, 4))

        # ttk decides the split from the panes' requested sizes, which the block
        # stack can dominate. Place it deliberately once the window has a real
        # size, giving the editor the larger share.
        self.after(60, self._place_sash)

    def _place_sash(self) -> None:
        """Split the editor and the code roughly 55/45 on first show."""
        self.update_idletasks()
        height = self._stack.winfo_height()
        if height > 200:
            self._stack.sashpos(0, int(height * 0.55))

    # ------------------------------------------------------------------
    # Outline
    # ------------------------------------------------------------------

    def refresh_tree(self) -> None:
        """Rebuild the outline, keeping the current selection if it still exists.

        Every caller has just changed the script, so this is where the preview
        learns it is behind. Loading a whole script renders instead -- see
        :meth:`refresh_all`.
        """
        self.mark_pending()
        previous = self._current_node() or self._selection
        self.tree.delete(*self.tree.get_children())

        setup = self.tree.insert("", "end", iid="setup", text="1  Setup", open=True)
        self.tree.insert(setup, "end", iid="session", text="Session")
        self.tree.insert(setup, "end", iid="bus", text="I3C Bus")
        devices = self.tree.insert(setup, "end", iid="devices", text="Devices", open=True)
        for i, device in enumerate(self.script.devices):
            spec = DEVICES.get(device.kind)
            label = spec.label if spec else device.kind
            self.tree.insert(devices, "end", iid=f"dev:{i}",
                             text=f"{device.var}  -  {label}")
            if spec and spec.role == "controller":
                self._insert_timing_nodes(i, device)

        # init_bus() brings the bus up and addresses the targets, so bring-up is
        # one row rather than a separate "assign dynamic address" step.
        init_at = next((i for i, step in enumerate(self.script.steps)
                        if step.op == "init_bus"), None)
        if init_at is not None:
            mark = "" if self.script.steps[init_at].enabled else "   (disabled)"
            self.tree.insert("", "end", iid=f"step:{init_at}",
                             text=f"2  Initialize the bus{mark}")
        else:
            self.tree.insert("", "end", iid="bringup:init_bus",
                             text="2  Initialize the bus")

        frames = self.tree.insert("", "end", iid="transactions",
                                  text="3  Transactions", open=True)
        # Script order is kept, so a DAA row sits wherever it was added --
        # between two sends, before them, after them.
        for i, step in enumerate(self.script.steps):
            if not self._under_transactions(step):
                continue
            mark = "" if step.enabled else "   (disabled)"
            self.tree.insert(frames, "end", iid=f"step:{i}",
                             text=f"{self._step_label(step)}{mark}")

        others = [(i, step) for i, step in enumerate(self.script.steps)
                  if not self._under_transactions(step) and step.op != "init_bus"]
        if others:
            other = self.tree.insert("", "end", iid="other", text="4  Other", open=True)
            for i, step in others:
                mark = "" if step.enabled else "   (disabled)"
                self.tree.insert(other, "end", iid=f"step:{i}",
                                 text=f"{self._step_label(step)}{mark}")

        self._preserve_tx_selection = True
        if previous and self.tree.exists(previous):
            self.tree.selection_set(previous)
        else:
            self.tree.selection_set("bus")

    @staticmethod
    def _under_transactions(step: Step) -> bool:
        """Whether a step's row belongs in the Transactions list.

        Frames, and the two CCCs added from that list. ENTDAA and RSTDAA are
        each a controller call rather than a frame, so they are rows of their
        own there -- never blocks inside one, which _daa_routing refuses.
        """
        return step.is_frame or step.op in DAA_OPS

    def _insert_timing_nodes(self, index: int, device: Device) -> None:
        """Hang the controller's timing sub-tree off its device node."""
        timing = timing_spec.normalise(device.params.get("timing"))
        i2c = timing_spec.normalise_i2c(device.params.get("i2c_timing"))

        mark = "" if timing["enabled"] else "   (off)"
        node = self.tree.insert(f"dev:{index}", "end", iid=f"timing:{index}",
                                text=f"Timing{mark}", open=timing["enabled"])
        for phase in timing_spec.PHASES:
            mode = timing["phases"][phase].get("mode", "simple")
            self.tree.insert(node, "end", iid=f"phase:{index}:{phase}",
                             text=f"{timing_spec.phase_label(phase)}  ({mode})")

        i2c_mark = "" if i2c["enabled"] else "   (off)"
        self.tree.insert(f"dev:{index}", "end", iid=f"i2ctiming:{index}",
                         text=f"I2C timing{i2c_mark}")

        # The HDR blocks have no switch, so the row says whether anything is in
        # them -- that is what decides if they reach the generated script.
        for iid, label, block in (
            ("hdrpattern", "HDR pattern timing",
             timing_spec.normalise_hdr_pattern(device.params.get("hdr_pattern_timing"))),
            ("hdrddr", "HDR-DDR timing",
             timing_spec.normalise_hdr_ddr(device.params.get("hdr_ddr_timing"))),
            ("daatable", "Dynamic address table",
             normalise_daa_table(device.params.get("dyn_addr_table"))),
        ):
            mark = "" if is_set(block) else "   (unset)"
            self.tree.insert(f"dev:{index}", "end", iid=f"{iid}:{index}",
                             text=f"{label}{mark}")

    @staticmethod
    def _step_label(step: Step) -> str:
        if step.is_frame:
            return step.comment or "Transaction"
        spec = OPERATIONS.get(step.op)
        return step.comment or (spec.label if spec else step.op)

    def _on_tree_select(self, _event: object = None) -> None:
        selection = self.tree.selection()
        self._selection = selection[0] if selection else ""
        if self._preserve_tx_selection:
            self._preserve_tx_selection = False
        else:
            self._selected_tx = None
        self.build_form()

    def _on_tree_double_click(self, _event: object = None) -> None:
        """Double-clicking a Transaction opens its own workspace window."""
        index, _ = self._frame_selection(self._current_node())
        if index is not None:
            self.open_frame_editor(index)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_tree_right_click(self, event: tk.Event) -> None:
        """Open a menu of what can be done to the row under the pointer.

        The row is selected first, so the entries act on what was clicked
        rather than on whatever happened to be selected before.
        """
        menu = self._context_menu(self.tree.identify_row(event.y))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _context_menu(self, node: str) -> tk.Menu:
        """Build the menu for `node`. An empty node means the blank area.

        Selecting here rather than in the click handler keeps the menu and its
        entries in step: Remove, Duplicate and Move act on the current
        selection, so building a menu for a row that is not selected would give
        entries that act on something else.
        """
        if node and self._current_node() != node and self.tree.exists(node):
            self.tree.selection_set(node)
            self._selection = node
            self.build_form()

        menu = tk.Menu(self, tearoff=False)

        if node.startswith("dev:"):
            self._device_menu_items(menu, int(node.split(":")[1]))
        elif node.startswith("phase:"):
            self._phase_menu_items(menu, node)
        elif node.startswith("timing:"):
            self._timing_menu_items(menu, int(node.split(":")[1]), i2c=False)
        elif node.startswith("i2ctiming:"):
            self._timing_menu_items(menu, int(node.split(":")[1]), i2c=True)
        elif node.startswith("step:"):
            self._step_menu_items(menu, int(node.split(":")[1]))
        elif node == "transactions":
            self._transactions_menu_items(menu)
        elif node.startswith("bringup:"):
            self._bringup_menu_items(menu, node.split(":")[1])
        else:
            self._add_menu_items(menu)
        return menu

    def _transactions_menu_items(self, menu: tk.Menu) -> None:
        """The Transactions list, plus the two CCCs that are not transactions.

        ENTDAA and RSTDAA belong here because this is where someone goes
        looking for them, but neither is a send: each is a whole controller
        procedure, so picking one adds that step. They are deliberately absent
        from the CCC menus inside a transaction, where the command byte on its
        own would address nobody.
        """
        for ccc, op_key in CCC_VIA_OPERATION.items():
            menu.add_command(
                label=f"Add {ccc.rsplit('_', 1)[-1]}",
                command=lambda k=op_key: self._add_step(k),
            )
        menu.add_separator()
        self._add_menu_items(menu)

    def _add_menu_items(self, menu: tk.Menu) -> None:
        """The things that can be added, wherever you are."""
        devices = tk.Menu(menu, tearoff=False)
        for key, spec in DEVICES.items():
            devices.add_command(label=spec.label, command=lambda k=key: self._add_device(k))
        menu.add_cascade(label="Add device", menu=devices)
        menu.add_command(label="Add Transaction", command=self._add_empty_frame)
        menu.add_cascade(label="Add step", menu=self._step_cascade(menu))
        menu.add_separator()
        menu.add_cascade(label="Insert flow", menu=self._flow_cascade(menu))

    def _step_cascade(self, parent: tk.Menu) -> tk.Menu:
        cascade = tk.Menu(parent, tearoff=False)
        for group in groups():
            group_menu = tk.Menu(cascade, tearoff=False)
            for category in categories(group):
                sub = tk.Menu(group_menu, tearoff=False)
                for spec in ops_in(category, group):
                    sub.add_command(label=spec.label,
                                    command=lambda k=spec.key: self._add_step(k))
                group_menu.add_cascade(label=category, menu=sub)
            cascade.add_cascade(label=GROUP_LABELS[group], menu=group_menu)
        return cascade

    def _flow_cascade(self, parent: tk.Menu) -> tk.Menu:
        cascade = tk.Menu(parent, tearoff=False)
        for key, flow in FLOWS.items():
            if flow.steps():
                cascade.add_command(label=flow.label,
                                    command=lambda k=key: self._insert_flow(k))
        return cascade

    def _device_menu_items(self, menu: tk.Menu, index: int) -> None:
        device = self.script.devices[index]
        spec = DEVICES.get(device.kind)
        menu.add_command(label="Rename...", command=lambda: self._rename_device_dialog(index))
        if spec and spec.role != "controller":
            menu.add_command(label="Duplicate", command=self._duplicate)
        menu.add_command(label="Remove", command=self._remove)
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self._move(-1))
        menu.add_command(label="Move down", command=lambda: self._move(1))
        menu.add_separator()
        self._add_menu_items(menu)

    def _step_menu_items(self, menu: tk.Menu, index: int) -> None:
        step = self.script.steps[index]
        if step.is_frame:
            menu.add_command(label="Open in its own window",
                             command=lambda: self.open_frame_editor(index))
            menu.add_separator()
        menu.add_command(label="Rename..." if step.is_frame else "Edit comment...",
                         command=lambda: self._comment_dialog(index))
        menu.add_command(label="Duplicate", command=self._duplicate)
        menu.add_command(label="Remove", command=self._remove)
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self._move(-1))
        menu.add_command(label="Move down", command=lambda: self._move(1))
        menu.add_command(label="Disable" if step.enabled else "Enable",
                         command=self._toggle)
        menu.add_separator()
        self._add_menu_items(menu)

    def _bringup_menu_items(self, menu: tk.Menu, op: str) -> None:
        """The placeholder rows for bus bring-up, shown when the step is absent."""
        label = OPERATIONS[op].label
        menu.add_command(label=f"Add {label}", command=lambda: self._add_step(op))
        menu.add_separator()
        self._add_menu_items(menu)

    def _timing_menu_items(self, menu: tk.Menu, index: int, *, i2c: bool) -> None:
        block = self._i2c_timing_of(index) if i2c else self._timing_of(index)
        on = block["enabled"]
        setter = self._set_i2c_enabled if i2c else self._set_timing_enabled
        menu.add_command(label="Switch off" if on else "Switch on",
                         command=lambda: setter(block, not on))

    def _phase_menu_items(self, menu: tk.Menu, node: str) -> None:
        _, index, phase = node.split(":")
        block = self._timing_of(int(index))
        values_ = block["phases"][phase]
        for mode in ("simple", "advanced"):
            menu.add_command(
                label=f"Use {mode} timing" + ("  (current)" if values_.get("mode") == mode else ""),
                command=lambda m=mode: self._set_phase_mode(values_, m, int(index), phase),
            )

    # -- small edit dialogs ----------------------------------------------

    def _rename_device_dialog(self, index: int) -> None:
        device = self.script.devices[index]
        new_var = simpledialog.askstring(
            "Rename device", "Variable name:", initialvalue=device.var, parent=self,
        )
        if new_var:
            self._rename_device(device, new_var)
            self.build_form()

    def _comment_dialog(self, index: int) -> None:
        step = self.script.steps[index]
        text = simpledialog.askstring(
            "Comment", "Shown as a comment above the generated code:",
            initialvalue=step.comment, parent=self,
        )
        if text is not None:
            self._set_comment(step, text)
            self.build_form()

    def _current_node(self) -> str:
        """The outline node selected right now.

        Read from the widget rather than the cached value: `selection_set` fires
        `<<TreeviewSelect>>` as a virtual event, so the cache can lag behind a
        programmatic selection change.
        """
        selection = self.tree.selection()
        return selection[0] if selection else ""

    # ------------------------------------------------------------------
    # Form
    # ------------------------------------------------------------------

    def _bind_form_wheel(self, on: bool) -> None:
        """Let the wheel scroll the form while the pointer is over it."""
        if on:
            self._form_canvas.bind_all("<MouseWheel>", self._on_form_wheel)
        else:
            self._form_canvas.unbind_all("<MouseWheel>")

    def _on_form_wheel(self, event: tk.Event) -> None:
        # The block workspace scrolls itself; leave its wheel alone.
        if self._block_workspace is not None and self._block_workspace.winfo_exists():
            return
        first, last = self._form_canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return  # nothing to scroll
        self._form_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_form_canvas_configure(self, event: tk.Event) -> None:
        """Stretch the form to the pane; fill the pane when the block workspace is up."""
        self._fit_form_window(event.width, event.height)

    def _fit_form_window(self, width: int | None = None, height: int | None = None) -> None:
        if width is None:
            width = self._form_canvas.winfo_width()
        if height is None:
            height = self._form_canvas.winfo_height()
        if width > 80:
            self.form_help.configure(wraplength=max(width - 16, 200))
        workspace = self._block_workspace
        if workspace is not None and workspace.winfo_exists():
            self._form_canvas.itemconfigure(
                self._form_window, width=width, height=max(height, 200),
            )
            return
        self.form.update_idletasks()
        self._form_canvas.itemconfigure(
            self._form_window, width=width, height=max(self.form.winfo_reqheight(), 1),
        )

    def _frame_selection(self, sel: str) -> tuple[int | None, int | None]:
        """Return ``(step_index, tx_index)`` when the selection is a send."""
        if sel.startswith("tx:"):
            _, i, j = sel.split(":")
            index, tx_index = int(i), int(j)
            if 0 <= index < len(self.script.steps) and self.script.steps[index].is_frame:
                return index, tx_index
        if sel.startswith("step:"):
            index = int(sel.split(":")[1])
            if 0 <= index < len(self.script.steps) and self.script.steps[index].is_frame:
                return index, self._selected_tx
        return None, None

    def _select_workspace_node(self, node: str) -> None:
        """Keep the outline on the Transaction; remember which block is selected."""
        if node.startswith("tx:"):
            _, i, j = node.split(":")
            self._selected_tx = int(j)
            frame = f"step:{i}"
            if self._current_node() == frame and self._block_workspace:
                self._block_workspace.apply_selection(int(j))
                self._sync_code_selection()
                return
            self._preserve_tx_selection = True
            self.tree.selection_set(frame)
            return
        self._selected_tx = None
        if self._current_node() == node and self._block_workspace:
            self._block_workspace.apply_selection(None)
            self._sync_code_selection()
            return
        self.tree.selection_set(node)

    def _selected_block(self) -> tuple[int, int] | None:
        """``(step, tx)`` when a stack block inside the selected Transaction is active."""
        if self._selected_tx is None:
            return None
        node = self._current_node()
        if not node.startswith("step:"):
            return None
        index = int(node.split(":")[1])
        if not (0 <= index < len(self.script.steps) and self.script.steps[index].is_frame):
            return None
        if not 0 <= self._selected_tx < len(self.script.steps[index].transactions):
            return None
        return index, self._selected_tx

    def _reuse_block_workspace(self, step_index: int) -> bool:
        workspace = self._block_workspace
        return (
            workspace is not None
            and workspace.winfo_exists()
            and workspace.step_index == step_index
            # What it was built from, not what it reads now: the latter is the
            # same object and would always match.
            and workspace.built_structure == structure_key(self.script.steps[step_index])
        )

    def _send_block_workspace(self, step_index: int, selected_tx: int | None) -> None:
        """Replace the middle form with the Scratch-style send editor."""
        step = self.script.steps[step_index]
        self._header(
            step.comment or "Controller send",
            "An I3CControllerTransactionV2 frame. Drag a palette block onto the "
            "stack to place it, or click to add it. Drag a stacked block to "
            "reorder it. Edit the fields on the block.",
        )
        ttk.Button(
            self.form,
            text="Open in its own window",
            command=lambda i=step_index: self.open_frame_editor(i),
        ).pack(anchor="w", pady=(0, 6))
        self._block_workspace = BlockWorkspace(
            self.form,
            step=step,
            step_index=step_index,
            selected_tx=selected_tx,
            controllers=self._controller_vars(),
            callbacks=self._block_callbacks(),
        )
        self._block_workspace.pack(fill=tk.BOTH, expand=True)
        self._fit_form_window()

    def _controller_vars(self) -> list[str]:
        """Variable names of the controllers a frame may be sent by."""
        return [
            d.var for d in self.script.devices
            if DEVICES.get(d.kind) and DEVICES[d.kind].role == "controller"
        ]

    def _block_callbacks(self) -> BlockCallbacks:
        """The hooks a workspace edits through, wherever it is shown."""
        return BlockCallbacks(
            add_primitive=self._add_transaction,
            merge_frame=self._merge_send,
            select_node=self._select_workspace_node,
            on_change=self._schedule_refresh,
            on_status=lambda text: self._set_status(text),
            refresh_tree=self.refresh_tree,
            reorder=self._reorder_transaction,
            insert_at=self._insert_block_at,
            delete_at=self._delete_block_at,
            duplicate_at=self._duplicate_block_at,
            expand_at=self._expand_block_at,
        )

    def open_frame_editor(self, step_index: int | None = None) -> None:
        """Open (or raise) the standalone workspace for a frame."""
        if step_index is None:
            step_index, _ = self._frame_selection(self._current_node())
        if step_index is None:
            messagebox.showinfo(
                "No Transaction selected",
                "Select a Transaction in the outline to open its workspace.",
            )
            return
        editor = self._frame_editor
        if editor is not None and editor.winfo_exists():
            if editor.step_index != step_index:
                editor.retarget(step_index)
            editor.deiconify()
            editor.lift()
            editor.focus_set()
            return
        self._frame_editor = FrameEditor(self, step_index)

    def build_form(self) -> None:
        """Render the editor for the selection, then resize its scroll area."""
        previous = self._selection
        self._build_form_body()
        # Every path through _build_form_body replaces the widgets, and several
        # return early, so the scroll area is recomputed here rather than in
        # each of them.
        self._refresh_form_scroll(to_top=self._selection != previous)

    def _refresh_form_scroll(self, *, to_top: bool = False) -> None:
        """Match the scrollable area to what the form now holds.

        The frame's <Configure> does not reliably fire before the new children
        are laid out, so the scrollbar was left describing the previous form and
        would not scroll to the bottom of a taller one.
        """
        self.form.update_idletasks()
        self._fit_form_window()
        self._form_canvas.configure(
            scrollregion=self._form_canvas.bbox("all") or (0, 0, 0, 0))
        if to_top:
            # Switching rows should start at the top, not wherever the previous
            # form happened to be scrolled to.
            self._form_canvas.yview_moveto(0)

    def _build_form_body(self) -> None:
        """Render the editor for whatever the outline has selected."""
        sel = self._current_node()
        self._selection = sel
        frame_index, selected_tx = self._frame_selection(sel)
        if frame_index is not None:
            if self._reuse_block_workspace(frame_index) and self._block_workspace:
                self._block_workspace.apply_selection(selected_tx)
                self._sync_code_selection()
                return
            for child in self.form.winfo_children():
                child.destroy()
            self._send_block_workspace(frame_index, selected_tx)
            self._sync_code_selection()
            return

        self._block_workspace = None
        for child in self.form.winfo_children():
            child.destroy()

        if sel == "session":
            self._header("Session", "Connection to the Protocol Exerciser backend.")
            self._scalar("Port", self.script.session.port, "int",
                         lambda v: setattr(self.script.session, "port", v))
            self._checkbox("Clear existing handles on connect",
                           self.script.session.clear_handles,
                           lambda v: setattr(self.script.session, "clear_handles", v))
            self._checkbox("Log operation results", self.script.log_results,
                           lambda v: setattr(self.script, "log_results", v))
            self._checkbox("Detach every device when the script finishes",
                           self.script.detach_on_finish,
                           lambda v: setattr(self.script, "detach_on_finish", v))
            self._hint("Off leaves the bus as the script left it.")
            self._multiline("Script description", self.script.description,
                            lambda v: setattr(self.script, "description", v))

        elif sel == "bus":
            self._header("I3C Bus", "Pin assignment and electrical levels of the bus.")
            bus = self.script.bus
            for attr, label, hint in [
                ("scl", "SCL pin", ""),
                ("sda", "SDA pin", ""),
                ("voltage", "Voltage (mV)", "e.g. 3300"),
                ("la_threshold_voltage", "LA threshold (mV)", "e.g. 1600"),
                ("pullup_resistance", "Pull-up (ohm)", "0 disables the pull-up"),
                ("pulldown_resistance", "Pull-down (ohm)", ""),
            ]:
                self._scalar(label, getattr(bus, attr), "int",
                             lambda v, a=attr: setattr(bus, a, v), hint=hint)

        elif sel.startswith("phase:"):
            _, i, phase = sel.split(":")
            self._phase_form(int(i), phase)
        elif sel.startswith("timing:"):
            self._timing_form(int(sel.split(":")[1]))
        elif sel.startswith("i2ctiming:"):
            self._i2c_timing_form(int(sel.split(":")[1]))
        elif sel.startswith("hdrpattern:"):
            self._hdr_pattern_form(int(sel.split(":")[1]))
        elif sel.startswith("hdrddr:"):
            self._hdr_ddr_form(int(sel.split(":")[1]))
        elif sel.startswith("daatable:"):
            self._daa_table_form(int(sel.split(":")[1]))
        elif sel.startswith("dev:"):
            self._device_form(int(sel.split(":")[1]))
        elif sel.startswith("tx:"):
            _, i, j = sel.split(":")
            self._transaction_form(int(i), int(j))
        elif sel.startswith("step:"):
            self._step_form(int(sel.split(":")[1]))
        elif sel == "setup":
            self._header("1  Setup",
                         "The bus, and the controller and targets attached to it. "
                         "Use + Device to add one.")
        elif sel == "devices":
            self._header("Devices", "Controllers and targets on the bus.")
        elif sel == "transactions":
            self._header(
                "4  Transactions",
                "Each Transaction is an empty I3CControllerTransactionV2 frame. "
                "Select one, then add Private write / read and other patterns "
                "from the Scratch palette.",
            )
        elif sel == "other":
            self._header(
                "4  Other",
                "Steps that are not bus bring-up or a Transaction -- RSTDAA, "
                "I2C, target IBI, and the other non-V2 calls.",
            )
        elif sel == "bringup:init_bus":
            self._bringup_placeholder(
                "Initialize the bus",
                "init_bus() brings the bus up and assigns dynamic addresses. "
                "This step is not in the script yet.",
                "init_bus",
            )

        self._sync_code_selection()

    def _bringup_placeholder(self, title: str, help_text: str, op: str) -> None:
        self._header(title, help_text)
        ttk.Button(
            self.form, text=f"Add {title}",
            command=lambda: self._add_step(op),
        ).pack(anchor="w", pady=4)

    def _device_form(self, index: int) -> None:
        device = self.script.devices[index]
        spec = DEVICES.get(device.kind)
        if spec is None:
            self._header(device.var, f"Unknown device type {device.kind!r}.")
            return
        self._header(spec.label, spec.help)
        self._scalar("Variable name", device.var, "str",
                     lambda v, d=device: self._rename_device(d, v),
                     hint="Used as the Python variable and the device name.")
        for param in spec.params:
            self._param(param, device.params.get(param.name, param.default),
                        lambda v, p=param, d=device: d.params.__setitem__(p.name, v))

    def _step_form(self, index: int) -> None:
        step = self.script.steps[index]
        if step.is_frame:
            self._header(
                step.comment or "Controller send",
                "An I3CControllerTransactionV2 frame. Select a transaction below to "
                "edit it, or use + Transaction to add one.",
            )
            candidates = [d.var for d in self.script.devices
                          if DEVICES.get(d.kind) and DEVICES[d.kind].role == "controller"]
            self._combo("Sent by", step.target_var, candidates,
                        lambda v, s=step: setattr(s, "target_var", v))
            self._checkbox("Enabled", step.enabled,
                           lambda v, s=step: self._set_enabled(s, v))
            self._scalar("Comment", step.comment, "str",
                         lambda v, s=step: self._set_comment(s, v),
                         hint="Shown as a comment above the frame and in the log line.")
            ttk.Separator(self.form, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
            active = len([t for t in step.transactions if t.enabled])
            ttk.Label(self.form, text=f"{active} transaction(s) in this frame.",
                      foreground="#555").pack(anchor="w")
            return

        spec = OPERATIONS.get(step.op)
        if spec is None:
            self._header(step.op, f"Unknown operation {step.op!r}.")
            return
        self._header(spec.label, spec.help)
        candidates = [d.var for d in self.script.devices
                      if device_matches(d.kind, spec.applies_to)]
        self._combo("Run on", step.target_var, candidates,
                    lambda v, s=step: setattr(s, "target_var", v))
        self._checkbox("Enabled", step.enabled, lambda v, s=step: self._set_enabled(s, v))
        self._scalar("Comment", step.comment, "str",
                     lambda v, s=step: self._set_comment(s, v),
                     hint="Blank uses the step name.")
        if spec.params:
            ttk.Separator(self.form, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        for param in spec.params:
            self._param(param, step.params.get(param.name, param.default),
                        lambda v, p=param, s=step: s.params.__setitem__(p.name, v))

    def _transaction_form(self, step_index: int, tx_index: int) -> None:
        transaction = self.script.steps[step_index].transactions[tx_index]
        spec = TRANSACTIONS.get(transaction.kind)
        if spec is None:
            self._header(transaction.kind, f"Unknown transaction {transaction.kind!r}.")
            return
        self._header(f"{spec.label}  ->  {spec.message}", spec.help)
        self._checkbox("Enabled", transaction.enabled,
                       lambda v, t=transaction: self._set_enabled(t, v))
        self._scalar("Comment", transaction.comment, "str",
                     lambda v, t=transaction: self._set_comment(t, v),
                     hint="Emitted as a comment above this transaction.")
        ttk.Separator(self.form, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        for param in spec.params:
            self._param(param, transaction.params.get(param.name, param.default),
                        lambda v, p=param, t=transaction: t.params.__setitem__(p.name, v))

    # -- controller timing ------------------------------------------------

    def _timing_of(self, index: int) -> dict:
        """The controller's timing block, normalised and stored back."""
        device = self.script.devices[index]
        timing = timing_spec.normalise(device.params.get("timing"))
        device.params["timing"] = timing
        return timing

    def _i2c_timing_of(self, index: int) -> dict:
        device = self.script.devices[index]
        i2c = timing_spec.normalise_i2c(device.params.get("i2c_timing"))
        device.params["i2c_timing"] = i2c
        return i2c

    def _timing_form(self, index: int) -> None:
        timing = self._timing_of(index)
        self._header(
            "Controller timing",
            "Bus timing for PxI3CControllerTimingConfig. Turn it off to leave the "
            "argument out entirely and let the library defaults apply. The values "
            "here come from a working setup -- tune them for your own bus.",
        )
        self._checkbox("Send a timing config", timing["enabled"],
                       lambda v, t=timing: self._set_timing_enabled(t, v))
        ttk.Separator(self.form, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        for param in timing_spec.SCALARS:
            self._param(param, timing["scalars"].get(param.name, param.default),
                        lambda v, p=param, t=timing: t["scalars"].__setitem__(p.name, v))

    def _phase_form(self, index: int, phase: str) -> None:
        timing = self._timing_of(index)
        values_ = timing["phases"][phase]
        mode = values_.get("mode", "simple")
        self._header(
            f"{timing_spec.phase_label(phase)} timing",
            f"{timing_spec.phase_help(phase)} Emitted as "
            f"PxI3CMsgTiming({mode}=...).",
        )
        if not timing["enabled"]:
            ttk.Label(
                self.form,
                text="The timing block is off, so this phase is not emitted.",
                foreground="#a60",
            ).pack(anchor="w", pady=(0, 6))

        for param in timing_spec.phase_params_for_mode(phase, mode):
            if param.name == "mode":
                # Switching mode changes which fields exist, so redraw the form.
                self._combo(param.label, mode, list(param.choices),
                            lambda v, d=values_, i=index, ph=phase:
                                self._set_phase_mode(d, v, i, ph))
                self._hint(param.help)
                continue
            self._param(param, values_.get(param.name, param.default),
                        lambda v, p=param, d=values_: d.__setitem__(p.name, v))

    def _i2c_timing_form(self, index: int) -> None:
        i2c = self._i2c_timing_of(index)
        self._header(
            "Legacy I2C timing",
            "Emitted as i2_c_timing=PxI2CTimingConfig(...), in nanoseconds. Applies "
            "to the I2C transfers on this I3C bus.",
        )
        self._checkbox("Send an I2C timing config", i2c["enabled"],
                       lambda v, d=i2c: self._set_i2c_enabled(d, v))
        ttk.Separator(self.form, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        for param in timing_spec.I2C_TIMING:
            self._param(param, i2c.get(param.name, param.default),
                        lambda v, p=param, d=i2c: d.__setitem__(p.name, v))

    def _hdr_pattern_of(self, index: int) -> dict:
        device = self.script.devices[index]
        block = timing_spec.normalise_hdr_pattern(device.params.get("hdr_pattern_timing"))
        device.params["hdr_pattern_timing"] = block
        return block

    def _hdr_ddr_of(self, index: int) -> dict:
        device = self.script.devices[index]
        block = timing_spec.normalise_hdr_ddr(device.params.get("hdr_ddr_timing"))
        device.params["hdr_ddr_timing"] = block
        return block

    def _hdr_pattern_form(self, index: int) -> None:
        block = self._hdr_pattern_of(index)
        self._header(
            "HDR pattern timing",
            "Emitted as hdr_pattern_timing=PxI3CHdrPatternTimingConfig(...). "
            "Covers HDR Restart / Exit and the Target Reset Pattern. Every "
            "interval between SDA transitions must be at least tDIG_H, and "
            "asymmetric timing is allowed -- 35 high with 100 low, say.",
        )
        self._unset_note(block)
        for param in timing_spec.HDR_PATTERN:
            self._param(param, block.get(param.name),
                        lambda v, p=param, d=block: self._set_hdr(d, p.name, v))

    def _hdr_ddr_form(self, index: int) -> None:
        block = self._hdr_ddr_of(index)
        self._header(
            "HDR-DDR timing",
            "Emitted as hdr_ddr_timing=PxI3CHdrDdrTimingConfig(...). This is "
            "where clock stalling in HDR-DDR mode is set. Data is clocked on "
            "both edges, so the rising and falling sides are timed separately.",
        )
        self._unset_note(block)
        by_name = {p.name: p for p in timing_spec.HDR_DDR}
        for group, names in timing_spec.HDR_DDR_GROUPS:
            ttk.Separator(self.form, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 2))
            ttk.Label(self.form, text=group).pack(anchor="w")
            for name in names:
                param = by_name[name]
                self._param(param, block.get(name),
                            lambda v, p=param, d=block: self._set_hdr(d, p.name, v))

    def _daa_table_of(self, index: int) -> dict:
        device = self.script.devices[index]
        block = normalise_daa_table(device.params.get("dyn_addr_table"))
        device.params["dyn_addr_table"] = block
        return block

    def _daa_table_form(self, index: int) -> None:
        block = self._daa_table_of(index)
        self._header(
            "Dynamic address table",
            "Emitted as dyn_addr_table=PxI3CControllerDynamicAssignmentTable(...). "
            "This is what ENTDAA follows when it picks addresses: with a "
            "priority list set, each new target takes the next unused entry, "
            "and once the list runs out no further addresses are assigned.",
        )
        self._unset_note(block)
        for param in DAA_TABLE:
            self._param(param, block.get(param.name),
                        lambda v, p=param, d=block: self._set_hdr(d, p.name, v))

    def _unset_note(self, block: dict) -> None:
        """Say so while the block is empty, since there is no switch to look at."""
        if is_set(block):
            return
        ttk.Label(
            self.form,
            text=("Every field is blank, so no argument is emitted and the "
                  "device's own timing applies. Fill one in to send the block."),
            foreground="#a60",
            wraplength=360,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 6))

    def _set_hdr(self, block: dict, name: str, value: object) -> None:
        """Store one HDR field.

        Deliberately does not rebuild the outline, even though the row's
        (unset) marker is derived from this: refresh_tree re-selects, which
        fires the tree-select handler, which rebuilds the form -- destroying
        the entry being typed into. The marker catches up at the next rebuild,
        and the form says plainly whether the block is empty meanwhile.
        """
        block[name] = value
        self._schedule_refresh()

    def _set_timing_enabled(self, timing: dict, enabled: bool) -> None:
        timing["enabled"] = enabled
        self.refresh_tree()

    def _set_i2c_enabled(self, i2c: dict, enabled: bool) -> None:
        i2c["enabled"] = enabled
        self.refresh_tree()

    def _set_phase_mode(self, values_: dict, mode: str, index: int, phase: str) -> None:
        if values_.get("mode") == mode:
            return
        values_["mode"] = mode
        self.refresh_tree()
        self.tree.selection_set(f"phase:{index}:{phase}")
        self.build_form()

    # -- row builders ---------------------------------------------------

    def _header(self, title: str, help_text: str = "") -> None:
        self.form_title.configure(text=title)
        self.form_help.configure(text=help_text)

    def _hint(self, text: str) -> None:
        if text:
            ttk.Label(self.form, text=f"      {text}", foreground="#777",
                      font=SMALL).pack(anchor="w")

    def _scalar(self, label: str, value: Any, kind: str, setter, hint: str = "") -> None:
        frame = ttk.Frame(self.form)
        frame.pack(fill=tk.X, pady=3)
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value="" if value is None else str(value))
        entry = ttk.Entry(frame, textvariable=var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._hint(hint)

        def commit(*_a: object) -> None:
            text = var.get().strip()
            if kind == "int":
                try:
                    setter(values.parse_int(text, label=label))
                    entry.configure(foreground="")
                except values.ValueError_ as exc:
                    entry.configure(foreground="#c00")
                    self._set_status(str(exc))
                    return
            else:
                setter(text)
            self._set_status("")
            self._schedule_refresh()

        var.trace_add("write", commit)

    def _multiline(self, label: str, value: str, setter) -> None:
        ttk.Label(self.form, text=label, anchor="w").pack(fill=tk.X, pady=(6, 2))
        box = tk.Text(self.form, height=3, wrap=tk.WORD)
        box.insert("1.0", value)
        box.pack(fill=tk.X)

        def commit(_e: object = None) -> None:
            setter(box.get("1.0", "end-1c").strip())
            self._schedule_refresh()

        box.bind("<KeyRelease>", commit)

    def _checkbox(self, label: str, value: bool, setter) -> None:
        var = tk.BooleanVar(value=bool(value))

        def commit(*_a: object) -> None:
            setter(var.get())
            self._schedule_refresh()

        var.trace_add("write", commit)
        ttk.Checkbutton(self.form, text=label, variable=var).pack(anchor="w", pady=3)

    def _combo(self, label: str, value: str, options: list[str], setter) -> None:
        frame = ttk.Frame(self.form)
        frame.pack(fill=tk.X, pady=3)
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=value)
        ttk.Combobox(frame, textvariable=var, values=options, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        def commit(*_a: object) -> None:
            setter(var.get())
            self._schedule_refresh()

        var.trace_add("write", commit)

    def _flags(self, label: str, value: list[str] | None, options: list[str], setter) -> None:
        """A tick box per flag. Nothing ticked stores None, not an empty list.

        The difference matters: a bitmask the builder leaves out is not "no
        actions", it is whatever the library defaults to.
        """
        frame = ttk.Frame(self.form)
        frame.pack(fill=tk.X, pady=3)
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side=tk.LEFT, anchor="n")
        box = ttk.Frame(frame)
        box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        picked = set(value or ())
        vars_by_name = {name: tk.BooleanVar(value=name in picked) for name in options}

        def commit(*_a: object) -> None:
            chosen = [name for name in options if vars_by_name[name].get()]
            setter(chosen or None)
            self._schedule_refresh()

        for name in options:
            var = vars_by_name[name]
            var.trace_add("write", commit)
            ttk.Checkbutton(box, text=name, variable=var).pack(anchor="w")

    def _param(self, param: Param, value: Any, setter) -> None:
        if param.type == "bool":
            self._checkbox(param.label, bool(value), setter)
            self._hint(param.help)
            return

        if param.type == "enum_flags":
            self._flags(param.label, value, list(param.choices), setter)
            self._hint(" ".join(x for x in (values.hint(param), param.help) if x))
            return

        if param.type in ("enum", "ccc"):
            self._combo(param.label, str(value), list(param.choices), setter)
            self._hint(param.help)
            return

        frame = ttk.Frame(self.form)
        frame.pack(fill=tk.X, pady=3)
        label_text = param.label + (" *" if param.always_emit else "")
        ttk.Label(frame, text=label_text, width=24, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=values.to_text(param, value))
        entry = ttk.Entry(frame, textvariable=var, font=MONO)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._hint(" ".join(x for x in (values.hint(param), param.help) if x))

        def commit(*_a: object) -> None:
            try:
                setter(values.from_text(param, var.get()))
                entry.configure(foreground="")
                self._set_status("")
            except values.ValueError_ as exc:
                entry.configure(foreground="#c00")
                self._set_status(str(exc))
                return
            self._schedule_refresh()

        var.trace_add("write", commit)

    # ------------------------------------------------------------------
    # Editing actions
    # ------------------------------------------------------------------

    def _rename_device(self, device: Device, new_var: str) -> None:
        new_var = new_var.strip()
        if not new_var or new_var == device.var:
            return
        if not new_var.isidentifier():
            self._set_status(f"{new_var!r} is not a valid Python variable name")
            return
        old = device.var
        device.var = new_var
        for step in self.script.steps:
            if step.target_var == old:
                step.target_var = new_var
        self.refresh_tree()

    def _set_enabled(self, item: Step | Transaction, enabled: bool) -> None:
        item.enabled = enabled
        self.refresh_tree()

    def _set_comment(self, item: Step | Transaction, comment: str) -> None:
        item.comment = comment
        self.refresh_tree()

    def _post(self, menu: tk.Menu) -> None:
        """Drop a menu at the pointer, releasing the grab afterwards."""
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    # -- devices ---------------------------------------------------------

    def _add_device_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        for key, spec in DEVICES.items():
            menu.add_command(label=spec.label, command=lambda k=key: self._add_device(k))
        self._post(menu)

    def _add_device(self, kind: str) -> None:
        spec = DEVICES[kind]
        if spec.role == "controller" and any(
            DEVICES[d.kind].role == "controller" for d in self.script.devices
        ):
            messagebox.showinfo(
                "Controller already present",
                "The bus already has a controller. Only one can be active at a time.",
            )
            return
        self.script.devices.append(Device(
            kind=kind,
            var=self.script.unique_var(spec.default_var),
            params=default_params(spec.params),
        ))
        self.refresh_tree()
        self.tree.selection_set(f"dev:{len(self.script.devices) - 1}")

    # -- controller sends (V2 frames) -----------------------------------

    def _add_send_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        for category in frame_categories():
            sub = tk.Menu(menu, tearoff=False)
            for spec in frames_in(category):
                sub.add_command(label=spec.label,
                                command=lambda k=spec.key: self._add_send(k))
            menu.add_cascade(label=category, menu=sub)
        self._post(menu)

    def _controller_var(self) -> str:
        return next(
            (d.var for d in self.script.devices
             if DEVICES.get(d.kind) and DEVICES[d.kind].role == "controller"),
            "",
        )

    def _add_send(self, frame_key: str) -> None:
        spec = FRAMES[frame_key]
        controller = self._controller_var()
        if not controller:
            messagebox.showinfo(
                "No controller",
                "Add an I3C Controller before building a send frame.",
            )
            return
        if frame_body(frame_key):
            # One composite block for the send, then the frame's own STOP --
            # the same shape as dropping it into an empty Transaction.
            blocks = self._blocks_for(frame_key) + [
                Transaction(
                    kind="stop",
                    params=default_params(TRANSACTIONS["stop"].params),
                ),
            ]
        else:
            # A pattern that is nothing but a stop has no body to wrap, so it
            # goes in as the primitive it already is.
            blocks = [
                Transaction(kind=kind, params=params)
                for kind, params in build_frame(frame_key)
            ]
        self._insert_step(Step(
            op=SEND_FRAME,
            target_var=controller,
            comment=spec.label,
            transactions=blocks,
        ))

    def _blocks_for(self, frame_key: str) -> list[Transaction]:
        """The block a named send contributes to a frame.

        The 0x7E header is on, wherever the block lands. Mid-frame it is often
        redundant -- the turnaround is a repeated start straight to the target
        -- but that is a judgement about the test being written, so it is left
        as an editable field on the block rather than decided here.
        """
        spec = FRAMES[frame_key]
        return [Transaction(
            kind=composite_kind(frame_key),
            params=default_params(spec.params),
        )]

    def _add_empty_frame(self) -> None:
        """Add a Transaction shell. Patterns are added inside it from the palette."""
        controller = self._controller_var()
        if not controller:
            messagebox.showinfo(
                "No controller",
                "Add an I3C Controller before building a Transaction.",
            )
            return
        self._insert_step(Step(
            op=SEND_FRAME,
            target_var=controller,
            comment="Transaction",
            # A frame closes with a STOP, so a new one starts with it rather
            # than leaving the bus unterminated until you remember. It is an
            # ordinary block: move or delete it if the test needs that. Idle
            # Time is not included -- a gap only means something between two
            # transfers, so there is nowhere for it to sit yet.
            transactions=[
                Transaction(kind="stop",
                            params=default_params(TRANSACTIONS["stop"].params)),
            ],
        ))

    # -- direct calls ----------------------------------------------------

    def _add_step_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        for group in groups():
            group_menu = tk.Menu(menu, tearoff=False)
            for category in categories(group):
                sub = tk.Menu(group_menu, tearoff=False)
                for spec in ops_in(category, group):
                    sub.add_command(label=spec.label,
                                    command=lambda k=spec.key: self._add_step(k))
                group_menu.add_cascade(label=category, menu=sub)
            menu.add_cascade(label=GROUP_LABELS[group], menu=group_menu)
        self._post(menu)

    def _add_step(self, op: str) -> None:
        spec = OPERATIONS[op]
        default_target = next(
            (d.var for d in self.script.devices if device_matches(d.kind, spec.applies_to)),
            "",
        )
        if not default_target:
            messagebox.showinfo(
                "No suitable device",
                f"{spec.label} needs a device this script does not have yet.",
            )
            return
        self._insert_step(Step(
            op=op,
            target_var=default_target,
            params=default_params(spec.params),
        ))

    def _insert_step(self, step: Step) -> None:
        insert_at = len(self.script.steps)
        current = self._current_node()
        if current.startswith(("step:", "tx:")):
            insert_at = int(current.split(":")[1]) + 1
        self.script.steps.insert(insert_at, step)
        self.refresh_tree()
        self.tree.selection_set(f"step:{insert_at}")

    # -- transactions ----------------------------------------------------

    def _frame_index(self) -> int | None:
        """The frame step the selection is inside, if any."""
        node = self._current_node()
        if node.startswith("tx:"):
            return int(node.split(":")[1])
        if node.startswith("step:"):
            index = int(node.split(":")[1])
            return index if self.script.steps[index].is_frame else None
        return None

    def _insert_position(self, step: Step) -> int:
        """Where a new transaction should go.

        After the selected block when one is selected. Otherwise before the
        frame's trailing STOP, so what you add stays inside the frame instead of
        landing after it has already ended.
        """
        if self._drop_at is not None:
            return max(0, min(self._drop_at, len(step.transactions)))
        if self._selected_tx is not None and 0 <= self._selected_tx < len(step.transactions):
            return self._selected_tx + 1
        # Walk back over the blocks that close the frame, so what you add lands
        # inside it rather than after it has already ended.
        at = len(step.transactions)
        while at > 0 and step.transactions[at - 1].kind in CLOSING_BLOCKS:
            at -= 1
        return at

    def _ensure_trailing_stop(self, step: Step) -> None:
        """Keep a STOP among the frame's closing blocks once it has content.

        The closing run may be a STOP followed by an Idle Time, so looking only
        at the last block would append a second STOP behind the idle gap.
        """
        closing = []
        for transaction in reversed(step.transactions):
            if transaction.kind not in CLOSING_BLOCKS:
                break
            closing.append(transaction.kind)
        if "stop" in closing:
            return
        spec = TRANSACTIONS["stop"]
        step.transactions.append(Transaction(
            kind="stop",
            params=default_params(spec.params),
        ))

    def _reorder_transaction(self, src: int, dest: int) -> None:
        """Move a transaction to insert-before index ``dest`` inside the current frame."""
        index = self._frame_index()
        if index is None:
            return
        items = self.script.steps[index].transactions
        if not 0 <= src < len(items):
            return
        dest = max(0, min(dest, len(items)))
        if dest == src or dest == src + 1:
            return
        item = items.pop(src)
        if dest > src:
            dest -= 1
        items.insert(dest, item)
        self._select_added_block(index, dest)

    def _block_at(self, index: int) -> tuple[int, Transaction] | None:
        """The frame and block at `index`, when the selection is inside a frame."""
        step_index = self._frame_index()
        if step_index is None:
            return None
        transactions = self.script.steps[step_index].transactions
        if not 0 <= index < len(transactions):
            return None
        return step_index, transactions[index]

    def _delete_block_at(self, index: int) -> None:
        """Remove one block from the frame being edited."""
        found = self._block_at(index)
        if found is None:
            return
        step_index, transaction = found
        label = block_info(transaction.kind, transaction.params)
        del self.script.steps[step_index].transactions[index]
        remaining = len(self.script.steps[step_index].transactions)
        self._selected_tx = min(index, remaining - 1) if remaining else None
        self.refresh_tree()
        self.build_form()
        self._notify(f"Deleted {label.label if label else transaction.kind}.")

    def _duplicate_block_at(self, index: int) -> None:
        """Copy a block, placing the copy directly after it."""
        found = self._block_at(index)
        if found is None:
            return
        step_index, transaction = found
        self.script.steps[step_index].transactions.insert(index + 1, Transaction(
            kind=transaction.kind, params=dict(transaction.params),
            enabled=transaction.enabled, comment=transaction.comment,
        ))
        self._selected_tx = index + 1
        self.refresh_tree()
        self.build_form()

    def _expand_block_at(self, index: int) -> None:
        """Expand the block at `index`, whichever one was right-clicked."""
        self._selected_tx = index
        self._expand_block()

    def _expand_block(self) -> None:
        """Break the selected named send open into the primitives it stands for."""
        block = self._selected_block()
        if block is None:
            self._set_status("Select a block to expand.")
            return
        index, position = block
        step = self.script.steps[index]
        transaction = step.transactions[position]
        if not is_composite(transaction.kind):
            self._set_status(
                f"{block_info(transaction.kind).label} is already a primitive."
            )
            return

        pieces = expand(transaction.kind, transaction.params)
        label = block_info(transaction.kind).label
        step.transactions[position:position + 1] = [
            Transaction(kind=kind, params=params) for kind, params in pieces
        ]
        self._selected_tx = position
        self.refresh_tree()
        self.build_form()
        self._notify(f"Expanded {label} into {len(pieces)} transaction(s).")

    def _insert_block_at(self, dest: int, key: str) -> None:
        """Drop a palette primitive or named send at insert-before ``dest``."""
        self._drop_at = dest
        try:
            if key in TRANSACTIONS:
                self._add_transaction(key)
            elif key in FRAMES:
                self._merge_send(key)
        finally:
            self._drop_at = None

    def _select_added_block(self, index: int, at: int) -> None:
        self._selected_tx = at
        self._preserve_tx_selection = True
        self.refresh_tree()
        if self._current_node() != f"step:{index}":
            self._preserve_tx_selection = True
            self.tree.selection_set(f"step:{index}")

    def _idle_position(self, step: Step, at: int) -> int | None:
        """Where an Idle Time may go, given a wanted position.

        It only means something between two transfers, so it is nudged inside
        the frame rather than refused outright; a frame with nothing to sit
        between has nowhere for it at all.
        """
        transfers = [i for i, t in enumerate(step.transactions)
                     if t.kind not in ("idle_time", *CLOSING_BLOCKS)]
        if len(transfers) < 2:
            self._set_status(
                "An Idle Time goes between two transfers; this frame has "
                f"{'only one' if transfers else 'none'} so far."
            )
            return None
        return max(transfers[0] + 1, min(at, transfers[-1]))

    def _add_transaction(self, kind: str) -> None:
        index = self._frame_index()
        if index is None:
            return
        spec = TRANSACTIONS[kind]
        step = self.script.steps[index]
        at = self._insert_position(step)
        if kind == "idle_time":
            at = self._idle_position(step, at)
            if at is None:
                return
        step.transactions.insert(at, Transaction(
            kind=kind,
            params=default_params(spec.params),
        ))
        if kind != "stop":
            self._ensure_trailing_stop(step)
        self._select_added_block(index, at)

    def _merge_send(self, frame_key: str) -> None:
        """Place a named send in the frame as one block.

        The block keeps its own parameters and expands to primitives only when
        the code is generated, so the stack reads as the sequence of sends it
        is rather than as a dozen sdr_writes. Use Expand to break one open.
        """
        index = self._frame_index()
        if index is None:
            return
        spec = FRAMES[frame_key]
        step = self.script.steps[index]
        at = self._insert_position(step)
        if frame_body(frame_key):
            blocks = self._blocks_for(frame_key)
        else:
            # Nothing but a stop: there is no body to wrap, so place the real
            # transaction rather than a composite that expands to nothing.
            blocks = [Transaction(kind=kind, params=params)
                      for kind, params in build_frame(frame_key)]
        for offset, block in enumerate(blocks):
            step.transactions.insert(at + offset, block)
        self._select_added_block(index, at)
        self._notify(f"Added {spec.label}.")

    def _duplicate(self) -> None:
        block = self._selected_block()
        if block is not None:
            i, j = block
            transactions = self.script.steps[i].transactions
            original = transactions[j]
            transactions.insert(j + 1, Transaction(
                kind=original.kind, params=dict(original.params),
                enabled=original.enabled, comment=original.comment,
            ))
            self._select_added_block(i, j + 1)
            return
        node = self._current_node()
        if node.startswith("step:"):
            i = int(node.split(":")[1])
            original = self.script.steps[i]
            self.script.steps.insert(i + 1, Step(
                op=original.op, target_var=original.target_var,
                params=dict(original.params),
                transactions=[
                    Transaction(t.kind, dict(t.params), t.enabled, t.comment)
                    for t in original.transactions
                ],
                enabled=original.enabled, comment=original.comment,
            ))
            self.refresh_tree()
            self.tree.selection_set(f"step:{i + 1}")
        elif node.startswith("dev:"):
            i = int(node.split(":")[1])
            original = self.script.devices[i]
            spec = DEVICES.get(original.kind)
            if spec and spec.role == "controller":
                messagebox.showinfo("Cannot duplicate",
                                    "Only one controller can be active on the bus.")
                return
            self.script.devices.insert(i + 1, Device(
                kind=original.kind,
                var=self.script.unique_var(original.var),
                params=dict(original.params),
            ))
            self.refresh_tree()
            self.tree.selection_set(f"dev:{i + 1}")

    def _remove(self) -> None:
        block = self._selected_block()
        if block is not None:
            i, j = block
            del self.script.steps[i].transactions[j]
            self._selected_tx = None
            self._selection = f"step:{i}"
            self.refresh_tree()
            return
        node = self._current_node()
        if node.startswith("step:"):
            del self.script.steps[int(node.split(":")[1])]
        elif node.startswith("dev:"):
            device = self.script.devices.pop(int(node.split(":")[1]))
            orphaned = [s for s in self.script.steps if s.target_var == device.var]
            for step in orphaned:
                step.target_var = ""
            if orphaned:
                self._notify(
                    f"{len(orphaned)} step(s) referenced {device.var!r} "
                    "and now need a new device."
                )
        else:
            return
        self._selection = ""
        self.refresh_tree()

    def _move(self, delta: int) -> None:
        block = self._selected_block()
        if block is not None:
            i, j = block
            items = self.script.steps[i].transactions
            target = j + delta
            if not 0 <= target < len(items):
                return
            items[j], items[target] = items[target], items[j]
            self._selected_tx = target
            self._preserve_tx_selection = True
            self.refresh_tree()
            return
        node = self._current_node()
        if node.startswith("step:"):
            if not self._swap(self.script.steps, node, delta, "step"):
                return
        elif node.startswith("dev:"):
            if not self._swap(self.script.devices, node, delta, "dev"):
                return
        else:
            return
        self.refresh_tree()

    def _swap(self, items: list, node: str, delta: int, prefix: str) -> bool:
        i = int(node.split(":")[1])
        j = i + delta
        if not 0 <= j < len(items):
            return False
        items[i], items[j] = items[j], items[i]
        self._selection = f"{prefix}:{j}"
        return True

    def _toggle(self) -> None:
        block = self._selected_block()
        if block is not None:
            i, j = block
            item: Step | Transaction = self.script.steps[i].transactions[j]
        elif self._current_node().startswith("step:"):
            item = self.script.steps[int(self._current_node().split(":")[1])]
        else:
            return
        item.enabled = not item.enabled
        self.refresh_tree()
        self.build_form()

    # -- flows -----------------------------------------------------------

    def _insert_flow_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        for key, flow in FLOWS.items():
            if not flow.steps():
                continue
            menu.add_command(label=flow.label, command=lambda k=key: self._insert_flow(k))
        self._post(menu)

    def _insert_flow(self, key: str) -> None:
        """Append a flow's steps, adding any device it needs but the script lacks."""
        flow = FLOWS[key]
        added = missing_devices(self.script, flow)
        self.script.devices.extend(added)

        # Point the flow's steps at the devices actually on this bus.
        by_kind: dict[str, str] = {}
        for device in self.script.devices:
            by_kind.setdefault(device.kind, device.var)
        remap = {d.var: by_kind.get(d.kind, d.var) for d in flow.devices}

        for step in flow.steps():
            step.target_var = remap.get(step.target_var, step.target_var)
            self.script.steps.append(step)

        self.refresh_tree()
        note = f" (added {len(added)} device(s))" if added else ""
        self._notify(f"Inserted flow: {flow.label}{note}")

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        """Note that the script has changed; the preview waits for Apply.

        Edits do not re-render on their own: a half-typed address or a block
        mid-drag would otherwise flicker through the preview, and a long frame
        would regenerate on every keystroke. Apply is the one place the code is
        rebuilt.
        """
        self.mark_pending()

    def mark_pending(self) -> None:
        """Mark the preview as behind the script, and offer Apply."""
        self._pending = True
        self._update_apply_state()

    def _update_apply_state(self) -> None:
        """Reflect whether there is anything to apply."""
        if not hasattr(self, "apply_button"):
            return
        self.apply_button.configure(
            state=tk.NORMAL if self._pending else tk.DISABLED)
        self.code_title.configure(
            text="Generated code  -  not applied" if self._pending
            else "Generated code",
            foreground="#a60" if self._pending else "",
        )
        if self._frame_editor is not None and self._frame_editor.winfo_exists():
            self._frame_editor.update_apply_state(self._pending)

    def apply(self) -> None:
        """Rebuild the preview from the script as it now stands."""
        self._pending = False
        self.refresh_code()

    def refresh_code(self) -> None:
        self._debounce = None
        self._pending = False
        problems = validate(self.script)
        try:
            source, self._anchors = generate_with_anchors(self.script)
        except CodeGenError as exc:
            self._anchors = {}
            source = "# Cannot generate yet:\n#\n" + "".join(
                f"#   {line}\n" for line in str(exc).splitlines()
            )
        self.code.configure(state=tk.NORMAL)
        self.code.delete("1.0", tk.END)
        self.code.insert("1.0", source)
        self._highlight()
        self.code.configure(state=tk.DISABLED)
        self._update_apply_state()
        self._sync_code_selection()
        if problems:
            self._notice = ""
            self._set_status("; ".join(problems))
        else:
            self._set_status(self._notice, problem=False)

    def _code_target(self) -> str:
        """Which outline node the preview should be showing.

        A block selected inside a Transaction wins over the Transaction itself,
        so clicking a block in the workspace jumps to just that transaction.
        """
        # Follow the node the form is actually showing. self._selection is what
        # build_form() rendered; the tree widget can disagree with it while a
        # programmatic selection change is still being delivered.
        node = self._selection or self._current_node()
        if node.startswith("step:") and self._selected_tx is not None:
            index = int(node.split(":")[1])
            step = self.script.steps[index] if index < len(self.script.steps) else None
            if step is not None and step.is_frame and (
                0 <= self._selected_tx < len(step.transactions)
            ):
                return f"tx:{index}:{self._selected_tx}"
        return node

    def _anchor_for(self, node: str) -> tuple[int, int] | None:
        """The line range for `node`, widening to its children where it has none.

        Container rows -- Setup, Devices, Transactions -- carry no code of their
        own, so they show everything nested under them. Reading the children
        from the tree keeps this working when the outline gains a new grouping.
        """
        if node in self._anchors:
            return self._anchors[node]
        ranges = [
            span for child in self._descendants(node)
            if (span := self._anchors.get(child)) is not None
        ]
        if ranges:
            return min(r[0] for r in ranges), max(r[1] for r in ranges)
        # Nothing of its own and nothing below: a timing phase with the timing
        # block switched off, say. Show the parent so the view still lands
        # somewhere relevant rather than not moving at all.
        parent = self.tree.parent(node) if self.tree.exists(node) else ""
        return self._anchor_for(parent) if parent else None

    def _descendants(self, node: str) -> list[str]:
        """Every node under `node` in the outline, at any depth."""
        if not node or not self.tree.exists(node):
            return []
        found: list[str] = []
        for child in self.tree.get_children(node):
            found.append(child)
            found += self._descendants(child)
        return found

    def _sync_code_selection(self) -> None:
        """Scroll the preview to the selected node and band its lines."""
        band(self.code, self._anchor_for(self._code_target()))
        if self._frame_editor is not None:
            self._frame_editor.sync()

    def _highlight(self) -> None:
        """Tag keywords, strings, numbers and comments in the preview."""
        apply_syntax(self.code)

    def _set_status(self, text: str, *, problem: bool = True) -> None:
        """Show a transient message. Problems are red, confirmations are not."""
        self.status.configure(text=text, foreground="#a00" if problem and text else "#060")

    def _notify(self, text: str) -> None:
        """Show a confirmation that survives the next preview refresh."""
        self._notice = text
        self._set_status(text, problem=False)

    def _copy_code(self) -> None:
        try:
            source = generate(self.script)
        except CodeGenError as exc:
            messagebox.showerror("Cannot generate", str(exc))
            return
        self.clipboard_clear()
        self.clipboard_append(source)
        self._notify("Copied the generated code to the clipboard.")

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        """Show a script from scratch. Nothing is pending -- it is all new."""
        self.refresh_tree()
        self.build_form()
        self.refresh_code()

    def _close_frame_editor(self) -> None:
        """Drop the workspace window; whatever it was editing is gone."""
        if self._frame_editor is not None and self._frame_editor.winfo_exists():
            self._frame_editor.destroy()
        self._frame_editor = None

    def new_from_flow(self, key: str) -> None:
        self._close_frame_editor()
        self.script = FLOWS[key].as_script()
        self.project_path = None
        self._selection = ""
        self._notice = ""
        self.title("PX Py Builder - I3C")
        self.refresh_all()

    def open_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("PX Builder project", "*.pxb.json"), ("JSON", "*.json"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.script = Script.load(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open project", str(exc))
            return
        self.project_path = Path(path)
        self._selection = ""
        self.title(f"PX Py Builder - {self.project_path.name}")
        self.refresh_all()

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        try:
            self.script.save(self.project_path)
        except OSError as exc:
            messagebox.showerror("Could not save project", str(exc))
            return
        self._notify(f"Saved {self.project_path}")

    def save_project_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save project as",
            defaultextension=".pxb.json",
            initialfile=f"{self.script.name}.pxb.json",
            filetypes=[("PX Builder project", "*.pxb.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.project_path = Path(path)
        self.title(f"PX Py Builder - {self.project_path.name}")
        self.save_project()

    def export_python(self) -> None:
        try:
            source = generate(self.script)
        except CodeGenError as exc:
            messagebox.showerror("Cannot generate", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="Export Python",
            defaultextension=".py",
            initialfile=f"{self.script.name}.py",
            filetypes=[("Python", "*.py"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(source, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Could not export", str(exc))
            return
        self._notify(f"Exported {path}")

    def _about(self) -> None:
        messagebox.showinfo(
            "PX Py Builder",
            "Generates aqpxlib I3C sample scripts.\n\n"
            "1  Setup -- the bus, controller and targets.\n"
            "2  Power the bus up / 3  Assign Dynamic Address -- bus bring-up.\n"
            "4  Transactions -- empty frames; add Private write / read and other "
            "patterns from the Scratch palette.\n"
            "Flows -- ready-made sequences, from File > New from flow or the "
            "Insert flow button.",
        )


def main() -> None:
    """Launch the builder."""
    App().mainloop()


if __name__ == "__main__":
    main()
