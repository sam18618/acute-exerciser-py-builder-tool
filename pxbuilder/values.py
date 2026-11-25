"""Convert between what a user types in the GUI and what the model stores.

Each parameter type gets a `parse_*` (text -> value) and a `format_*`
(value -> text). Parsing is deliberately forgiving: engineers type addresses as
``0x08``, ``08`` or ``8`` interchangeably, and byte lists with spaces, commas or
both.
"""

from __future__ import annotations

from typing import Any

from .catalog import Param


class ValueError_(ValueError):
    """Raised when typed text cannot be turned into a value."""


def parse_int(text: str, *, label: str = "value") -> int:
    """Parse a decimal, hex (0x..), binary (0b..) or octal (0o..) integer."""
    cleaned = text.strip().replace("_", "")
    if not cleaned:
        msg = f"{label} is empty"
        raise ValueError_(msg)
    try:
        # base=0 honours the 0x / 0b / 0o prefixes and rejects stray characters.
        return int(cleaned, 0)
    except ValueError:
        pass
    try:
        # Bare hex digits are common for addresses and CCC codes.
        return int(cleaned, 16)
    except ValueError:
        msg = f"{label}: {text!r} is not a number"
        raise ValueError_(msg) from None


def parse_int_list(text: str, *, label: str = "list") -> list[int | str]:
    """Parse ``DE AD BE EF``, ``0xDE, 0xAD`` or ``[0xDE 0xAD]`` into ints.

    A token that is a CCC constant name (``I3C_CCC_...``) is kept as a string, so
    a frame can carry ``bytes([PxI3CCommonCommandCode.I3C_CCC_DIRECT_GETBCR])``
    rather than a bare number.
    """
    cleaned = text.strip().strip("[]()")
    if not cleaned:
        return []
    tokens = [t for t in cleaned.replace(",", " ").split() if t]
    return [t if t.startswith("I3C_CCC_") else parse_int(t, label=label) for t in tokens]


def parse_addr_pairs(text: str, *, label: str = "pairs") -> list[list[int]]:
    """Parse ``08>09, 0A>0B`` into ``[[8, 9], [10, 11]]``."""
    cleaned = text.strip()
    if not cleaned:
        return []
    pairs: list[list[int]] = []
    for chunk in cleaned.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        for sep in ("->", ">", ":"):
            if sep in chunk:
                left, right = chunk.split(sep, 1)
                break
        else:
            msg = f"{label}: {chunk!r} should look like 08>09"
            raise ValueError_(msg)
        pairs.append([parse_int(left, label=label), parse_int(right, label=label)])
    return pairs


def parse_pairs(text: str, *, label: str = "pairs") -> list[list[Any]]:
    """Parse ``08: DE AD; 0A: BE EF`` into ``[[8, [222, 173]], [10, [190, 239]]]``."""
    cleaned = text.strip()
    if not cleaned:
        return []
    entries: list[list[Any]] = []
    for chunk in cleaned.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            msg = f"{label}: {chunk!r} should look like 08: DE AD"
            raise ValueError_(msg)
        addr, data = chunk.split(":", 1)
        entries.append([parse_int(addr, label=label), parse_int_list(data, label=label)])
    return entries


