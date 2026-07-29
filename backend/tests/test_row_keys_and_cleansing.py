"""Per-row decision keys + the cleansing rule families.

Pure: pandas and stdlib only, no Beanie/Mongo/network — runs under plain pytest or
`python3 backend/tests/test_row_keys_and_cleansing.py`.

The duplicate-key tests are written against the real shape that exposed the bug: a
9-row CRRC YANGTZE cluster whose names differ only by trailing punctuation and
casing, so the identity hash collapsed them to three keys and the survivor radio
selected three rows at once.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.cleansing_rules import (  # noqa: E402
    CASE, LEGAL_SUFFIX, SPECIAL_CHARS, WHITESPACE_PUNCT,
    cleanse_frame, clean_value, preview_frame,
)
from app.services.decision_engine import (  # noqa: E402
    apply_decisions, cluster_key, identity_part, row_key, row_keys_for,
)

IDC = ["Supplier Name", "Taxpayer ID"]

# The nine rows from the live screen. Three distinct identity strings, three
# byte-identical rows each.
CRRC = pd.DataFrame([
    {"Supplier Name": "CRRC YANGTZE CO. LTD - ABU DHABI",  "Taxpayer ID": "100393595200003", "City": "Abu Dhabi"},
    {"Supplier Name": "CRRC YANGTZE CO. LTD - ABU DHABI",  "Taxpayer ID": "100393595200003", "City": ""},
    {"Supplier Name": "CRRC YANGTZE CO. LTD - ABU DHABI",  "Taxpayer ID": "100393595200003", "City": "Dubai"},
    {"Supplier Name": "CRRC YANGTZE CO. LTD - ABU DHABi.", "Taxpayer ID": "",                "City": "Abu Dhabi"},
    {"Supplier Name": "CRRC YANGTZE CO. LTD - ABU DHABi.", "Taxpayer ID": "",                "City": "Sharjah"},
    {"Supplier Name": "CRRC YANGTZE CO. LTD - ABU DHABi.", "Taxpayer ID": "",                "City": ""},
    {"Supplier Name": "CRRC YANGTZE CO. Ltd - ABU DHABI..", "Taxpayer ID": "",               "City": "Abu Dhabi"},
    {"Supplier Name": "CRRC YANGTZE CO. Ltd - ABU DHABI..", "Taxpayer ID": "",               "City": "Al Ain"},
    {"Supplier Name": "CRRC YANGTZE CO. Ltd - ABU DHABI..", "Taxpayer ID": "",               "City": ""},
])

_failures = []


def check(name, cond, detail=""):
    """Record AND raise — pytest judges a test by whether it throws, so a check
    that only printed FAIL would report the file green."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


# ── 1. Row keys are now per-row ──────────────────────────────────────────────
def test_keys_are_unique_per_row():
    keys = row_keys_for(CRRC, IDC)
    check("9 rows produce 9 distinct keys", len(set(keys)) == 9,
          f"got {len(set(keys))}")

    idents = {identity_part(k) for k in keys}
    check("still only 3 identity groups", len(idents) == 3, f"got {len(idents)}")


def test_identity_only_keys_unchanged():
    """The identity hash itself must not move, or every saved decision breaks."""
    bare = row_keys_for(CRRC, IDC, unique=False)
    manual = row_key(["CRRC YANGTZE CO. LTD - ABU DHABI", "100393595200003"])
    check("bare identity hash matches row_key()", bare[0] == manual)
    check("identity_part recovers it", identity_part(row_keys_for(CRRC, IDC)[0]) == manual)


def test_cluster_key_survives_the_change():
    """cluster_key reduces to identity parts, so keys saved before per-row
    disambiguation still match — including cross-conversion keep_all learnings."""
    new = cluster_key(row_keys_for(CRRC, IDC))
    old = cluster_key(row_keys_for(CRRC, IDC, unique=False))
    check("cluster_key identical before/after the fix", new == old, f"{new} vs {old}")


def test_identical_rows_still_addressable():
    twins = pd.DataFrame([
        {"Supplier Name": "ACME", "Taxpayer ID": "1", "City": "X"},
        {"Supplier Name": "ACME", "Taxpayer ID": "1", "City": "X"},
    ])
    keys = row_keys_for(twins, IDC)
    check("byte-identical rows get distinct keys", keys[0] != keys[1])
    check("...differing only in the occurrence ordinal",
          keys[0][:-1] == keys[1][:-1])


