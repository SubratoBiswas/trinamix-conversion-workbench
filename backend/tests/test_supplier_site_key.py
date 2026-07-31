"""Supplier Site = 2-character ISO country code, a hyphen, and the city.

Analyst, 30-Jul, in three messages that each changed the spec:
  1. "the rule for supplier site column is Country Code (2 character ISO code)-(City
     value) can you implement this in learnings so that it comes in output of
     existing and future"
  2. "if no city, keep just the country code, if no country code but there is city,
     just mentioned city"
  3. "sorry if no country code, fill in country code based on city"

THE CODE IS ALREADY ISO-2. Checked against the extract rather than assumed: every
non-blank "Country Code" is exactly two characters — US 2,592, BR 1,652, IN 1,172,
CN 358, AU 290 — so the rule joins, it does not convert. Worth stating, because
"Country" sits next to it holding full names, and picking that column would have
produced "United States-Chicago" on 2,592 rows.

THE COUNTRY IS DERIVED FROM THE EXTRACT ITSELF. 6,196 rows carry both a code and a
city, which is a better city→country table than any bundled list and cannot go
stale. Ambiguity is real — "New York" appears against US 48 times and CN once, "San
Jose" US 109 and CR once — so the majority wins rather than whichever row came
first.

MEASURED, and this is the part worth saying out loud: on THIS extract 6,196 rows
have both, 1,066 have a code and no city, 233 have neither, and ZERO have a city
without a code. The derivation the analyst asked for has nothing to resolve here
today. It is built because eBOS and SyteLine extracts are not this one, but nobody
should read a passing test as evidence it ran.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.strategy_overlay import directive_for      # noqa: E402
from app.transformations.engine import apply_pipeline        # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


CITY_INDEX = {"chicago": "US", "sorocaba": "BR", "hyderabad": "IN"}


def site(row, index=None):
    d = directive_for("Supplier Site", "Supplier Site")
    return apply_pipeline([d["rule"]], "", row=row,
                          ctx={"city_country": CITY_INDEX if index is None else index})


def test_the_key_is_code_hyphen_city():
    check("US-Chicago", site({"Country Code": "US", "City": "Chicago"}) == "US-Chicago")
    check("BR-Sorocaba", site({"Country Code": "BR", "City": "Sorocaba"}) == "BR-Sorocaba")


def test_no_city_keeps_just_the_country_code():
    """Analyst: "if no city, keep just the country code". 1,066 rows on this
    extract. It must not be "US-"."""
    check("US", site({"Country Code": "US", "City": ""}) == "US")


def test_no_country_code_derives_it_from_the_city():
    """Analyst's correction: "if no country code, fill in country code based on
    city". The extract's own rows are the lookup."""
    check("derived", site({"Country Code": "", "City": "Chicago"}) == "US-Chicago")


def test_an_underivable_city_still_yields_the_city():
    """The earlier instruction is the fallback: "if no country code but there is
    city, just mentioned city". Never a dangling separator."""
    check("city alone", site({"Country Code": "", "City": "Nowhereville"}) == "Nowhereville")


def test_neither_yields_nothing():
    check("blank", site({"Country Code": "", "City": ""}) == "")


def test_neither_present_keeps_whatever_was_already_there():
    """Both columns empty is ALSO what it looks like when the rule is pointed at
    columns an extract does not have. Blanking then would silently wipe a value
    something else had mapped — and 8,561 rows once shipped a literal "-" into
    this required unique key for exactly that reason."""
    d = directive_for("Supplier Site", "Supplier Site")
    got = apply_pipeline([d["rule"]], "KEEP", row={"unrelated": "x"},
                         ctx={"city_country": CITY_INDEX})
    check("incoming value survives", got == "KEEP", f"got {got!r}")


def test_the_index_is_built_from_the_extract_and_takes_the_majority():
    """"New York" is US 48 times and CN once in the real file. First-row-wins would
    be a coin toss on row order."""
    import pandas as pd
    from app.services.output_service import _build_city_country_index
    src = pd.DataFrame({"Country Code": ["US"] * 3 + ["CN"] + ["BR"],
                        "City": ["New York"] * 4 + ["Sorocaba"]})
    idx = _build_city_country_index(
        src, [{"country_column": "Country Code", "city_column": "City"}])
    check("majority wins", idx.get("newyork") == "US", f"got {idx}")
    check("and the unambiguous one is kept", idx.get("sorocaba") == "BR")


def test_the_index_ignores_rows_that_cannot_teach_anything():
    import pandas as pd
    from app.services.output_service import _build_city_country_index
    src = pd.DataFrame({"Country Code": ["", "US", ""], "City": ["Chicago", "", ""]})
    idx = _build_city_country_index(
        src, [{"country_column": "Country Code", "city_column": "City"}])
    check("nothing learned from half rows", idx == {}, f"got {idx}")


