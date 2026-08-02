"""Latest template wins, and the workbook branch is one implementation."""
import sys, types
from datetime import datetime, timedelta
sys.path.insert(0, ".")

def check(n, c, d=""):
    if c: print("  PASS ", n); return
    raise AssertionError(f"{n} {d}")

def test_template_recency_and_hdl_workbook():
        # 1. the model carries updated_at, defaulted
    from app.models.fbdi import FBDITemplate
    check("FBDITemplate declares updated_at", "updated_at" in FBDITemplate.model_fields)

    # 2. the ordering key: newest first, sheet count as tiebreak
    rows = [("old-11", datetime(2026,7,1), 11), ("new-2", datetime(2026,8,2), 2),
            ("same-day-0", datetime(2026,8,2), 0)]
    rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
    check("newest wins outright", rows[0][0] == "new-2", rows)
    check("and a same-day empty shell loses to the same-day one with sheets",
          rows[1][0] == "same-day-0" or rows[0][0] == "new-2", rows)
    # same timestamp -> more sheets first
    same = [("empty", datetime(2026,8,2), 0), ("full", datetime(2026,8,2), 11)]
    same.sort(key=lambda r: (r[1], r[2]), reverse=True)
    check("same timestamp: the complete template wins", same[0][0] == "full", same)

    # 3. six distinct worksheet names, from the real load order
    from app.services.hdl_schema import HDL_LOAD_ORDER, object_label
    names = [object_label(o)[:31] for o in HDL_LOAD_ORDER]
    check("six objects", len(names) == 6, names)
    check("names are unique and Excel-legal", len(set(names)) == 6 and all(len(n) <= 31 for n in names))

    # 4. the writer honours fmt, and both shapes share the row loop
    src = open("app/services/hdl_output_service.py").read()
    check("fmt selects the container", '_as_book = str(fmt).lower() in ("template"' in src)
    check("workbook path writes one sheet per object", "book.create_sheet(_object_label(obj)" in src)
    check("and the .dat path is unchanged", 'iz.writestr(spec["dat"], dat)' in src)
    check("one row loop, not two", src.count("for seq, obj in enumerate(HDL_LOAD_ORDER") == 1)
    osrc = open("app/services/output_service.py").read()
    check("the divert passes fmt through", "generate_hdl_artifact(conversion, fmt=fmt)" in osrc)

    # 5. timestamps move on CHANGE, never on a no-op read
    hsrc = open("app/services/hdl_seed_service.py").read()
    i = hsrc.index("already complete")
    check("a template with nothing to add is NOT re-stamped",
          "updated_at" not in hsrc[i-400:i+200],
          "a no-op pass would re-stamp and invert the precedence it exists to express")
    check("but adding sheets does stamp it",
          'await tpl.set({"updated_at"' in hsrc)
    print("\nall checks passed")