def test_keys_survive_a_reshuffle():
    shuffled = CRRC.iloc[[7, 2, 5, 0, 8, 3, 1, 6, 4]].reset_index(drop=True)
    before = row_keys_for(CRRC, IDC)
    after = row_keys_for(shuffled, IDC)
    check("the same 9 keys come back after reordering",
          sorted(before) == sorted(after))


# ── 2. Decisions land on the nominated PHYSICAL row ──────────────────────────
def test_keep_survivor_keeps_the_nominated_row():
    keys = row_keys_for(CRRC, IDC)
    # Nominate row index 2 — the third of three identity-twins. Before the fix its
    # key was shared with rows 0 and 1, and apply_decisions kept whichever sat
    # first, so "Dubai" could never be the survivor.
    out, rep = apply_decisions(CRRC, IDC, [{
        "decision_key": "c1", "verdict": "keep_survivor",
        "survivor_key": keys[2], "member_keys": keys,
    }])
    check("9 rows collapse to 1", len(out) == 1, f"got {len(out)}")
    check("the nominated row is the one kept",
          out.iloc[0]["City"] == "Dubai", f"got {out.iloc[0]['City']!r}")
    check("report counts 8 removed", rep["rows_removed"] == 8)


def test_each_twin_is_separately_selectable():
    keys = row_keys_for(CRRC, IDC)
    for idx, city in ((0, "Abu Dhabi"), (1, ""), (2, "Dubai")):
        out, _ = apply_decisions(CRRC, IDC, [{
            "decision_key": "c1", "verdict": "keep_survivor",
            "survivor_key": keys[idx], "member_keys": keys}])
        check(f"survivor {idx} -> City={city!r}", out.iloc[0]["City"] == city,
              f"got {out.iloc[0]['City']!r}")


def test_merge_still_builds_a_golden_record():
    keys = row_keys_for(CRRC, IDC)
    out, rep = apply_decisions(CRRC, IDC, [{
        "decision_key": "c1", "verdict": "merge",
        "survivor_key": keys[5], "member_keys": keys}])
    check("merge collapses to 1 row", len(out) == 1)
    check("golden record pulls the Taxpayer ID forward",
          out.iloc[0]["Taxpayer ID"] == "100393595200003",
          f"got {out.iloc[0]['Taxpayer ID']!r}")
    check("merge reported", rep["rows_merged"] == 1)


def test_legacy_identity_only_decision_still_applies():
    """A decision saved before this change stores bare identity hashes."""
    legacy = row_keys_for(CRRC, IDC, unique=False)
    out, rep = apply_decisions(CRRC, IDC, [{
        "decision_key": "c1", "verdict": "exclude",
        "survivor_key": None, "member_keys": list(dict.fromkeys(legacy))}])
    check("legacy keys still match all 9 rows", len(out) == 0, f"got {len(out)}")
    check("not reported stale", rep["stale"] == 0)


def test_keep_all_and_exclude_unchanged():
    keys = row_keys_for(CRRC, IDC)
    out, _ = apply_decisions(CRRC, IDC, [{
        "decision_key": "c1", "verdict": "keep_all", "member_keys": keys}])
    check("keep_all leaves every row", len(out) == 9)
    out, _ = apply_decisions(CRRC, IDC, [{
        "decision_key": "c1", "verdict": "exclude", "member_keys": keys}])
    check("exclude drops every row", len(out) == 0)


# ── 2b. keep_subset: keep SOME of a partly-duplicated cluster ────────────────
# The real PwC cluster: five 100%-name-matches carrying three different tax
# registrations, so it is three entities plus one duplicate pair.
PWC = pd.DataFrame([
    {"Supplier Name": "PricewaterhouseCoopers",        "Taxpayer ID": "",               "City": "New York"},
    {"Supplier Name": "PricewaterhouseCoopers LLP",    "Taxpayer ID": "13-4008324",     "City": "New York"},
    {"Supplier Name": "PricewaterhouseCoopers LLP.",   "Taxpayer ID": "98-0330214",     "City": "London"},
    {"Supplier Name": "PRICEWATERHOUSECOOPERS LLP.",   "Taxpayer ID": "870576089RT0001", "City": "Toronto"},
    {"Supplier Name": "Pricewaterhousecoopers, LLP.",  "Taxpayer ID": "870576089RT0001", "City": "Toronto"},
])


