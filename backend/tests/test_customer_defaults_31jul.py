"""Nine issues on the 31-Jul list, two causes.

The list reads like nine separate bugs, and every one of them is the analyst saying
the same sentence: I set this in the UI, the screen agrees, the mapping sheet agrees,
the FBDI file does not.

    row 29  Batch Identifier … Clicked on Keep Blank and approved, it reflects only
            on the UI. The output file still shows the earlier default value.
    row 30  Customer Account Source System … default NETSUITE … does not reflect in
            the downloaded output file.
    row 31  Role Type … set … for both target sheets, however it only reflects in the
            HZ_IMP_ACCTCONTACTS_T sheet and not in HZ_IMP_CONTACTROLES.
    row 32  Relationship Type … default CONTACT … does not reflect in
            HZ_IMP_ACCOUNTRELS and HZ_IMP_RELSHIPS_T.
    row 33  Relationship Code -> CONTACT_OF … does not reflect in output.
    row 34  Insert Update Indicator … should only reflect in the
            RA_CUSTOMER_PROFILES_INT_ALL sheet.
    row 35  Party Original System -> NETSUITE … does not reflect.
    row 37  Account Site Source System: NETSUITE … appears blank in the output file.
    (row 3, 27-Jul, same sentence: Include in Credit Check, Credit Hold, Send Dunning
     Letters, Send Statement.)

CAUSE ONE — the generated linkage overwrote the person.
``customer_structure_service.apply_to_frame`` fills the columns Oracle needs and no
source system supplies: Batch Identifier, Party Original System, Customer Account
Source System, Account Site Source System. It wrote all of them UNCONDITIONALLY. Glue
is a fallback for what the analyst has not filled in; it was behaving as an override.
Row 37 is the sharpest case: the level was hardcoded to "account" for every sheet, and
the account branch writes the site key to EMPTY STRING — so on the two sheets where
the site key is the whole point it was blanked on every row.

CAUSE TWO — a default written onto zero rows.
An optional interface emits data only when a real source column is mapped into it,
which is right (a naive fan-out put 5,599 rows of pure glue into all 19 sheets). But
"content" was defined as a SOURCE COLUMN, so a sheet whose entire contribution is
constants was written headers-only. That is rows 31/32/33 exactly: Role Type reached
HZ_IMP_ACCTCONTACTS_T, which has mapped columns, and not HZ_IMP_CONTACTROLES, which
does not.

And the scope, from both ends at once: the transformed frame is keyed by FIELD NAME,
so a per-sheet decision could not be expressed at all — row 31 is one sheet too few
and row 34 one sheet too many, in the same mechanism.

Pure: stdlib + pandas + the two services. No database.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd                                                    # noqa: E402
from app.services.customer_structure_service import (                  # noqa: E402
    apply_to_frame, level_for_sheet,
)
from app.services.output_service import (                              # noqa: E402
    analyst_default, analyst_keeps_blank, person_set_default,
)
from app.services.learning_service import sheet_allowed                # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class M:
    """A MappingSuggestion, as far as these rules are concerned."""
    def __init__(self, status="suggested", default_value=None, source_column=None,
                 approved_by=None):
        self.status = status
        self.default_value = default_value
        self.source_column = source_column
        self.approved_by = approved_by


class L:
    def __init__(self, sheets=None, exclude_sheets=None):
        self.sheets = sheets or []
        self.exclude_sheets = exclude_sheets or []


PERSON = "tejaswini@nextpower.com"


# ── The rule that decides whether a constant is the analyst's ───────────────
def test_a_person_typing_a_default_is_a_decision():
    check("approved default counts",
          analyst_default(M("approved", "NETSUITE", approved_by=PERSON)) == "NETSUITE")
    check("overridden counts too",
          analyst_default(M("overridden", "CONTACT", approved_by=PERSON)) == "CONTACT")


def test_an_engine_default_is_a_decision_too_because_authorship_is_provenance():
    """CHANGED 05-Aug, and this is the reason.

    This test used to assert the opposite — that `analyst_default` returns None for
    a row approved by "learning-engine". That rule shipped a Customer file
    contradicting its own mapping screen:

        Party Original System                    screen NETSUITE  file LEGACY
        Customer Account Source System Reference screen LEGACY_SYSTEM
                                                          file NXT000001_C1
        Account Site Source System               screen NETSUITE  file LEGACY

    All three approved, dated 05-Aug, `approved_by="learning-engine"`. Not a person
    -> not a default -> never entered the protected set -> the linkage glue
    overwrote all three. The twelve other constants on the same file were right,
    only because the glue does not touch those columns.

    The rule now is the one the architecture states and the analyst restated twice:
    newest wins, authorship is provenance. The old fear — a seeded row outranking a
    later correction — is answered by the one-row store and by date, not by who
    signed it.

    What a row still has to do is unchanged: say something, and be approved."""
    check("an engine-approved default IS a default now",
          analyst_default(M("approved", "NETSUITE", approved_by="learning-engine"))
          == "NETSUITE")
    check("so is one with no recorded approver",
          analyst_default(M("approved", "NETSUITE")) == "NETSUITE")
    check("an unapproved default still does not ship",
          analyst_default(M("suggested", "NETSUITE", approved_by=PERSON)) is None)
    check("a blank default is still not a default",
          analyst_default(M("approved", "   ", approved_by=PERSON)) is None)


def test_switching_an_optional_interface_on_still_takes_a_person():
    """The other half, and why the change above is safe.

    Two questions used to share one answer. WHAT VALUE a column carries no longer
    asks who typed it. Whether an OPTIONAL child table emits rows AT ALL still
    does — an engine-seeded constant must not be able to switch one back on, or the
    naive fan-out that put 5,599 rows of pure linkage glue into all 19 sheets
    returns through the back door.

    `person_set_default` is that narrow rule, stated on its own so widening the
    wide one cannot move it by accident."""
    check("a person's constant switches the sheet on",
          person_set_default(M("approved", "CONTACT", approved_by=PERSON)) == "CONTACT")
    check("the engine's does not",
          person_set_default(M("approved", "CONTACT", approved_by="learning-engine")) is None)
    check("nor does one with no approver",
          person_set_default(M("approved", "CONTACT")) is None)
    check("and an unapproved one still does not",
          person_set_default(M("suggested", "CONTACT", approved_by=PERSON)) is None)


def test_keep_blank_is_not_applicable_with_the_default_cleared():
    check("keep blank", analyst_keeps_blank(M("not_applicable", None, approved_by=PERSON)))
    # not_applicable WITH a default is the opposite instruction — populate with this
    # constant (Invoice Match Option = Receipt) — so the cleared default matters.
    check("not_applicable + default is not keep-blank",
          analyst_keeps_blank(M("not_applicable", "Receipt", approved_by=PERSON)) is False)
    check("an engine not_applicable is not a decision",
          analyst_keeps_blank(M("not_applicable", None, approved_by="learning-engine")) is False)


# ── Cause one: the glue must not overwrite the person ───────────────────────
def _frame():
    return pd.DataFrame([{
        "*Batch Identifier": "", "Party Original System": "",
        "Party Original System Reference": "",
        "Customer Account Source System": "", "Customer Account Source System Reference": "",
        "Account Site Source System": "", "Account Site Source System Reference": "",
    }])


def test_the_glue_still_fills_what_the_analyst_left_alone():
    """The fix must not disable the linkage — no source system supplies these."""
    fr = _frame()
    apply_to_frame(fr, source_system="NETSUITE", batch_id="900001", ref=["1001"],
                   sheet_name="HZ_IMP_PARTIES_T")
    r = fr.iloc[0]
    check("batch filled", r["*Batch Identifier"] == "900001")
    check("party system filled", r["Party Original System"] == "NETSUITE")
    check("party reference filled", r["Party Original System Reference"] == "1001")


def test_keep_blank_on_batch_identifier_survives_the_glue():
    """Row 29. Keep blank cleared it; the glue wrote the batch number back."""
    fr = _frame()
    apply_to_frame(fr, source_system="NETSUITE", batch_id="900001", ref=["1001"],
                   sheet_name="HZ_IMP_PARTIES_T", protected={"*Batch Identifier"})
    check("batch stays blank", fr.iloc[0]["*Batch Identifier"] == "")
    check("and the rest is still filled", fr.iloc[0]["Party Original System"] == "NETSUITE")


def test_an_analyst_default_survives_the_glue():
    """Rows 30 and 35. The analyst's value is the deliverable."""
    fr = _frame()
    fr.loc[0, "Party Original System"] = "NETSUITE_UI"
    fr.loc[0, "Customer Account Source System"] = "NETSUITE_UI"
    apply_to_frame(fr, source_system="DNB", batch_id="900001", ref=["1001"],
                   sheet_name="HZ_IMP_ACCOUNTS_T",
                   protected={"Party Original System", "Customer Account Source System"})
    r = fr.iloc[0]
    check("party system kept", r["Party Original System"] == "NETSUITE_UI")
    check("account system kept", r["Customer Account Source System"] == "NETSUITE_UI")
    check("unprotected linkage still generated",
          r["Customer Account Source System Reference"] == "1001")


