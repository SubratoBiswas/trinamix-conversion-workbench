"""Reconcile several address providers into one recommended address.

WHY MORE THAN ONE PROVIDER
--------------------------
Each vendor's reference data and match logic differ, so on a messy supplier
extract they disagree — and the disagreement is the signal. Two providers
returning the same corrected address is strong evidence; four returning four
different answers means the address is genuinely ambiguous and a human has to
look. A single provider gives a confident answer either way and you cannot tell
those two cases apart.

WHAT THIS DOES NOT DO
---------------------
It never applies anything. It produces a RECOMMENDATION plus the evidence behind
it — who agreed, who dissented, and what each one said — and the analyst
approves, edits or rejects it. Silently rewriting a supplier's remit-to address
is a payment-risk change; it gets the same explicit verdict duplicates get.

TRUST IS PER COUNTRY, NOT PER VENDOR
------------------------------------
Certification is country-specific: CASS is the US standard, SERP is Canada
Post's. A provider certified for one and not the other should not carry equal
weight on both, so weights are keyed by (provider, country). The defaults below
encode what was actually confirmed, and `unconfirmed` marks the ones to revisit
once contracts are in hand — a guess dressed as a constant is how a wrong
address ends up looking authoritative.

Pure: stdlib only. No network, no credentials, no DB — the arbitration rules are
unit-testable without a single vendor account.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from app.services.address_service import (
    SEVERITY_ERROR, address_key, normalize_country, normalize_postal,
    normalize_region, validate_address,
)

# ── Canonical provider verdicts ──────────────────────────────────────────────
VERIFIED = "verified"        # confirmed, at most a formatting change
CORRECTED = "corrected"      # returned a materially different address
AMBIGUOUS = "ambiguous"      # several candidates; provider would not choose
UNVERIFIED = "unverified"    # no match found
ERROR = "error"              # the call failed

_STATUS_WEIGHT = {
    VERIFIED: 1.00,
    CORRECTED: 0.90,   # useful, but a rewrite deserves slightly less pull
    AMBIGUOUS: 0.35,   # counts for something; must never win alone
    UNVERIFIED: 0.0,
    ERROR: 0.0,
}

# (provider, country) -> trust. 1.0 = certified by that country's postal
# authority for this purpose.
PROVIDER_TRUST: dict[str, dict[str, float]] = {
    "loqate":   {"US": 1.0, "CA": 1.0},
    "melissa":  {"US": 1.0, "CA": 1.0},
    "postgrid": {"US": 1.0, "CA": 1.0},
    # CASS confirmed; Canada Post SERP certification was NOT confirmed when these
    # weights were set. Canadian results are therefore discounted rather than
    # dropped — raise this to 1.0 once the contract confirms SERP.
    "smarty":   {"US": 1.0, "CA": 0.6},
}
UNCONFIRMED_CERTIFICATION = {("smarty", "CA")}

DEFAULT_TRUST = 0.5          # an unknown provider still counts, but never wins alone
CONTESTED_MARGIN = 0.15      # top-two closer than this -> force a human look

OUTCOME_UNCHANGED = "unchanged"
# Only FILLS fields the source left blank. Distinguished from a correction
# because the risk is different: completing a missing postal code adds
# information, overwriting an existing one contradicts the source. An analyst
# reviews both, but should be able to see which is which at a glance.
OUTCOME_COMPLETED = "completed"
OUTCOME_CORRECTED = "corrected"
# Providers answered and none could match it — the address is not real. Reported
# separately from "we could not reach anyone", because only one of those is a
# statement about the data.
OUTCOME_INVALID = "invalid"
OUTCOME_UNRESOLVED = "unresolved"

FIELDS = ("line1", "line2", "city", "region", "postal", "country")
CHANGE_NONE = "unchanged"
CHANGE_FILLED = "filled"      # source was blank
CHANGE_CHANGED = "changed"    # source had a different value


def field_changes(original: dict, recommended: dict) -> dict:
    """Per-field diff, split by whether a value was ADDED or OVERWRITTEN."""
    co, cr = canonical(original), canonical(recommended)
    out: dict = {}
    for f in FIELDS:
        was, now = _tidy(original.get(f)), _tidy(recommended.get(f))
        if not now:
            kind = CHANGE_NONE
        elif not was:
            kind = CHANGE_FILLED
        elif str(co[f]).casefold() == str(cr[f]).casefold():
            kind = CHANGE_NONE          # cosmetic only — not worth flagging
        else:
            kind = CHANGE_CHANGED
        out[f] = {"from": was, "to": now, "kind": kind}
    return out


def trust_for(provider: str, country: Optional[str]) -> float:
    return PROVIDER_TRUST.get((provider or "").lower(), {}).get(
        (country or "").upper(), DEFAULT_TRUST)


def canonical(addr: dict) -> dict:
    """The comparable form of an address.

    Providers differ cosmetically — "ST" vs "St", "M5H2N2" vs "M5H 2N2" — and
    counting those as disagreement would make every address look contested. So
    comparison happens on the normalised form while the provider's own text is
    kept for display.
    """
    country = normalize_country(addr.get("country")) or (addr.get("country") or "")
    region = normalize_region(addr.get("region"), country or None) or (addr.get("region") or "")
    postal = normalize_postal(addr.get("postal"), country or None) or (addr.get("postal") or "")
    return {
        "line1": _tidy(addr.get("line1")), "line2": _tidy(addr.get("line2")),
        "city": _tidy(addr.get("city")), "region": str(region).strip().upper(),
        "postal": str(postal).strip().upper(), "country": str(country).strip().upper(),
    }


def _tidy(v: Any) -> str:
    return " ".join(str("" if v is None else v).split()).strip()


def _same(a: dict, b: dict) -> bool:
    ca, cb = canonical(a), canonical(b)
    return all(str(ca[k]).casefold() == str(cb[k]).casefold() for k in ca)


def arbitrate(original: dict, responses: list[dict]) -> dict:
    """Pick the best address from several provider answers.

    ``responses``: [{provider, status, address?, provider_code?, raw?}]

    Returns the recommendation, the outcome, and the full evidence trail. The
    result is a pure function of its inputs — same responses, same winner, every
    time — so a decision saved against it stays meaningful.
    """
    country = normalize_country(original.get("country"))
    usable = [r for r in responses
              if (r.get("status") in _STATUS_WEIGHT
                  and r.get("status") not in (ERROR, UNVERIFIED)
                  and r.get("address"))]

    evidence = [{
        "provider": r.get("provider"),
        "status": r.get("status"),
        "address": r.get("address"),
        "provider_code": r.get("provider_code"),
        "trust": trust_for(r.get("provider", ""), country),
        "certification_unconfirmed":
            ((r.get("provider") or "").lower(), (country or "").upper())
            in UNCONFIRMED_CERTIFICATION,
    } for r in responses]

    if not usable:
        # Providers that answered "no match" are a verdict ON THE DATA; providers
        # that errored are a verdict on the call. Only the first means invalid.
        said_no = [r.get("provider") for r in responses
                   if r.get("status") == UNVERIFIED]
        offline = validate_address(original)
        invalid = bool(said_no)
        return {
            "outcome": OUTCOME_INVALID if invalid else OUTCOME_UNRESOLVED,
            "recommended": None, "recommended_canonical": None,
            "confidence": 0.0, "agreement": 0.0,
            "contested": True, "unanimous": bool(said_no) and len(said_no) > 1,
            "backers": [], "dissenters": [r.get("provider") for r in responses],
            "alternatives": [], "options": [], "field_changes": {},
            "evidence": evidence,
            "offline_issues": offline["issues"],
            "reason": (
                f"Not a deliverable address — {', '.join(said_no)} found no match."
                + (f" Offline checks also flag: "
                   f"{', '.join(i['code'] for i in offline['issues'] if i['severity'] == SEVERITY_ERROR)}."
                   if any(i["severity"] == SEVERITY_ERROR for i in offline["issues"]) else "")
                if invalid else
                "No provider could be reached — validation did not run."),
        }

    # Group by canonical form so cosmetic differences do not split a consensus.
    groups: dict[str, dict] = {}
    for r in usable:
        addr = r["address"]
        k = address_key(canonical(addr))
        g = groups.setdefault(k, {"address": addr, "score": 0.0, "backers": [],
                                  "statuses": []})
        g["score"] += (trust_for(r.get("provider", ""), country)
                       * _STATUS_WEIGHT.get(r.get("status"), 0.0))
        g["backers"].append(r.get("provider"))
        g["statuses"].append(r.get("status"))

    # Deterministic ordering: score first, then backer count, then the canonical
    # key. Without the last two a tie would resolve by dict order, and the same
    # inputs could produce different winners across runs.
    ranked = sorted(groups.items(),
                    key=lambda kv: (-kv[1]["score"], -len(kv[1]["backers"]), kv[0]))
    top_key, top = ranked[0]
    runner_score = ranked[1][1]["score"] if len(ranked) > 1 else 0.0

    total = sum(trust_for(r.get("provider", ""), country)
                * _STATUS_WEIGHT.get(r.get("status"), 0.0) for r in usable) or 1.0
    confidence = round(top["score"] / total, 4)
    agreement = round(len(top["backers"]) / len(usable), 4)
    unanimous = len(top["backers"]) == len(usable) and len(usable) > 1
    contested = (len(ranked) > 1
                 and (top["score"] - runner_score) < CONTESTED_MARGIN)

    # A consensus can still be wrong for Oracle. Run the offline checks over the
    # WINNER so a postal/region contradiction is caught even when every provider
    # agreed — and so a recommendation is never presented as safe on the strength
    # of agreement alone.
    check = validate_address(top["address"])
    hard = [i for i in check["issues"] if i["severity"] == SEVERITY_ERROR]

    changes = field_changes(original, top["address"])
    kinds = {c["kind"] for c in changes.values()}
    unchanged = _same(original, top["address"])
    outcome = (OUTCOME_UNCHANGED if unchanged
               else OUTCOME_CORRECTED if CHANGE_CHANGED in kinds
               else OUTCOME_COMPLETED)
    filled = [f for f, c in changes.items() if c["kind"] == CHANGE_FILLED]
    changed = [f for f, c in changes.items() if c["kind"] == CHANGE_CHANGED]

    if unchanged:
        reason = f"{len(top['backers'])} of {len(usable)} providers confirmed the address as-is."
    elif outcome == OUTCOME_COMPLETED:
        reason = (f"Source was incomplete — {', '.join(filled)} filled in by "
                  f"{', '.join(top['backers'])}. Nothing existing was overwritten.")
    elif unanimous:
        reason = f"All {len(usable)} providers returned the same correction."
    elif contested:
        reason = (f"Providers disagree — {', '.join(top['backers'])} back this, "
                  f"{', '.join(ranked[1][1]['backers'])} return something different.")
    else:
        reason = (f"{', '.join(top['backers'])} agree — {', '.join(changed)} "
                  f"corrected ({int(confidence * 100)}% of weighted support).")
    if hard:
        reason += " Winner still fails an offline check — review before applying."

    # Every distinct answer, ranked, with the original as an explicit choice. The
    # analyst approves one, edits one, or keeps what they had — so the screen has
    # to offer all four vendors' suggestions, not just the winner.
    options = [{"source": "recommended", "address": top["address"],
                "backers": top["backers"], "score": round(top["score"], 4),
                "field_changes": changes}]
    for k, v in ranked[1:]:
        options.append({"source": "alternative", "address": v["address"],
                        "backers": v["backers"], "score": round(v["score"], 4),
                        "field_changes": field_changes(original, v["address"])})
    options.append({"source": "original", "address": original, "backers": [],
                    "score": 0.0, "field_changes": {}})

    return {
        "outcome": outcome,
        "field_changes": changes,
        "filled_fields": filled,
        "changed_fields": changed,
        "options": options,
        "recommended": top["address"],
        "recommended_canonical": canonical(top["address"]),
        "confidence": confidence,
        "agreement": agreement,
        "unanimous": unanimous,
        # Contested, or a winner that fails our own checks, must reach a human
        # regardless of how confident the arithmetic looks.
        "contested": bool(contested or hard),
        "backers": top["backers"],
        "dissenters": [p for g in (v for k, v in ranked if k != top_key)
                       for p in g["backers"]],
        "alternatives": [{"address": v["address"], "score": round(v["score"], 4),
                          "backers": v["backers"]}
                         for k, v in ranked[1:]],
        "evidence": evidence,
        "offline_issues": check["issues"],
        "reason": reason,
    }


def needs_review(result: dict) -> bool:
    """Whether this address has to be shown to an analyst.

    Anything that would CHANGE the data, anything contested, and anything
    unresolved. A confirmed-unchanged address is the only case that can pass
    silently — nothing happens to it.
    """
    return (result.get("outcome") != OUTCOME_UNCHANGED
            or bool(result.get("contested")))


def summarize(results: dict[str, dict]) -> dict:
    """Roll up per-address arbitration into review counts."""
    counts = {OUTCOME_UNCHANGED: 0, OUTCOME_COMPLETED: 0, OUTCOME_CORRECTED: 0,
              OUTCOME_INVALID: 0, OUTCOME_UNRESOLVED: 0}
    contested = unanimous = 0
    for r in results.values():
        counts[r.get("outcome", OUTCOME_UNRESOLVED)] = \
            counts.get(r.get("outcome", OUTCOME_UNRESOLVED), 0) + 1
        contested += 1 if r.get("contested") else 0
        unanimous += 1 if r.get("unanimous") else 0
    return {
        "addresses": len(results),
        "counts": counts,
        "contested": contested,
        "unanimous": unanimous,
        "needs_review": sum(1 for r in results.values() if needs_review(r)),
    }


def arbitrate_many(originals: dict[str, dict],
                   responses_by_key: dict[str, list[dict]]) -> dict:
    """Arbitrate a whole deduplicated batch. Keys are ``address_key`` values."""
    results = {k: arbitrate(a, responses_by_key.get(k, []))
               for k, a in originals.items()}
    return {"results": results, "summary": summarize(results)}
