"""An identical name is a duplicate. A different key is not a defence.

Four NextPower supplier rows read

    1416     Nanjing Roytek & 3X Motion Technologies Co., LTD
    3567106  Nanjing Roytek & 3X Motion Technologies Co., LTD
    3567111  Nanjing Roytek & 3X Motion Technologies Co., LTD
    3792588  Nanjing Roytek & 3X Motion Technologies Co., LTD

— byte-identical names, four supplier numbers — and Duplicate suspects reported
"Scanned 3813 records — no near-duplicate entities above the match threshold".

`_pair_score` was a weighted average over every identity field non-blank in both
rows. The name scored 1.0; the supplier NUMBER, being a strong id that differed,
scored 0.0 and kept its weight in the denominator. A perfect name match was
averaged down to roughly 0.5/0.8 = 0.63 against a 0.86 threshold and the pair was
discarded.

That is backwards. Two rows carrying different supplier numbers is the definition
of the duplicate this function exists to find — the same company entered twice
under two keys. The scorer was treating the very thing that makes it a duplicate
as proof that it was not one.

Pure: hand-built rows, no database, no pandas fixtures.
"""
from app.services.entity_resolution import _pair_score, _str_sim

NAME = "Supplier Name"
NUM = "Supplier Number"
TAX = "Taxpayer ID"

FIELDS = [
    {"column": NAME, "kind": "name", "weight": 0.50},
    {"column": NUM, "kind": "number", "weight": 0.30},
    {"column": TAX, "kind": "taxid", "weight": 0.30},
]

ROYTEK = "Nanjing Roytek & 3X Motion Technologies Co., LTD"
THRESHOLD = 0.86


def row(num="", name=ROYTEK, tax=""):
    return {NAME: name, NUM: num, TAX: tax}


# ── The reported case ────────────────────────────────────────────────────────

def test_the_four_roytek_rows_are_duplicates():
    """The exact data that shipped, and the reason this test exists."""
    numbers = ["1416", "3567106", "3567111", "3792588"]
    rows = [row(n) for n in numbers]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            score, _ = _pair_score(rows[i], rows[j], FIELDS, NAME)
            assert score >= THRESHOLD, (
                f"{numbers[i]} vs {numbers[j]} scored {score} — identical names")


def test_an_identical_name_scores_full_confidence():
    score, ev = _pair_score(row("1416"), row("3792588"), FIELDS, NAME)
    assert score == 1.0
    assert ev.get(NAME) == 1.0


def test_a_differing_key_cannot_veto_an_identical_name():
    """The specific regression. Every strong id disagreeing at once must still
    leave an identical name standing."""
    a = row("1416", tax="111111111")
    b = row("3792588", tax="999999999")
    score, _ = _pair_score(a, b, FIELDS, NAME)
    assert score >= THRESHOLD


def test_the_old_weighted_average_would_have_failed_this():
    """Proof the test is aimed at the real defect rather than passing by luck:
    the previous formula, reconstructed here, drops the pair."""
    a, b = row("1416"), row("3792588")
    total_w = acc = 0.0
    for f in FIELDS:
        v1, v2 = str(a.get(f["column"], "")), str(b.get(f["column"], ""))
        if not v1 or not v2:
            continue
        total_w += f["weight"]
        s = (1.0 if v1 == v2 else 0.0) if f["kind"] in ("taxid", "number") \
            else _str_sim(v1, v2)
        acc += f["weight"] * s
    old_score = acc / total_w
    assert old_score < THRESHOLD, "the old formula would have passed; test is not aimed"
    assert _pair_score(a, b, FIELDS, NAME)[0] >= THRESHOLD


# ── A disagreeing strong id abstains, it does not vote against ───────────────

def test_a_differing_tax_id_abstains_rather_than_arguing_the_pair_apart():
    a = {NAME: "Acme Steel", NUM: "1", TAX: "111111111"}
    b = {NAME: "Acme Steele", NUM: "2", TAX: "999999999"}
    score, _ = _pair_score(a, b, FIELDS, NAME)
    name_only = _str_sim("Acme Steel", "Acme Steele")
    assert abs(score - name_only) < 0.01, (
        "a differing key changed the score; it should have abstained")


def test_a_matching_tax_id_still_strengthens_the_case():
    weak = {NAME: "Acme Steel", NUM: "1", TAX: ""}
    weak2 = {NAME: "Acme Trading", NUM: "2", TAX: ""}
    strong = {NAME: "Acme Steel", NUM: "1", TAX: "111111111"}
    strong2 = {NAME: "Acme Trading", NUM: "2", TAX: "111111111"}
    assert _pair_score(strong, strong2, FIELDS, NAME)[0] > \
           _pair_score(weak, weak2, FIELDS, NAME)[0]


# ── It must still say no to things that are not duplicates ──────────────────

def test_two_different_companies_are_not_clustered():
    a = {NAME: "Nanjing Roytek Motion Technologies", NUM: "1", TAX: ""}
    b = {NAME: "Shenzhen Precision Castings", NUM: "2", TAX: ""}
    assert _pair_score(a, b, FIELDS, NAME)[0] < THRESHOLD


def test_a_blank_name_is_not_a_match_for_another_blank_name():
    """Otherwise every unnamed row collapses into one enormous cluster."""
    a = {NAME: "", NUM: "1", TAX: ""}
    b = {NAME: "", NUM: "2", TAX: ""}
    assert _pair_score(a, b, FIELDS, NAME)[0] < THRESHOLD


def test_nothing_in_common_scores_zero():
    a = {NAME: "", NUM: "", TAX: ""}
    b = {NAME: "", NUM: "", TAX: ""}
    assert _pair_score(a, b, FIELDS, NAME)[0] == 0.0


# ── Normalisation still applies to the short-circuit ─────────────────────────

def test_legal_suffixes_and_punctuation_do_not_break_the_exact_match():
    """"ACME  Inc " and "acme, inc." are the same company."""
    a = {NAME: "ACME  Inc ", NUM: "1", TAX: ""}
    b = {NAME: "acme, inc.", NUM: "2", TAX: ""}
    assert _pair_score(a, b, FIELDS, NAME)[0] == 1.0


def test_the_short_circuit_needs_an_anchor_to_fire():
    """Called without one, it falls back to the weighted path rather than
    silently treating the first field as a name."""
    a, b = row("1416"), row("3792588")
    score, _ = _pair_score(a, b, FIELDS)
    assert score >= THRESHOLD, "the weighted path should still clear on an identical name"


def test_corroborating_evidence_is_reported_on_a_short_circuit():
    """The UI lists which fields matched. Short-circuiting must not blank that."""
    a = {NAME: ROYTEK, NUM: "1", TAX: "111111111"}
    b = {NAME: ROYTEK, NUM: "2", TAX: "111111111"}
    _, ev = _pair_score(a, b, FIELDS, NAME)
    assert ev.get(NAME) == 1.0
    assert TAX in ev, "a matching tax id should still be shown as evidence"


def test_the_scorer_is_passed_the_anchor_by_its_caller():
    """A fix the caller does not use is not a fix."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "entity_resolution.py").read_text(encoding="utf-8")
    assert "_pair_score(recs[i], recs[j], fields, anchor)" in src