def test_the_site_key_is_no_longer_blanked_on_the_site_sheets():
    """Row 37, and the sharpest of the nine: this one did not merely fail to write
    the analyst's value, it actively wrote empty string over it.

    ``apply_to_frame`` was called with a hardcoded level="account" for EVERY sheet,
    and the account branch blanks Account Site Source System and its Reference. On
    HZ_IMP_ACCTSITES_T and HZ_IMP_ACCTSITEUSES_T the site key is the whole point.
    """
    check("account sites are a site-level sheet",
          level_for_sheet("HZ_IMP_ACCTSITES_T") == "site")
    check("so are their uses", level_for_sheet("HZ_IMP_ACCTSITEUSES_T") == "site")
    check("a sheet whose level is not in evidence keeps the caller's",
          level_for_sheet("HZ_IMP_PARTIES_T") is None)

    fr = _frame()
    apply_to_frame(fr, source_system="NETSUITE", batch_id="900001", ref=["1001"],
                   level="account", sheet_name="HZ_IMP_ACCTSITES_T")
    r = fr.iloc[0]
    check("site system populated", r["Account Site Source System"] == "NETSUITE")
    check("site reference populated", r["Account Site Source System Reference"] == "1001")

    # And the account-level blanking is still correct where it belongs.
    fr2 = _frame()
    apply_to_frame(fr2, source_system="NETSUITE", batch_id="900001", ref=["1001"],
                   level="account", sheet_name="HZ_IMP_ACCOUNTS_T")
    check("still blank at account level",
          fr2.iloc[0]["Account Site Source System"] == "")