def test_keep_subset_keeps_exactly_the_nominated_rows():
    keys = row_keys_for(PWC, IDC)
    keep = [keys[1], keys[2], keys[3]]           # the three distinct registrations
    out, rep = apply_decisions(PWC, IDC, [{
        "decision_key": "pwc", "verdict": "keep_subset",
        "member_keys": keys, "keep_keys": keep}])
    check("3 of 5 rows survive", len(out) == 3, f"got {len(out)}")
    check("the stub and the duplicate pair member are gone",
          sorted(out["Taxpayer ID"]) == ["13-4008324", "870576089RT0001", "98-0330214"],
          f"got {sorted(out['Taxpayer ID'])}")
    check("2 removed", rep["rows_removed"] == 2)
    check("applied, not stale", rep["applied"] == 1 and rep["stale"] == 0)


def test_keep_subset_survives_a_reshuffle():
    keys = row_keys_for(PWC, IDC)
    keep = [keys[1], keys[3]]
    shuffled = PWC.iloc[[4, 0, 3, 1, 2]].reset_index(drop=True)
    out, _ = apply_decisions(shuffled, IDC, [{
        "decision_key": "pwc", "verdict": "keep_subset",
        "member_keys": keys, "keep_keys": keep}])
    check("the same two rows survive after reordering",
          sorted(out["Taxpayer ID"]) == ["13-4008324", "870576089RT0001"],
          f"got {sorted(out['Taxpayer ID'])}")


def test_keep_subset_with_no_resolvable_rows_refuses():
    """Dropping the cluster because the nominated rows vanished would delete
    exactly the rows the analyst asked to KEEP."""
    keys = row_keys_for(PWC, IDC)
    out, rep = apply_decisions(PWC, IDC, [{
        "decision_key": "pwc", "verdict": "keep_subset",
        "member_keys": keys, "keep_keys": ["deadbeef-cafe0"]}])
    check("no rows dropped", len(out) == len(PWC), f"got {len(out)}")
    check("reported stale", rep["stale"] == 1)


def test_keep_subset_of_one_equals_keep_survivor():
    keys = row_keys_for(PWC, IDC)
    a, _ = apply_decisions(PWC, IDC, [{"decision_key": "p", "verdict": "keep_subset",
                                       "member_keys": keys, "keep_keys": [keys[1]]}])
    b, _ = apply_decisions(PWC, IDC, [{"decision_key": "p", "verdict": "keep_survivor",
                                       "member_keys": keys, "survivor_key": keys[1]}])
    check("both leave the same single row",
          a.reset_index(drop=True).equals(b.reset_index(drop=True)))


# ── 2c. Conflicting strong identifiers are flagged, never auto-split ─────────
def test_id_conflicts_detected():
    from app.services.decision_engine import id_conflicts
    members = [{"values": {"Supplier Name": r["Supplier Name"],
                           "Taxpayer ID": r["Taxpayer ID"]}}
               for _, r in PWC.iterrows()]
    conf = id_conflicts(members, IDC)
    check("one conflicting column reported", len(conf) == 1, f"got {conf}")
    check("it is the Taxpayer ID", conf and conf[0]["column"] == "Taxpayer ID")
    check("three distinct values, blanks ignored, dupes collapsed",
          conf and len(conf[0]["values"]) == 3, f"got {conf[0]['values'] if conf else None}")


def test_no_conflict_when_ids_agree_or_are_blank():
    from app.services.decision_engine import id_conflicts
    members = [{"values": {"Supplier Name": "ACME", "Taxpayer ID": "1"}},
               {"values": {"Supplier Name": "ACME", "Taxpayer ID": ""}},
               {"values": {"Supplier Name": "ACME", "Taxpayer ID": "1"}}]
    check("one value + blanks is not a conflict",
          id_conflicts(members, IDC) == [], f"got {id_conflicts(members, IDC)}")


def test_name_columns_are_not_treated_as_strong_ids():
    from app.services.decision_engine import id_conflicts
    members = [{"values": {"Supplier Name": "ACME", "Taxpayer ID": "1"}},
               {"values": {"Supplier Name": "ACME Inc", "Taxpayer ID": "1"}}]
    check("differing names alone raise no conflict",
          id_conflicts(members, IDC) == [])


def test_clustering_is_not_split_by_a_conflict():
    """The CRRC cluster has one tax id and blanks; PwC has three. Both stay whole —
    id_conflicts is advisory and apply_decisions never consults it."""
    keys = row_keys_for(PWC, IDC)
    out, _ = apply_decisions(PWC, IDC, [{"decision_key": "p", "verdict": "keep_all",
                                         "member_keys": keys}])
    check("keep_all still keeps all five despite the conflict", len(out) == 5)


