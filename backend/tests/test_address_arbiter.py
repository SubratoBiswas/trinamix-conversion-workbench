"""Multi-provider address arbitration.

Pure: no network, no credentials. Provider responses are supplied as fixtures in
the canonical shape the adapters must produce, so the consensus rules can be
exercised without a single vendor account.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.address_arbiter import (  # noqa: E402
    AMBIGUOUS, CORRECTED, ERROR, OUTCOME_COMPLETED, OUTCOME_CORRECTED,
    OUTCOME_INVALID, OUTCOME_UNCHANGED, OUTCOME_UNRESOLVED, UNVERIFIED, VERIFIED,
    arbitrate, arbitrate_many, field_changes, needs_review, summarize, trust_for,
)
from app.services.address_service import address_key  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — a check that only printed FAIL would let pytest
    report the file green."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


US = dict(line1="500 Oracle Pkwy", line2="", city="Austin", region="TX",
          postal="78741", country="US")
CA = dict(line1="100 Queen St W", line2="", city="Toronto", region="ON",
          postal="M5H 2N2", country="CA")


def resp(provider, status, address=None, code=None):
    return {"provider": provider, "status": status, "address": address,
            "provider_code": code}


def four(status, address, providers=("loqate", "melissa", "postgrid", "smarty")):
    return [resp(p, status, dict(address)) for p in providers]


# ── Trust weighting ──────────────────────────────────────────────────────────
def test_trust_is_per_country():
    check("Loqate full trust in Canada", trust_for("loqate", "CA") == 1.0)
    check("Smarty discounted in Canada (SERP unconfirmed)",
          trust_for("smarty", "CA") == 0.6, f"got {trust_for('smarty', 'CA')}")
    check("Smarty full trust in the US", trust_for("smarty", "US") == 1.0)
    check("an unknown provider still counts but cannot dominate",
          0 < trust_for("acme", "US") < 1.0)


# ── Agreement ────────────────────────────────────────────────────────────────
def test_unanimous_confirmation_is_unchanged():
    r = arbitrate(US, four(VERIFIED, US))
    check("outcome unchanged", r["outcome"] == OUTCOME_UNCHANGED, f"got {r['outcome']}")
    check("unanimous", r["unanimous"])
    check("all four back it", len(r["backers"]) == 4)
    check("not contested", not r["contested"])
    check("no review needed", not needs_review(r))


def test_cosmetic_differences_do_not_split_consensus():
    """"ST" vs "St" and "M5H2N2" vs "M5H 2N2" are the same address."""
    variants = [
        resp("loqate", VERIFIED, dict(CA, postal="M5H2N2")),
        resp("melissa", VERIFIED, dict(CA, region="Ontario")),
        resp("postgrid", VERIFIED, dict(CA, country="Canada")),
        resp("smarty", VERIFIED, dict(CA)),
    ]
    r = arbitrate(CA, variants)
    check("still one group", len(r["backers"]) == 4, f"got {r['backers']}")
    check("and unchanged", r["outcome"] == OUTCOME_UNCHANGED, f"got {r['outcome']}")


# ── Completion vs correction — the distinction the analyst needs ─────────────
def test_missing_fields_are_completed_not_corrected():
    partial = dict(line1="500 Oracle Pkwy", city="Austin", region="", postal="",
                   country="US")
    r = arbitrate(partial, four(CORRECTED, US))
    check("outcome is completed", r["outcome"] == OUTCOME_COMPLETED, f"got {r['outcome']}")
    check("region and postal reported as filled",
          set(r["filled_fields"]) >= {"region", "postal"}, f"got {r['filled_fields']}")
    check("nothing reported as changed", r["changed_fields"] == [],
          f"got {r['changed_fields']}")
    check("reason says nothing was overwritten",
          "overwritten" in r["reason"], f"got {r['reason']}")
    check("still needs review", needs_review(r))


def test_overwriting_an_existing_value_is_a_correction():
    wrong = dict(US, postal="78702")
    r = arbitrate(wrong, four(CORRECTED, US))
    check("outcome is corrected", r["outcome"] == OUTCOME_CORRECTED, f"got {r['outcome']}")
    check("postal listed as changed", r["changed_fields"] == ["postal"],
          f"got {r['changed_fields']}")
    check("diff carries both sides",
          r["field_changes"]["postal"]["from"] == "78702"
          and r["field_changes"]["postal"]["to"] == "78741")


def test_field_changes_ignores_cosmetic_only():
    c = field_changes(dict(US, region="Texas"), US)
    check("Texas -> TX is not a change", c["region"]["kind"] == "unchanged",
          f"got {c['region']}")


# ── Disagreement ─────────────────────────────────────────────────────────────
def test_majority_wins_and_dissent_is_recorded():
    other = dict(US, line1="501 Oracle Pkwy")
    rs = [resp("loqate", CORRECTED, dict(US)),
          resp("melissa", CORRECTED, dict(US)),
          resp("postgrid", CORRECTED, dict(US)),
          resp("smarty", CORRECTED, other)]
    r = arbitrate(dict(US, line1="500 Oracle Parkway Ste"), rs)
    check("the three agreeing win",
          sorted(r["backers"]) == ["loqate", "melissa", "postgrid"], f"got {r['backers']}")
    check("the dissenter is named", r["dissenters"] == ["smarty"], f"got {r['dissenters']}")
    check("its answer is kept as an alternative",
          r["alternatives"] and r["alternatives"][0]["address"]["line1"] == "501 Oracle Pkwy")
    check("not unanimous", not r["unanimous"])


def test_two_two_split_is_contested():
    a, b = dict(US), dict(US, line1="501 Oracle Pkwy")
    rs = [resp("loqate", CORRECTED, a), resp("melissa", CORRECTED, a),
          resp("postgrid", CORRECTED, b), resp("smarty", CORRECTED, b)]
    r = arbitrate(dict(US, line1="500 Oracle Parkway Ste"), rs)
    check("flagged contested", r["contested"], f"confidence {r['confidence']}")
    check("reason names both camps", "disagree" in r["reason"], f"got {r['reason']}")
    check("needs review", needs_review(r))


def test_ambiguous_alone_cannot_win_against_a_confident_answer():
    a, b = dict(US), dict(US, line1="9 Nowhere Rd")
    rs = [resp("loqate", VERIFIED, a), resp("smarty", AMBIGUOUS, b)]
    r = arbitrate(US, rs)
    check("the verified answer wins", r["backers"] == ["loqate"], f"got {r['backers']}")


def test_canadian_weighting_breaks_a_tie_toward_serp_certified():
    a, b = dict(CA), dict(CA, line1="101 Queen St W")
    rs = [resp("loqate", CORRECTED, a), resp("smarty", CORRECTED, b)]
    r = arbitrate(dict(CA, line1="100 Queen Street West"), rs)
    check("SERP-certified Loqate outranks Smarty in Canada",
          r["backers"] == ["loqate"], f"got {r['backers']}")


def test_result_is_deterministic():
    """Same inputs must give the same winner — a decision is saved against it."""
    a, b = dict(US), dict(US, line1="501 Oracle Pkwy")
    rs = [resp("loqate", CORRECTED, a), resp("melissa", CORRECTED, b)]
    first = arbitrate(US, rs)["recommended"]
    for _ in range(5):
        check("stable winner", arbitrate(US, list(rs))["recommended"] == first)


# ── Invalid vs unreachable ───────────────────────────────────────────────────
def test_no_match_anywhere_is_invalid():
    rs = [resp(p, UNVERIFIED) for p in ("loqate", "melissa", "postgrid", "smarty")]
    r = arbitrate(dict(US, line1="99999 Nowhere Rd"), rs)
    check("outcome invalid", r["outcome"] == OUTCOME_INVALID, f"got {r['outcome']}")
    check("reason says not deliverable", "not a deliverable" in r["reason"].lower(),
          f"got {r['reason']}")
    check("needs review", needs_review(r))


def test_all_calls_failing_is_unresolved_not_invalid():
    """A dead API must never be reported as 'this address is wrong'."""
    rs = [resp(p, ERROR) for p in ("loqate", "melissa", "postgrid", "smarty")]
    r = arbitrate(US, rs)
    check("outcome unresolved", r["outcome"] == OUTCOME_UNRESOLVED, f"got {r['outcome']}")
    check("reason blames the call, not the data",
          "could be reached" in r["reason"], f"got {r['reason']}")


# ── Consensus is not a safety guarantee ──────────────────────────────────────
def test_agreed_winner_that_fails_offline_checks_is_still_flagged():
    bad = dict(CA, region="ON", postal="V6B 1A1")     # V-prefix is BC, not ON
    r = arbitrate(dict(CA, postal=""), four(CORRECTED, bad))
    check("unanimous but contested", r["contested"], "should force review")
    check("offline error surfaced",
          any(i["code"] == "postal_region_mismatch" for i in r["offline_issues"]))
    check("reason warns about it", "offline check" in r["reason"], f"got {r['reason']}")


# ── Options offered to the analyst ───────────────────────────────────────────
def test_every_distinct_answer_is_offered_plus_the_original():
    a, b = dict(US), dict(US, line1="501 Oracle Pkwy")
    rs = [resp("loqate", CORRECTED, a), resp("melissa", CORRECTED, a),
          resp("postgrid", CORRECTED, b), resp("smarty", ERROR)]
    r = arbitrate(dict(US, line1="500 Oracle Parkway Ste"), rs)
    sources = [o["source"] for o in r["options"]]
    check("recommended first", sources[0] == "recommended")
    check("alternative offered", "alternative" in sources)
    check("keeping the original is always a choice", sources[-1] == "original")
    check("each option carries its own diff",
          all("field_changes" in o for o in r["options"]))


def test_evidence_marks_unconfirmed_certification():
    r = arbitrate(CA, four(VERIFIED, CA))
    smarty = [e for e in r["evidence"] if e["provider"] == "smarty"][0]
    loqate = [e for e in r["evidence"] if e["provider"] == "loqate"][0]
    check("Smarty/CA flagged as unconfirmed", smarty["certification_unconfirmed"])
    check("Loqate/CA not flagged", not loqate["certification_unconfirmed"])


# ── Batch ────────────────────────────────────────────────────────────────────
def test_summary_counts_by_outcome():
    ok_key, bad_key = address_key(US), address_key(CA)
    res = arbitrate_many(
        {ok_key: US, bad_key: dict(CA, postal="")},
        {ok_key: four(VERIFIED, US), bad_key: four(CORRECTED, CA)})
    s = res["summary"]
    check("two addresses", s["addresses"] == 2)
    check("one unchanged", s["counts"][OUTCOME_UNCHANGED] == 1, f"got {s['counts']}")
    check("one completed", s["counts"][OUTCOME_COMPLETED] == 1, f"got {s['counts']}")
    check("only the completed one needs review", s["needs_review"] == 1,
          f"got {s['needs_review']}")


def test_summarize_handles_an_empty_batch():
    s = summarize({})
    check("no addresses", s["addresses"] == 0)
    check("nothing to review", s["needs_review"] == 0)


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