def test_a_protected_site_key_is_not_blanked_either():
    """Belt and braces: even at account level, a column the analyst decided is
    never written — including with empty string."""
    fr = _frame()
    fr.loc[0, "Account Site Source System"] = "NETSUITE"
    apply_to_frame(fr, source_system="DNB", batch_id="900001", ref=["1001"],
                   level="account", sheet_name="HZ_IMP_ACCOUNTS_T",
                   protected={"Account Site Source System"})
    check("kept", fr.iloc[0]["Account Site Source System"] == "NETSUITE")


def test_protection_matches_headers_loosely():
    """Sheets carry Oracle's friendly labels with required markers — '*Batch
    Identifier' — while field names do not. A protection that missed on a '*' would
    be no protection at all."""
    fr = _frame()
    apply_to_frame(fr, source_system="NETSUITE", batch_id="900001", ref=["1001"],
                   sheet_name="HZ_IMP_PARTIES_T", protected={"Batch Identifier"})
    check("'Batch Identifier' protects '*Batch Identifier'",
          fr.iloc[0]["*Batch Identifier"] == "")


# ── Cause two, and the per-sheet scope ─────────────────────────────────────
def test_a_constants_only_sheet_now_counts_as_carrying_data():
    """Rows 31/32/33. Seam against the generator, because the predicate is a closure
    over the conversion's mappings: what matters is that a field with an analyst
    constant joins the set that keeps a sheet from being written headers-only."""
    out = (_ROOT / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    check("analyst constants are collected",
          "_analyst_const_field_ids = {" in out)
    # `person_set_default`, not `analyst_default`. The authorship check moved out
    # of `analyst_default` on 05-Aug so an engine-recorded constant could hold its
    # column against the linkage glue. Switching an OPTIONAL interface on is the
    # one question that still wants the narrow rule, so it now names it directly
    # instead of inheriting it — see `test_switching_an_optional_interface_on_...`.
    check("through the person-only rule, named explicitly",
          "if person_set_default(m) is not None" in out)
    check("and they keep the sheet alive",
          "f.id in _mapped_field_ids or f.id in _analyst_const_field_ids" in out)


def test_each_sheets_own_decisions_are_re_applied_to_that_sheet():
    """Row 31 (one sheet too few) and row 34 (one sheet too many) are the same
    defect: the frame is keyed by field name and cannot hold a per-sheet decision."""
    out = (_ROOT / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    check("the frame really is keyed by name",
          "out_cols[tgt.field_name] = col_values" in out)
    check("a per-sheet overlay exists", "def _apply_sheet_decisions(" in out)
    check("and runs inside _finalize", "sdf = _apply_sheet_decisions(sdf, sfields)" in out)
    check("after the control defaults, so the analyst outranks them",
          out.index("effective=_eff_for_sheet(sfields)")
          < out.index("sdf = _apply_sheet_decisions(sdf, sfields)"))


def test_a_default_alongside_a_mapped_column_only_fills_blanks():
    """Overwriting a real mapped value with a fallback constant is the eBOS
    "Address Name shows city in the UI, ships PRIMARY" bug (issue #8). The rule is
    recorded here so the two cannot drift apart."""
    out = (_ROOT / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    check("fill-blank-only is derived from having a source column",
          "consts[f.field_name] = (dv, has_src)" in out)


def test_a_default_is_scoped_to_the_sheet_it_was_set_on():
    """Row 34. ``sheets``/``sheet_allowed`` already existed and defaults_service never
    consulted them — the scope was recorded and ignored, which is this codebase's
    signature failure. Asserted through the real resolver."""
    only_profiles = L(sheets=["RA_CUSTOMER_PROFILES_INT_ALL"])
    check("applies where it was set",
          sheet_allowed(only_profiles, "RA_CUSTOMER_PROFILES_INT_ALL") is True)
    for other in ("HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T", "HZ_IMP_ACCTSITES_T"):
        check(f"and nowhere else ({other})", sheet_allowed(only_profiles, other) is False)
    unscoped = L()
    check("an unscoped default still reaches every sheet",
          sheet_allowed(unscoped, "HZ_IMP_PARTIES_T") is True)

    svc = (_ROOT / "app" / "services" / "defaults_service.py").read_text(encoding="utf-8")
    check("defaults_service now carries the scope out", '"scopes": scopes' in svc)
    out = (_ROOT / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    check("and the generator filters per sheet", "def _eff_for_sheet(" in out)
    check("using the one shared resolver",
          "from app.services.learning_service import sheet_allowed" in out)

    lrn = (_ROOT / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    check("a captured default records its sheet",
          "sheets=[_sheet_name] if _sheet_name else None" in lrn)


def test_capturing_a_default_still_works_when_the_sheet_is_unknown():
    """Scope is a refinement. A default whose sheet cannot be resolved must still be
    captured — unscoped, exactly as before — rather than lost."""
    lrn = (_ROOT / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    check("sheet lookup cannot raise", "except Exception:  # noqa: BLE001 — scope is a refinement" in lrn)
    store = (_ROOT / "app" / "services" / "mapping_store.py").read_text(encoding="utf-8")
    check("and no sheet means unscoped", "sheets=list(sheets or [])" in store)
    check("which the store reads as every sheet",
          "if not only and not never:" in store)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall 31-Jul customer default checks passed")

# ── Role Type set on TWO sheets (CW 31-Jul, row 30) ─────────────────────────
def test_setting_one_default_on_a_second_sheet_does_not_unset_the_first():
    """Analyst: "Role Type target field has been set a default value as CONTACT for
    both target sheets, however it only reflects in [one]."

    A default carries the sheet it was set on, and for the single-answer kinds the
    row is found by (object, field, client, source) WITHOUT the sheet — so the second
    save overwrote sheets=["HZ_IMP_CONTACTS_T"] with sheets=["HZ_IMP_RELSHIPS_T"] and
    silently cancelled the first. Both saves reported approved. The screen showed two
    defaults and the file carried one, which is the shape that costs a whole
    regenerate-and-read cycle to notice.
    """
    import asyncio
    import app.models.learned as learned_model
    from app.services import mapping_store

    rows = []

    class _Row:
        def __init__(self, **kw):
            self.sheets = []
            for k, v in kw.items():
                setattr(self, k, v)

        async def set(self, patch):
            for k, v in patch.items():
                setattr(self, k, v)
            return self

        def __getattr__(self, n):
            if n.startswith("__"):
                raise AttributeError(n)
            return None

    class _LM:
        def __init__(self, **kw):
            self.r = _Row(**kw)
            rows.append(self.r)

        async def insert(self):
            return self.r

        @classmethod
        def find(cls, *_q, include_deleted=False):
            class _Q:
                async def to_list(_s):
                    return list(rows)
            return _Q()

    _real, learned_model.LearnedMapping = learned_model.LearnedMapping, _LM
    try:
        async def _go():
            for sheet in ("HZ_IMP_CONTACTS_T", "HZ_IMP_RELSHIPS_T"):
                await mapping_store.record_learning(
                    kind="example_default", category="Default Value",
                    original_value="(default)", resolved_value="CONTACT",
                    target_object="Customer", target_field="Role Type",
                    captured_from="ui", captured_by="analyst", sheets=[sheet])
        asyncio.run(_go())
    finally:
        learned_model.LearnedMapping = _real

    check("it is still ONE learning, not two competing ones", len(rows) == 1,
          f"got {len(rows)}")
    check("and it now covers both sheets",
          set(rows[0].sheets) == {"HZ_IMP_CONTACTS_T", "HZ_IMP_RELSHIPS_T"},
          f"got {rows[0].sheets}")