# ── 3. Cleansing families ────────────────────────────────────────────────────
def test_edge_punctuation():
    f = [WHITESPACE_PUNCT]
    check("trailing dot removed",
          clean_value("CRRC YANGTZE CO. LTD - ABU DHABi.", families=f)
          == "CRRC YANGTZE CO. LTD - ABU DHABi")
    check("double dot collapsed then stripped",
          clean_value("CRRC YANGTZE CO. Ltd - ABU DHABI..", families=f)
          == "CRRC YANGTZE CO. Ltd - ABU DHABI")
    check("internal run collapsed",
          clean_value("ACME  Inc", families=f) == "ACME Inc")
    check("interior dot preserved",
          clean_value("CO. LTD", families=f) == "CO. LTD")


def test_numeric_values_are_guarded():
    f = [WHITESPACE_PUNCT]
    check("negative number untouched", clean_value("-5", families=f) == "-5")
    check("decimal untouched", clean_value("1,234.50", families=f) == "1,234.50")
    check("percentage untouched", clean_value("12.5%", families=f) == "12.5%")


def test_special_characters():
    f = [SPECIAL_CHARS]
    check("zero-width stripped", clean_value("AC​ME", families=f) == "ACME")
    check("control char stripped", clean_value("AC\x07ME", families=f) == "ACME")
    check("smart quote plain", clean_value("O’Brien", families=f) == "O'Brien")
    check("em dash to hyphen", clean_value("A—B", families=f) == "A-B")
    check("nbsp to space", clean_value("A B", families=f) == "A B")
    check("accents kept by default", clean_value("Müller", families=f) == "Müller")
    check("ascii_fold strips accents when asked",
          clean_value("Müller", families=f, ascii_fold=True) == "Muller")


def test_case_normalisation():
    f = [CASE]
    check("acronym preserved",
          clean_value("CRRC YANGTZE", column="Supplier Name", families=f)
          == "CRRC Yangtze")
    check("code column upper-cased",
          clean_value("us", column="Country Code", families=f) == "US")
    check("particle lowered",
          clean_value("BANK OF AMERICA", column="Supplier Name", families=f)
          == "BANK of America", )
    check("O'Brien handled",
          clean_value("o'brien", column="Supplier Name", families=f) == "O'Brien")
    check("McDonald handled",
          clean_value("mcdonald", column="Supplier Name", families=f) == "McDonald")
    check("hyphenated handled",
          clean_value("smith-jones", column="Supplier Name", families=f) == "Smith-Jones")
    check("unknown column kind untouched",
          clean_value("weird value", column="Zzz", families=f) == "weird value")


def test_legal_suffix():
    # A column is now REQUIRED for this family: legal-suffix standardisation only
    # runs on name-kind columns, so that Tax Organization Type = CORPORATION (an
    # Oracle lookup code) is never rewritten to Corp.
    f = [LEGAL_SUFFIX]
    col = "Supplier Name"

    def cv(v):
        return clean_value(v, column=col, families=f)

    check("Limited -> Ltd", cv("Acme Limited") == "Acme Ltd")
    check("LTD -> Ltd", cv("Acme LTD") == "Acme Ltd")
    check("Incorporated -> Inc", cv("Acme Incorporated") == "Acme Inc")
    check("llc -> LLC", cv("Acme llc") == "Acme LLC")
    check("mid-name token untouched (outside the window)",
          cv("Limited Brands Holdings International Group")
          == "Limited Brands Holdings International Group")
    check("ambiguous 2-letter token untouched",
          cv("Nextracker AS Holdings") == "Nextracker AS Holdings")


def test_family_order_legal_beats_case():
    """Legal suffix runs last, so its canonical form is not title-cased away."""
    out = clean_value("acme limited", column="Supplier Name",
                      families=[WHITESPACE_PUNCT, CASE, LEGAL_SUFFIX])
    check("case then legal suffix", out == "Acme Ltd", f"got {out!r}")


def test_blank_and_none_are_left_alone():
    check("empty string", clean_value("", families=[WHITESPACE_PUNCT]) == "")
    check("None -> empty", clean_value(None, families=[WHITESPACE_PUNCT]) == "")
    check("whitespace-only preserved as-is",
          clean_value("   ", families=[WHITESPACE_PUNCT]) == "   ")


