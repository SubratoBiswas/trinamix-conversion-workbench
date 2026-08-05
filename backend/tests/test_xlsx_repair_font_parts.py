"""The out-of-range font attribute is not only in the stylesheet.

WHAT WAS BROKEN
---------------
``xlsx_repair`` exists because Oracle ships templates containing
``<family val="34"/>``; OOXML bounds that attribute at 14 and openpyxl refuses the
ENTIRE workbook over it. The repair read ``xl/styles.xml``, clamped what it found
there, and rewrote the zip.

``3_SupplierSite_POZ_SUPPLIER_SITES_INT.xlsm`` has a clean stylesheet. Its bad
value — seven of them — sits in ``xl/comments1.xml``, in the tooltip Oracle
attaches to a column header. So the repair reported "nothing out of range",
changed nothing, and the workbook still would not open write-capable. The filled
Supplier Site template could not be produced at all: generation raised, the
carrier was marked failed, and the bundle had five interfaces where six were
expected.

WHY NOBODY SAW IT COMING
------------------------
The parser opens templates ``read_only=True``, and openpyxl skips comments
entirely in that mode. So the template uploaded, parsed, and listed all 211 of
its fields perfectly. It failed only at the moment of FILLING it — a different
code path, usually on a different day. ``test_dropped_sheets.py`` had been
skipping itself over this exact error for long enough that the skip read as
scenery.

A font description is legal in more places than the stylesheet: shared strings
carry one per rich-text run, comments carry one per run of tooltip text, and
openpyxl parses all of them through the same bounded descriptor. Any single one
refuses the whole file, so the repair has to cover all of them.
"""
import os
import re
import sys
import warnings
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.xlsx_repair import (                            # noqa: E402
    clamp_style_values, load_workbook_tolerant, repair_bytes,
)

_BACKEND = Path(__file__).resolve().parent.parent
_TEMPLATES = _BACKEND / "app" / "data" / "fbdi_templates"
_SITE = _TEMPLATES / "3_SupplierSite_POZ_SUPPLIER_SITES_INT.xlsm"
_FAMILY = re.compile(rb'<family\s+val="(\d+)"\s*/?>')


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_supplier_site_template_really_does_carry_the_bad_value():
    """Pinned to the file, so this cannot pass because the workbook was quietly
    swapped for a clean one. If Oracle ever reships it fixed, this fails and the
    repair can be reconsidered rather than left in place forever on faith."""
    check("the template is bundled", _SITE.exists())
    with zipfile.ZipFile(_SITE) as z:
        parts = {n: z.read(n) for n in z.namelist() if n.endswith(".xml")}
    offenders = {n: sorted({int(v) for v in _FAMILY.findall(d) if int(v) > 14})
                 for n, d in parts.items() if _FAMILY.search(d)}
    offenders = {n: v for n, v in offenders.items() if v}
    check("something is out of range", offenders, "the workbook looks clean now")
    check("and it is NOT the stylesheet", "xl/styles.xml" not in offenders,
          f"got {offenders}")
    check("it is a comments part", any("comments" in n for n in offenders),
          f"got {sorted(offenders)}")


def test_the_repair_reaches_parts_other_than_the_stylesheet():
    raw = _SITE.read_bytes()
    repaired, what = repair_bytes(raw)
    check("something was repaired", repaired is not raw, f"said: {what}")
    check("and it says where", "comments" in what, f"said: {what}")
    with zipfile.ZipFile(__import__("io").BytesIO(repaired)) as z:
        for n in z.namelist():
            if not n.endswith(".xml"):
                continue
            bad = [int(v) for v in _FAMILY.findall(z.read(n)) if int(v) > 14]
            check(f"{n} is in range", not bad, f"still {bad}")


def test_the_repaired_workbook_opens_write_capable():
    """read_only is not enough. ``template_fill_service`` writes rows INTO this
    workbook and hands it back, so it has to open in the mode that can be saved."""
    warnings.filterwarnings("ignore")
    wb = load_workbook_tolerant(_SITE, keep_vba=True)
    check("it opens", wb is not None)
    check("with its sheets", len(wb.sheetnames) >= 2, f"got {wb.sheetnames}")
    check("including the interface tab",
          any("SITES" in s.upper() for s in wb.sheetnames), f"got {wb.sheetnames}")


def test_every_bundled_template_opens_write_capable():
    """The whole point. A template that parses and cannot be filled is a feature
    that works until the day somebody asks for the deliverable."""
    warnings.filterwarnings("ignore")
    failures = []
    for path in sorted(_TEMPLATES.glob("*.xlsm")):
        try:
            load_workbook_tolerant(path, keep_vba=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}"[:160])
    check("every bundled template can be filled", not failures,
          "\n         " + "\n         ".join(failures))


def test_nothing_else_in_the_workbook_is_touched():
    """It is a TEMPLATE — macros, drawings and the VBA project are part of the
    deliverable. Only the parts carrying a bad font may change."""
    raw = _SITE.read_bytes()
    repaired, _ = repair_bytes(raw)
    import io
    a, b = zipfile.ZipFile(io.BytesIO(raw)), zipfile.ZipFile(io.BytesIO(repaired))
    check("same parts, same order", a.namelist() == b.namelist())
    changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
    check("only font-carrying parts changed",
          all("comments" in n or "sharedStrings" in n or "styles" in n for n in changed),
          f"changed {changed}")
    check("the VBA project survived",
          all(a.read(n) == b.read(n) for n in a.namelist() if n.endswith(".bin")))


def test_a_clean_workbook_is_returned_untouched():
    """Rewriting a zip that needs nothing is a way to break a file for free."""
    clean = _TEMPLATES / "6_SupplierBank_IBY_TEMP_EXT_PAYEES.xlsm"
    raw = clean.read_bytes()
    repaired, what = repair_bytes(raw)
    check("identical object returned", repaired is raw, f"said: {what}")
    check("and it says why", "nothing out of range" in what, f"said: {what}")


def test_the_clamp_itself_still_only_moves_what_is_out_of_range():
    xml = b'<font><family val="2"/><family val="34"/><charset val="1"/></font>'
    out, n = clamp_style_values(xml)
    check("one change", n == 1, f"got {n}")
    check("the legal value is untouched", b'<family val="2"/>' in out)
    check("the illegal one is clamped", b'<family val="34"/>' not in out)
    check("to a sane default", out.count(b'<family val="2"/>') == 2, f"got {out}")
    check("charset untouched", b'<charset val="1"/>' in out)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall xlsx repair checks passed")
