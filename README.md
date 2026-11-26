# PX Py Builder

A GUI for writing **aqpxlib** sample scripts. You assemble an I3C test in a
form — the bus, the devices on it, the sequence of steps — and the tool writes
the Python for you.

The point is to get a runnable script without looking up the library's calls,
arguments and defaults by hand. What it produces is an ordinary `.py` file that
reads like something a person wrote, so it is a starting point to edit, not a
black box.

Currently covers **I3C**. The catalog is protocol-agnostic, so I2C, SPI and
UART can be added later without touching the GUI or the generator.

## What it does

- **Set up the bus and devices.** Pins, voltage and pull-ups, one controller
  and any mix of targets — register, memory, FIFO and MCTP endpoint — each with
  its addresses, identifiers and limits. Bus and legacy-I2C timing are editable
  down to the individual bit when a test needs a timing violation.
- **Build the sequence.** Transactions are assembled in a Scratch-style
  workspace: pick a named send (broadcast or direct CCC, private write/read,
  HDR-DDR, bus patterns) and it stacks as one block, or drop to primitives when
  a single transfer needs tuning. Target actions and bus procedures are
  ordinary steps.
- **Start from a flow.** Ready-made sequences — bus bring-up, address
  assignment, discovery, write/read-back, IBI, Hot-Join, legacy I2C, the FIFO
  and memory targets — either as a new script or appended to the current one.
- **See the code as you go.** The generated script updates on every edit, and
  only arguments that differ from the library's defaults are emitted.
- **Keep your work.** Save a project as `.pxb.json` and keep editing it, or
  export the Python with Ctrl+E. Any step or transaction can be disabled to
  leave it out of the output without deleting it.

Input is forgiving: addresses accept `0x08`, `08` or `8`, and byte lists accept
`DE AD BE EF` or `0xDE, 0xAD`.

## Requirements

| To | You need |
| --- | --- |
| **Run the UI** | Python 3.11+ with Tkinter. Nothing else — the builder is standard library only. |
| **Run a generated script** | `aqpxlib`, and the PX hardware the script talks to. |
| **Build the .exe** | PyInstaller. |
| **Run the tests** | pytest, and `aqpxlib` for the tests that check the catalog against the real library. |

Tkinter ships with the python.org Windows installer. A Linux distribution
usually splits it out, as `python3-tk`.

The development and build dependencies are pinned in `requirements.txt`:

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`aqpxlib` is not in that file, and the builder does not need it: the tool
generates code that calls the library, so you can build a script on a machine
with no PX software or hardware installed. You need it to *run* what you
export, and to run the full test suite.

## Installing aqpxlib

`aqpxlib` is published on PyPI, so it installs like anything else — into this
project's venv:

```bash
.venv\Scripts\python.exe -m pip install aqpxlib
```

It needs Python 3.11 or later. The library's own
[getting started](https://acute-technology-inc.github.io/pxlib-docs/getting-started/)
page is the reference, and recommends `uv add aqpxlib` if you use uv.

That is the whole install: the published package carries its generated protobuf
modules, so there is no code-generation step to run.

You only need it here for the tests that check the catalog against the real
library — and on whatever machine runs the scripts you export.

## Running it

Double-click **`run_builder.bat`**, or from a terminal:

```bash
run_builder.bat
```

It uses the project venv when present and falls back to Python on PATH
otherwise. If the window does not appear, run **`run_builder_debug.bat`** — the
same launch but with a console attached, so you see the traceback.

Straight from Python, if you prefer:

```bash
.venv\Scripts\python.exe -m pxbuilder
```

## Building the UI

There is no compile step to run the UI — it is Python, so `run_builder.bat`
starts it from source. "Building" means packaging it as a standalone `.exe`
for a machine with no Python at all.

From a clean checkout:

```bash
py -3.13 -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
.venv\Scripts\pyinstaller.exe PX_Py_Builder.spec --noconfirm
```

That writes **`dist\PX_Py_Builder.exe`** (~11 MB). Copy that one file anywhere
and double-click it — no Python, no venv, no install.

The recipe lives in `PX_Py_Builder.spec`: one-file, windowed (no console), with
`aqpxlib` excluded, since the builder only writes code that imports it. Add a
new `pxbuilder` module and the spec needs no change — PyInstaller follows the
imports — but a **data file** would have to be listed in `datas`.

Build on the oldest Windows you intend to support: the .exe carries the CRT it
was built against, so it runs forward but not back. The build is not
reproducible byte-for-byte, and `build/` and `dist/` are generated and not
tracked.

To check the result, run the .exe with a console attached and watch for import
errors:

```bash
cmd /c "dist\PX_Py_Builder.exe & pause"
```

## Tests

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

Beyond checking that every flow, send and operation produces parseable Python,
the suite checks the catalog against the **real library**: that every generated
call matches the actual signature, that every field and enum choice the GUI
offers exists, and that nothing deprecated is offered or emitted. That is what
catches the builder drifting away from aqpxlib after an upgrade, so run it
after installing a new version.

Those tests skip themselves when aqpxlib is absent; the GUI tests skip when
there is no display.

## Keeping up with aqpxlib

The catalog is written by hand against the library's API, so it drifts when the
library moves — and the test suite above is what catches it. Run it after
installing a new aqpxlib: a call whose arguments moved, a field or enum value
that no longer exists, or something newly deprecated fails a test rather than
turning up in a generated script.

What each new version changed for someone using the builder is recorded in
[CHANGELOG.md](CHANGELOG.md).

## Layout

| File | Role |
| --- | --- |
| `pxbuilder/catalog.py` | The devices and the direct calls they offer. |
| `pxbuilder/transactions.py` | The transaction model: primitives and frames. |
| `pxbuilder/timing.py` | Bus and legacy-I2C timing. |
| `pxbuilder/flows.py` | The ready-made sequences. |
| `pxbuilder/model.py` | The script model and its `.pxb.json` serialization. |
| `pxbuilder/codegen.py` | Model to Python source. |
| `pxbuilder/values.py` | Parsing and formatting of typed field input. |
| `pxbuilder/gui.py` | The Tkinter window. |
| `pxbuilder/block_view.py` | The send workspace (palette + stacked blocks). |

## Not covered yet

- The CTS target, and event-driven tests: an IBI or Hot-Join handler has a
  different shape from the linear step model and needs its own design.
- I2C, SPI and UART protocols.

## Issues and patches

Bug reports and questions are welcome -- open an
[issue](https://github.com/sam18618/acute-exerciser-py-builder-tool/issues), and
say which aqpxlib version you are on and what the builder generated.

Pull requests are not accepted. The catalog is written by hand against the
library's API, and the test suite is what keeps the two in step, so a change is
quicker to make here than to review. If something is wrong or missing, an issue
is the way to get it fixed.

## License

BSD 3-Clause — see [LICENSE](LICENSE).

The builder is licensed; **aqpxlib is not**. This tool references its public
API by name and generates code that calls it, but contains none of its source.
You still need aqpxlib, on whatever terms it comes to you, to run anything this
tool writes.

PX Py Builder is an independent project, not affiliated with, endorsed by or
sponsored by Acute Technology Inc. "Acute", "PX" and "aqpxlib" are that
company's names for its own products, and appear here only to say what this
tool works with.