# ── 4. Frame-level cleansing + preview ───────────────────────────────────────
def test_cleanse_frame_reports_per_rule():
    out, findings = cleanse_frame(CRRC, {
        "families": [WHITESPACE_PUNCT], "per_field": {}, "exclude_fields": []})
    names = set(out["Supplier Name"])
    # Still 3 distinct: punctuation is gone, but "DHABi" vs "DHABI" and "Ltd" vs
    # "LTD" are casing differences that only the CASE family resolves.
    check("dots stripped, casing differences remain", len(names) == 3,
          f"got {sorted(names)}")
    check("no value ends in a dot any more",
          not any(n.endswith(".") for n in names), f"got {sorted(names)}")
    f = [x for x in findings if x["field"] == "Supplier Name"]
    check("a finding is reported for Supplier Name", len(f) == 1)
    check("it counts the 6 changed rows", f and f[0]["count"] == 6,
          f"got {f[0]['count'] if f else None}")
    check("it carries before/after examples", f and f[0]["examples"])


def test_all_four_families_collapse_the_variants():
    out, _ = cleanse_frame(CRRC, {
        "families": [SPECIAL_CHARS, WHITESPACE_PUNCT, CASE, LEGAL_SUFFIX],
        "per_field": {}, "exclude_fields": []})
    names = set(out["Supplier Name"])
    check("all 9 rows normalise to one supplier name", len(names) == 1,
          f"got {sorted(names)}")


def test_preview_changes_nothing():
    before = CRRC.copy()
    rep = preview_frame(CRRC, families=[WHITESPACE_PUNCT, CASE])
    check("source frame untouched by preview", CRRC.equals(before))
    check("preview reports a change count", rep["total_changes"] > 0)
    check("preview lists affected fields", rep["fields_affected"] >= 1)


def test_per_field_override_and_exclusion():
    out, _ = cleanse_frame(CRRC, {
        "families": [WHITESPACE_PUNCT], "per_field": {},
        "exclude_fields": ["Supplier Name"]})
    check("excluded field is untouched",
          set(out["Supplier Name"]) == set(CRRC["Supplier Name"]))

    out, _ = cleanse_frame(CRRC, {
        "families": [], "per_field": {"Supplier Name": [WHITESPACE_PUNCT]},
        "exclude_fields": []})
    check("per-field override enables a family for one column",
          not any(str(n).endswith(".") for n in out["Supplier Name"]),
          f"got {sorted(set(out['Supplier Name']))}")


def test_family_catalogue_matches_what_the_endpoint_promises():
    """/cleansing-profile builds its checkbox list from these constants, and the
    UI marks the unsafe ones "rewrites values" — so the split has to be right."""
    from app.services.cleansing_rules import FAMILIES, FAMILY_LABEL, SAFE_FAMILIES
    check("every family has a label",
          all(f in FAMILY_LABEL for f in FAMILIES))
    check("safe families are a subset", set(SAFE_FAMILIES) <= set(FAMILIES))
    check("the two value-rewriting families are NOT safe",
          CASE not in SAFE_FAMILIES and LEGAL_SUFFIX not in SAFE_FAMILIES)
    check("the two character-removal families ARE safe",
          WHITESPACE_PUNCT in SAFE_FAMILIES and SPECIAL_CHARS in SAFE_FAMILIES)


def test_default_profile_enables_only_safe_families():
    from app.services.cleansing_rules import SAFE_FAMILIES, default_profile
    prof = default_profile(["Supplier Name"])
    check("defaults are exactly the safe families",
          set(prof["families"]) == set(SAFE_FAMILIES), f"got {prof['families']}")
    check("ascii_fold off by default", prof["ascii_fold"] is False)


def test_preview_can_show_a_family_that_is_not_enabled():
    """The point of the preview is deciding whether to switch one on."""
    saved = {"families": [WHITESPACE_PUNCT], "per_field": {}, "exclude_fields": []}
    rep = preview_frame(CRRC, saved, families=[CASE])
    check("preview honours the override, not the saved profile",
          rep["families"] == [CASE], f"got {rep['families']}")
    check("and reports what CASE alone would do", rep["total_changes"] > 0)


ALL_FAMILIES = [SPECIAL_CHARS, WHITESPACE_PUNCT, CASE, LEGAL_SUFFIX]