def test_it_reads_the_iso_column_not_the_country_name():
    d = directive_for("Supplier Site", "Supplier Site")
    cfg = d["rule"]["config"]
    # Both are CANDIDATE LISTS now — the sheet's frame may spell them Billing/
    # Shipping. What still matters is that the ISO-2 code leads and the full
    # country NAME never does: "Country" holds "United States", and choosing it
    # would have produced "United States-Chicago" on 2,592 rows.
    ccs = cfg["country_column"]
    ccs = ccs if isinstance(ccs, list) else [ccs]
    cities = cfg["city_column"]
    cities = cities if isinstance(cities, list) else [cities]
    check("the ISO code column leads", ccs[0] == "Country Code", f"got {ccs}")
    check("every earlier candidate is a CODE column",
          all("code" in c.lower() for c in ccs[:-1]), f"got {ccs}")
    check("the full-name column is last resort only",
          ccs[-1] == "Country" and len(ccs) > 1, f"got {ccs}")
    check("city leads the city candidates", cities[0] == "City", f"got {cities}")
    check("separator is a hyphen", cfg["separator"] == "-")
    check("derivation is on", cfg.get("resolve_country_from_city") is True)


def test_the_generator_declares_both_columns_and_builds_the_index():
    """Seam: the rule reads two source columns it does not own, and an index the
    generator has to build. Either one missing makes the column silently empty —
    which is exactly how Supplier Site shipped blank on 8,561 rows before."""
    from pathlib import Path
    from app.services.strategy_overlay import referenced_columns
    cols = referenced_columns("Supplier Site")
    for c in ("Country Code", "City"):
        check(f"{c} is declared", c in cols, f"got {sorted(cols)}")
    out = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "output_service.py").read_text(encoding="utf-8")
    check("the index is built", "_build_city_country_index(" in out)
    check("and handed to every chunk", "_city_idx" in out)


def test_existing_conversions_get_the_rule_too():
    """Seam over "existing and future"."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    svc = (root / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    check("there is a propagation for rule corrections",
          "async def apply_rule_corrections(" in svc)
    check("it writes the derivation onto the mapping",
          '"suggested_transformation": {"rule_type": rtype, "config": rcfg}' in svc)
    check("it respects a later human decision", "_at >= as_of" in svc)
    check("and marks the built files stale", '"status": "stale"' in svc)
    seed = (root / "app" / "services" / "catalog_seed_service.py").read_text(encoding="utf-8")
    check("the seeder calls it", "apply_rule_corrections(" in seed)
    check("with the file's date", "as_of=_effective_date_of(doc)" in seed)
    check("and reports what it did", '"rule_corrections": rules_applied' in seed)


# ── The 30-Jul output said "Hyderabad", not "IN-Hyderabad" ───────────────────
def test_the_key_reads_whichever_country_column_the_sheet_actually_carries():
    """A sheet is routed to whichever bound source supplies most of its mapped
    columns, and that frame need not carry the column the analyst named. The
    30-Jul 22:18 bundle is the proof: POZ_SUPPLIER_SITES_INT shipped "Hyderabad"
    on 4,388 rows — city present, country code empty — because the rule asked for
    "Country Code" and that frame holds the Billing/Shipping spellings.
    """
    for label, row, want in (
        ("plain",    {"Country Code": "IN", "City": "Hyderabad"}, "IN-Hyderabad"),
        ("billing",  {"Billing Country Code": "IN", "Billing City": "Hyderabad"}, "IN-Hyderabad"),
        ("shipping", {"Shipping Country Code": "BR", "Shipping City": "Sorocaba"}, "BR-Sorocaba"),
        ("mixed",    {"Country Code": "US", "Billing City": "Chicago"}, "US-Chicago"),
    ):
        check(f"{label} spelling resolves", site(row) == want, f"got {site(row)!r}")


def test_column_names_match_case_and_punctuation_insensitively():
    check("lower-case, no space", site({"countrycode": "IN", "city": "Hyderabad"})
          == "IN-Hyderabad", f"got {site({'countrycode': 'IN', 'city': 'Hyderabad'})!r}")


def test_the_reported_row_shape_now_produces_the_full_key():
    """City present, country code absent — exactly the 4,388 rows that shipped
    bare city names. The index derives the code from the extract's own rows."""
    check("derived", site({"City": "Hyderabad"}) == "IN-Hyderabad",
          f"got {site({'City': 'Hyderabad'})!r}")


def test_every_candidate_column_is_declared_to_the_generator():
    """The generator prunes the frame to declared columns. A candidate the rule
    might read but never declared is a column it will never see — which is how
    Supplier Site shipped empty on 8,561 rows in the first place."""
    from app.services.strategy_overlay import referenced_columns
    cols = referenced_columns("Supplier Site")
    for c in ("Country Code", "Billing Country Code", "Shipping Country Code",
              "City", "Billing City", "Shipping City"):
        check(f"{c} is declared", c in cols, f"got {sorted(cols)}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall supplier-site key checks passed")
