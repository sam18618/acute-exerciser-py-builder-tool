# Changelog

What changed for someone using the builder — the fields, blocks, devices and
menu entries, and the code they generate. Internal refactors and test-only work
are not listed; `git log` has those.

Each entry covers one aqpxlib version the builder was brought up to.
**Breaking** marks a change to something the builder already offered; unless an
entry says otherwise, a saved `.pxb.json` still opens and still generates the
same script.

## 2026-11-25 — aqpxlib 0.7.1 to 0.8.0

### Added

- Every target takes an **IBI payload size**: the largest IBI payload it
  reports, 0 for unlimited, and the third byte it returns to GETMRL. It only
  takes effect with BCR bit 2 set, and blank emits nothing.
- The same value is readable as target state, so a sample can check what the
  target actually reports.
- **HDR-DDR write and read** take a **command parity**, and HDR-DDR read also
  takes a **parity mode**. The default parity mode is RAW, the library's own
  zero value, so it is not emitted and existing frames generate unchanged.
- The controller offers **Prohibit Hot-Join** again, which makes it refuse
  Hot-Join requests. It was lost in the 0.7 move to the domain config, and is
  emitted only when switched on.

### Fixed

- A **direct RSTACT read** is no longer flagged as off-spec. 0.8.0 defines both
  the write and the read form, so the builder stopped warning about a frame
  that was always valid.

## 2026-10-15 — aqpxlib 0.3.0 to 0.7.1

### Added

- The controller carries **HDR pattern timing** (HDR Restart / Exit and the
  Target Reset Pattern) and **HDR-DDR timing** nodes, plus a **dynamic address
  table** for seeding DAA. Each appears in the generated config only once a
  field is filled in.
- `do_daa` takes an optional **cap on how many addresses ENTDAA assigns**.
- Every target takes a **reset pin** and a set of **reset actions** — the
  bitmask of what a Target Reset Pattern or a GPIO reset clears. Tick nothing
  and nothing is emitted, which is *not* a reset that does nothing: the library
  then clears everything except `DATA`, so wiping the target's contents stays
  opt-in.
- New **MCTP Endpoint** device: a FIFO target answering with the MCTP DCR
  (`0xCC`), with `send_mctp_packet` for a whole MCTP-over-I3C frame, and an
  **MCTP address** state read for the address its PEC is computed over.
- **Add ENTDAA** and **Add RSTDAA** on the Transactions node, each adding the
  controller step that carries it out.

### Changed

- **Breaking** — generated scripts now build device configuration from
  aqpxlib's domain classes (`PxI3CBaseControllerConfig` and friends, imported
  from `aqpxlib.i3c`) instead of the protobuf messages. 0.7.0 moved them, and
  the old form fails late and confusingly, at attach rather than at the
  constructor. Field names are identical and the structure of the generated
  code is unchanged, but a **script exported before this needs re-exporting**
  to run against 0.7 or newer. Transaction frames still use
  `I3CControllerTransactionV2` from `aqpxlib.message`, which 0.7 supports.
- **Breaking** — **ENTDAA and RSTDAA are no longer on the Broadcast CCC menu**.
  Neither is a send: address assignment is a per-target handshake, so a frame
  carrying the command byte addressed nobody. They are on the Transactions node
  instead and emit `do_daa()` / `do_rstdaa()`. A project saved while they were
  still on the menu keeps working — such a block is routed to the same call —
  but an *expanded* block holding the CCC as hand-typed bytes is still refused,
  because rewriting those bytes would discard the edit. Both stay in the CCC
  list, which mirrors the enum.