def test_legal_suffix_never_touches_a_code_column():
    """Tax Organization Type = CORPORATION is the Oracle lookup CODE, not a
    company called "Corporation". Rewriting it to Corp broke 1,392 rows of a
    required field until legal-suffix was restricted to name columns."""
    out = clean_value("CORPORATION", column="Tax Organization Type",
                      families=ALL_FAMILIES)
    check("CORPORATION survives on a code column", out == "CORPORATION", f"got {out!r}")
    check("but a real company suffix is still standardised",
          clean_value("Acme LIMITED", column="Supplier Name",
                      families=[LEGAL_SUFFIX]) == "Acme Ltd")
    check("unknown-kind columns are left alone too",
          clean_value("Acme LIMITED", column="Zzz", families=[LEGAL_SUFFIX])
          == "Acme LIMITED")


def test_protected_values_are_never_rewritten():
    prot = {"g-treasury", "corporation"}
    check("a strategy constant survives case normalisation",
          clean_value("G-Treasury", column="Payment Method",
                      families=ALL_FAMILIES, protected=prot) == "G-Treasury")
    check("and survives every family",
          clean_value("CORPORATION", column="Supplier Name",
                      families=ALL_FAMILIES, protected=prot) == "CORPORATION")
    check("an unprotected value still cleanses",
          clean_value("acme  ltd.", column="Supplier Name",
                      families=ALL_FAMILIES, protected=prot) == "Acme Ltd")


def test_protected_values_are_harvested_from_the_strategy():
    from app.services.generate_dq import protected_values
    pv = protected_values()
    for v in ("corporation", "g-treasury", "spend_authorized", "supplier"):
        check(f"strategy constant {v!r} is protected", v in pv)


def test_iberian_particles_lowercase():
    out = clean_value("1 PLAN CONSULTORIA E ASSESSORIA LTDA",
                      column="Supplier Name", families=[CASE])
    check("Portuguese 'e' is lowered, LTDA kept as an acronym",
          out == "1 PLAN Consultoria e Assessoria LTDA", f"got {out!r}")


def test_value_override_beats_every_rule():
    prof = {"families": ALL_FAMILIES, "per_field": {}, "exclude_fields": [],
            "value_overrides": {"Supplier Name": {"acme  ltd.": "ACME Holdings"}}}
    df = pd.DataFrame([{"Supplier Name": "acme  ltd."},
                       {"Supplier Name": "beta limited"}])
    out, findings = cleanse_frame(df, prof)
    check("the pinned value wins", out["Supplier Name"].iloc[0] == "ACME Holdings",
          f"got {out['Supplier Name'].iloc[0]!r}")
    check("other rows still follow the rules",
          out["Supplier Name"].iloc[1] == "Beta Ltd",
          f"got {out['Supplier Name'].iloc[1]!r}")
    check("the override is reported as its own finding",
          any(f["rule"] == "override" for f in findings))


def test_override_matching_is_case_and_space_insensitive():
    prof = {"families": [], "per_field": {}, "exclude_fields": [],
            "value_overrides": {"Supplier Name": {"ACME LTD": "Fixed"}}}
    df = pd.DataFrame([{"Supplier Name": "  acme   ltd  "}])
    out, _ = cleanse_frame(df, prof)
    check("a differently-cased/spaced original still matches",
          out["Supplier Name"].iloc[0] == "Fixed", f"got {out['Supplier Name'].iloc[0]!r}")


def test_per_field_skip_stops_one_rule_on_one_column():
    prof = {"families": ALL_FAMILIES,
            "per_field": {"Alternate Name": [SPECIAL_CHARS, WHITESPACE_PUNCT]},
            "exclude_fields": []}
    df = pd.DataFrame([{"Supplier Name": "G&D Holdings INC",
                        "Alternate Name": "G&D Holdings INC"}])
    out, _ = cleanse_frame(df, prof)
    check("the rule still runs where it is enabled",
          out["Supplier Name"].iloc[0] == "G&D Holdings Inc",
          f"got {out['Supplier Name'].iloc[0]!r}")
    check("and is skipped on the excluded field",
          out["Alternate Name"].iloc[0] == "G&D Holdings INC",
          f"got {out['Alternate Name'].iloc[0]!r}")


def test_cleansing_does_not_change_row_count():
    out, _ = cleanse_frame(CRRC, {
        "families": [SPECIAL_CHARS, WHITESPACE_PUNCT, CASE, LEGAL_SUFFIX],
        "per_field": {}, "exclude_fields": []})
    check("cleansing never adds or drops rows", len(out) == len(CRRC))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass          # already recorded; keep going so one run shows them all
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
