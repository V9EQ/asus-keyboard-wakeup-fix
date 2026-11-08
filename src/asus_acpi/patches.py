# SPDX-License-Identifier: MIT
"""Conservative ASL transforms; never invent devices or thermal limits."""

import re

from . import require


def masked(text):
    return re.sub(
        r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
        lambda m: "".join("\n" if c == "\n" else " " for c in m[0]),
        text,
        flags=re.S,
    )


def block_end(text, start):
    code = masked(text)
    pos = code.index("{", start) + 1
    depth = 1
    while depth and pos < len(code):
        depth += (code[pos] == "{") - (code[pos] == "}")
        pos += 1
    require(depth == 0, "Unbalanced ASL block")
    return pos


def unique_replace(text, old, new):
    require(text.count(old) == 1, "Unexpected/ambiguous ASL layout")
    return text.replace(old, new, 1)


def revision(text, oem, table, old, new):
    header = f'DefinitionBlock ("", "SSDT", 2, "{oem}", "{table}", 0x{old:08X})'
    return unique_replace(text, header, header.replace(f"0x{old:08X}", f"0x{new:08X}"))


def indent_block(text, amount):
    return "\n".join(" " * amount + line if line else line for line in text.split("\n"))


def patch_usb(source):
    pattern = re.compile(
        r"(?m)^(?P<indent> +)Method \((?P<name>_UPC|_PLD), 0, NotSerialized\)[^\n]*\n"
    )
    code = masked(source)
    edits, declarations, seen = [], [], set()
    for match in pattern.finditer(source):
        scopes = list(
            re.finditer(
                r"Scope \(\\_SB\.PC00\.XHCI\.RHUB\.((?:HS|SS)\d\d)\)", source[: match.start()]
            )
        )
        require(bool(scopes), "USB method outside supported port scope")
        port, method = scopes[-1][1], match["name"]
        seen.add((port, method))
        end = block_end(code, match.end())
        old = source[match.start() : end]
        path = f"\\_SB.PC00.XHCI.RHUB.{port}.{method}"
        indent = match["indent"]
        declarations.append(f"    External ({path}, MethodObj)\n")
        new = f"{indent}If (!CondRefOf ({path}))\n{indent}{{\n{indent_block(old, 4)}\n{indent}}}"
        edits.append((match.start(), end, old, new))
    expected = {
        (f"{kind}{i:02}", method)
        for kind, count in [("HS", 14), ("SS", 10)]
        for i in range(1, count + 1)
        for method in ["_UPC", "_PLD"]
    }
    require(seen == expected and len(edits) == 48, "Expected exactly 48 USB methods")
    result = source
    for start, end, old, new in reversed(edits):
        require(result[start:end] == old, "Overlapping USB edits")
        result = result[:start] + new + result[end:]
    externals = "".join(declarations)
    header = 'DefinitionBlock ("", "SSDT", 2, "INTEL", "xh_rplsb", 0x00000000)\n{\n'
    result = unique_replace(result, header, header + externals)
    # Round-trip invariant: every original method body/other source byte survives.
    restored = result.replace(externals, "", 1)
    for _, _, old, new in edits:
        restored = restored.replace(new, old, 1)
    require(restored == source, "USB transform changed unrelated source")
    return revision(result, "INTEL", "xh_rplsb", 0, 1)


def resolve_i2c(source):
    guessed = "External (PSON, MethodObj)    // Warning: Unknown method, guessing 1 arguments"
    # The tested DSDT declares root PSON as a RegionField. This corrects the
    # disassembler's declaration, not the firmware's field or executable code.
    if guessed in source:
        return unique_replace(
            source,
            guessed,
            "External (PSON, FieldUnitObj)    // Verified RegionField in original DSDT",
        )
    require("External (PSON, FieldUnitObj)" in source, "Unexpected PSON external declaration")
    return source


def patch_i2c(source):
    result, replacements = source, []
    for number in [1, 3]:
        parent = f"Scope (\\_SB.PC00.I2C{number})"
        require(result.count(parent) == 1, f"Ambiguous I2C{number} parent")
        parent_start = result.index(parent)
        parent_end = block_end(result, parent_start)
        matches = list(re.finditer(r"(?m)^( +)Scope \(TPL1\)", result[parent_start:parent_end]))
        require(len(matches) == 1, f"Unexpected I2C{number}.TPL1 scope")
        match = matches[0]
        start = parent_start + match.start()
        end = block_end(result, start)
        old = result[start:end]
        indent = match[1]
        path = f"\\_SB.PC00.I2C{number}.TPL1"
        new = f"{indent}If (CondRefOf ({path}))\n{indent}{{\n{indent_block(old, 4)}\n{indent}}}"
        # The same body may occur in both parents. Replace the already verified
        # span inside this unique parent, not a global text match.
        result = result[:start] + new + result[end:]
        replacements.append((old, new))
    restored = result
    for old, new in replacements:
        restored = unique_replace(restored, new, old)
    require(restored == source, "I2C transform changed unrelated source")
    return revision(result, "_ASUS_", "RplSBTbt", 0x1000, 0x1001)


def thermal_blocks(source):
    matches = list(re.finditer(r"(?m)^( +)Name \(PSVT, Package \(0x04\)", source))
    require(len(matches) == 1, "Expected one four-element PSVT package")
    match = matches[0]
    start, end = match.start(), block_end(source, match.start())
    require(source[end : end + 1] == ")", "Unexpected PSVT closure")
    old = source[start : end + 1]
    rows = list(re.finditer(r"(?m)^ +Package \(0x0C\)", old))
    require(len(rows) == 3, "Unexpected PSVT relationships")
    middle_start = rows[1].start()
    middle_end = block_end(old, middle_start)
    require(
        "\\_SB.PC00.LPCB.SEN2," in old[middle_start:middle_end],
        "Wrong missing sensor relationship",
    )
    require(
        re.fullmatch(r",\s*", old[middle_end : rows[2].start()]) is not None,
        "Unexpected PSVT separator",
    )
    default = (old[:middle_start] + old[rows[2].start() :]).replace(
        "Name (PSVT, Package (0x04)", "Name (PSVT, Package (0x03)", 1
    )
    require(
        "SEN2" not in default and "TCPU" in default and "SEN3" in default,
        "Unexpected remaining thermal relationships",
    )
    indent = match[1]
    full_return = old.replace("Name (PSVT, ", "Return (", 1)
    default_return = default.replace("Name (PSVT, ", "Return (", 1)
    new = (
        f"{indent}Method (PSVT, 0, NotSerialized)\n{indent}{{\n"
        f"{indent}    If (CondRefOf (\\_SB.PC00.LPCB.SEN2))\n{indent}    {{\n"
        f"{indent_block(full_return, 8)}\n{indent}    }}\n"
        f"{indent_block(default_return, 4)}\n{indent}}}"
    )
    return old, new


def patch_thermal(source):
    old, new = thermal_blocks(source)
    result = unique_replace(source, old, new)
    require(
        unique_replace(result, new, old) == source, "Thermal transform changed unrelated source"
    )
    return revision(result, "DptfTb", "DptfTabl", 0x1000, 0x1001)