def parse_flags(text: str, *, choices: tuple[str, ...], label: str = "flags") -> list[str]:
    """Parse ``MWL | DATA`` (or commas, or spaces) into a list of flag names.

    Order follows `choices` rather than the typing, so the rendered bitmask
    reads the same however it was entered.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    tokens = [t for t in cleaned.replace("|", " ").replace(",", " ").split() if t]
    picked = set()
    for token in tokens:
        name = token.upper()
        if name not in choices:
            msg = f"{label}: {token!r} is not one of {', '.join(choices)}"
            raise ValueError_(msg)
        picked.add(name)
    return [c for c in choices if c in picked]


def format_flags(value: list[str] | None) -> str:
    """Render flag names the way they appear in the generated bitmask."""
    if not value:
        return ""
    return " | ".join(value)


def format_int(value: int | None) -> str:
    """Render an int for an entry box."""
    return "" if value is None else str(value)


def format_hex(value: int | None) -> str:
    """Render an int as 0x.., padded to an even number of digits."""
    if value is None:
        return ""
    width = 2
    magnitude = abs(int(value))
    while magnitude >= 1 << (width * 4):
        width += 2
    return f"0x{int(value):0{width}X}"


def format_int_list(value: list[int | str] | None) -> str:
    """Render a list of bytes as space-separated hex, keeping CCC names intact."""
    if not value:
        return ""
    return " ".join(v if isinstance(v, str) else format_hex(v)[2:] for v in value)


def format_addr_pairs(value: list[list[int]] | None) -> str:
    """Render address pairs as ``08>09, 0A>0B``."""
    if not value:
        return ""
    return ", ".join(f"{format_hex(a)[2:]}>{format_hex(b)[2:]}" for a, b in value)


def format_pairs(value: list[list[Any]] | None) -> str:
    """Render per-target data as ``08: DE AD; 0A: BE EF``."""
    if not value:
        return ""
    return "; ".join(
        f"{format_hex(addr)[2:]}: {format_int_list(data)}" for addr, data in value
    )


def format_ccc_targets(value: list[dict[str, Any]] | None) -> str:
    """Render the repeated-start entries as ``08:r4, 09:w AA BB``."""
    if not value:
        return ""
    parts = []
    for entry in value:
        address = format_hex(entry.get("address"))[2:]
        if entry.get("direction") == "read":
            parts.append(f"{address}:r{int(entry.get('count', 1))}")
        else:
            payload = format_int_list(entry.get("data"))
            parts.append(f"{address}:w {payload}".rstrip())
    return ", ".join(parts)


def parse_ccc_targets(text: str, *, label: str = "targets") -> list[dict[str, Any]]:
    """Parse ``08:r4, 09:w AA BB`` back into entries."""
    cleaned = text.strip()
    if not cleaned:
        return []
    entries: list[dict[str, Any]] = []
    for chunk in cleaned.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            msg = f"{label}: {chunk!r} should look like 08:r4 or 09:w AA BB"
            raise ValueError_(msg)
        address, rest = chunk.split(":", 1)
        rest = rest.strip()
        if not rest or rest[0] not in "rw":
            msg = f"{label}: {chunk!r} needs r (read) or w (write) after the colon"
            raise ValueError_(msg)
        entry: dict[str, Any] = {
            "address": parse_int(address, label=label),
            "direction": "read" if rest[0] == "r" else "write",
            "count": 1,
            "data": [],
        }
        payload = rest[1:].strip()
        if entry["direction"] == "read":
            entry["count"] = parse_int(payload or "1", label=label)
        else:
            entry["data"] = parse_int_list(payload, label=label)
        entries.append(entry)
    return entries


def to_text(param: Param, value: Any) -> str:
    """Render `value` the way it should appear in the widget for `param`."""
    if param.type == "hex":
        return format_hex(value)
    if param.type == "int":
        return format_int(value)
    if param.type in ("bytes", "bytes_literal", "int_list"):
        return format_int_list(value)
    if param.type == "addr_pairs":
        return format_addr_pairs(value)
    if param.type in ("pairs", "byte_pairs"):
        return format_pairs(value)
    if param.type == "ccc_targets":
        return format_ccc_targets(value)
    if param.type == "enum_flags":
        return format_flags(value)
    return "" if value is None else str(value)


def from_text(param: Param, text: str) -> Any:
    """Turn widget text into a stored value, or raise :class:`ValueError_`."""
    stripped = text.strip()
    if param.type in ("int", "hex"):
        if not stripped:
            if param.always_emit:
                msg = f"{param.label} is required"
                raise ValueError_(msg)
            return None
        return parse_int(stripped, label=param.label)
    if param.type in ("bytes", "bytes_literal", "int_list"):
        return parse_int_list(stripped, label=param.label)
    if param.type == "addr_pairs":
        return parse_addr_pairs(stripped, label=param.label)
    if param.type in ("pairs", "byte_pairs"):
        return parse_pairs(stripped, label=param.label) or None
    if param.type == "ccc_targets":
        return parse_ccc_targets(stripped, label=param.label)
    if param.type == "enum_flags":
        return parse_flags(stripped, choices=param.choices, label=param.label) or None
    if param.type == "str":
        return stripped
    msg = f"{param.label}: type {param.type!r} is not editable as text"
    raise ValueError_(msg)


def hint(param: Param) -> str:
    """A short example of the accepted input format, for the widget tooltip."""
    return {
        "hex": "e.g. 0x08 or 08",
        "int": "decimal, blank = default" if not param.always_emit else "decimal",
        "bytes": "e.g. DE AD BE EF",
        "bytes_literal": "e.g. DE AD, or a CCC name",
        "int_list": "e.g. 08 09 0A",
        "addr_pairs": "e.g. 08>09, 0A>0B",
        "pairs": "e.g. 08: DE AD; 0A: BE EF",
        "byte_pairs": "defining byte: value, e.g. 00: 12 34; 01: AB",
        "ccc_targets": "e.g. 08:r4, 09:w AA BB",
        "enum_flags": "tick none to use the library default",
    }.get(param.type, "")
