"""End-to-end smoke tests covering the Project (engagement) → Conversion split."""
import os
import sys
from pathlib import Path

# Make the app importable when running pytest from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Use isolated test DB so we don't touch the dev one
os.environ["DATABASE_URL"] = "sqlite:///./test_workbench.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.seed import run_seed  # noqa: E402

# Initialise DB + seed once for all tests
run_seed()
client = TestClient(app)


def _login() -> str:
    r = client.post(
        "/api/auth/login",
        json={"email": settings.ADMIN_EMAIL, "password": settings.ADMIN_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_login()}"}


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_and_seed():
    headers = _headers()
    # Datasets seeded
    r = client.get("/api/datasets", headers=headers)
    assert r.status_code == 200
    assert any("Demo" in d["name"] for d in r.json())
    # Templates seeded
    r = client.get("/api/fbdi/templates", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) >= 1
    # Engagement project seeded — should have client + go-live + ~10 conversions
    r = client.get("/api/projects", headers=headers)
    assert r.status_code == 200
    projs = r.json()
    assert len(projs) >= 1
    p = projs[0]
    assert p["client"]
    assert p["conversion_count"] >= 9


def test_engagement_lists_conversions():
    """The /api/projects/{id}/conversions endpoint returns objects within
    the engagement, ordered by planned_load_order."""
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]
    r = client.get(f"/api/projects/{project_id}/conversions", headers=headers)
    assert r.status_code == 200
    convs = r.json()
    assert len(convs) >= 9
    # Confirm Item Master is in there and bound to a dataset
    item_conv = next((c for c in convs if c["target_object"] == "Item"), None)
    assert item_conv is not None
    assert item_conv["dataset_id"] is not None
    assert item_conv["template_id"] is not None
    # Order is sorted by planned_load_order
    orders = [c["planned_load_order"] for c in convs]
    assert orders == sorted(orders)


def test_full_conversion_flow():
    """End-to-end conversion lifecycle scoped to a single Conversion object."""
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]

    # Find the fully-bound Item Master conversion
    convs = client.get(f"/api/projects/{project_id}/conversions", headers=headers).json()
    item_conv = next(c for c in convs if c["target_object"] == "Item" and c["dataset_id"])
    cid = item_conv["id"]

    # Suggest mapping
    r = client.post(f"/api/conversions/{cid}/suggest-mapping", headers=headers)
    assert r.status_code == 200, r.text
    suggestions = r.json()
    assert len(suggestions) > 0

    # Approve all suggestions with a real source column
    approved = 0
    for m in suggestions:
        if m["source_column"]:
            r2 = client.put(f"/api/mappings/{m['id']}/approve", headers=headers)
            assert r2.status_code == 200
            approved += 1
    assert approved > 0

    # Cleansing + validation
    assert client.post(f"/api/conversions/{cid}/profile-cleansing", headers=headers).status_code == 200
    assert client.post(f"/api/conversions/{cid}/validate", headers=headers).status_code == 200

    # Generate output
    r = client.post(f"/api/conversions/{cid}/generate-output?fmt=csv", headers=headers)
    assert r.status_code == 200

    # Simulate load
    r = client.post(f"/api/conversions/{cid}/simulate-load", headers=headers)
    assert r.status_code == 200
    assert "passed_count" in r.json()


def test_conversion_crud():
    """Create + update + delete a planned conversion inside the demo engagement."""
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]

    # Create a planning-only conversion (no source file or template yet)
    r = client.post(
        "/api/conversions",
        headers=headers,
        json={
            "project_id": project_id,
            "name": "Test — Trade Compliance Codes",
            "target_object": "Trade Compliance",
            "planned_load_order": 200,
        },
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert r.json()["status"] == "planning"

    # Update it
    r = client.patch(
        f"/api/conversions/{new_id}",
        headers=headers,
        json={"description": "Auto-generated test object", "planned_load_order": 95},
    )
    assert r.status_code == 200
    assert r.json()["planned_load_order"] == 95

    # Cleanup
    r = client.delete(f"/api/conversions/{new_id}", headers=headers)
    assert r.status_code == 200


def test_dependency_impact_uses_conversion_id():
    """The /api/dependencies/impact/{conversion_id} endpoint replaces the old
    project-scoped variant."""
    headers = _headers()
    convs = client.get("/api/conversions", headers=headers).json()
    item_conv = next(c for c in convs if c["target_object"] == "Item" and c["dataset_id"])
    r = client.get(f"/api/dependencies/impact/{item_conv['id']}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "dependencies" in body
    assert "impacts" in body


def test_learning_capture_uses_engagement_project():
    """Learned mappings are tied to the engagement-level Project, not a Conversion."""
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]
    r = client.post(
        "/api/learned-mappings",
        headers=headers,
        json={
            "kind": "column_mapping",
            "category": "Test Category",
            "original_value": "LEGACY_NUM",
            "resolved_value": "ItemNumber",
            "project_id": project_id,
            "captured_from": "Test capture",
        },
    )
    assert r.status_code == 200, r.text
    captured_id = r.json()["id"]

    # Confirm it appears in stats
    r = client.get("/api/learned-mappings/stats", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] >= 1

    # Cleanup
    client.delete(f"/api/learned-mappings/{captured_id}", headers=headers)


def test_approval_teaches_and_replays_on_new_conversion():
    """Approving a mapping on one Item conversion should make a brand-new
    conversion bound to the same dataset+template auto-apply that mapping —
    confidence 1.0, status approved, approved_by 'learning-engine'.
    """
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]

    convs = client.get(f"/api/projects/{project_id}/conversions", headers=headers).json()
    item_conv = next(c for c in convs if c["target_object"] == "Item" and c["dataset_id"])
    cid = item_conv["id"]

    # Run AI suggest on the original conversion and approve one mapping.
    r = client.post(f"/api/conversions/{cid}/suggest-mapping", headers=headers)
    assert r.status_code == 200, r.text
    suggestions = r.json()
    teach = next(m for m in suggestions if m["source_column"])
    assert (
        client.put(f"/api/mappings/{teach['id']}/approve", headers=headers).status_code
        == 200
    )

    # The learning library should now hold a record for this column.
    learned = client.get(
        "/api/learned-mappings",
        headers=headers,
        params={"kind": "column_mapping"},
    ).json()
    assert any(
        lm["target_field"] == teach["target_field_name"]
        and lm["original_value"] == teach["source_column"]
        and lm["target_object"] == "Item"
        for lm in learned
    ), learned

    # Spawn a fresh conversion against the same dataset + template.
    fresh = client.post(
        "/api/conversions",
        headers=headers,
        json={
            "project_id": project_id,
            "name": "Item Master — Replay Cycle 2",
            "target_object": "Item",
            "dataset_id": item_conv["dataset_id"],
            "template_id": item_conv["template_id"],
            "planned_load_order": 999,
        },
    )
    assert fresh.status_code == 200, fresh.text
    fresh_id = fresh.json()["id"]

    try:
        r = client.post(
            f"/api/conversions/{fresh_id}/suggest-mapping", headers=headers
        )
        assert r.status_code == 200, r.text
        replayed = r.json()
        match = next(
            m for m in replayed
            if m["target_field_name"] == teach["target_field_name"]
        )
        assert match["status"] == "approved", match
        assert match["approved_by"] == "learning-engine", match
        assert match["source_column"] == teach["source_column"], match
        assert match["confidence"] == 1.0, match
    finally:
        client.delete(f"/api/conversions/{fresh_id}", headers=headers)


def test_engine_universal_rule_types():
    """Smoke-test each new engine rule type with a representative input."""
    from app.transformations.engine import apply_rule, apply_pipeline

    # CONSTANT — always overwrite
    assert apply_rule("CONSTANT", {"value": "Active"}, "anything") == "Active"
    assert apply_rule("CONSTANT", {"value": "Active"}, None) == "Active"

    # TITLE_CASE
    assert apply_rule("TITLE_CASE", {}, "hello world") == "Hello World"

    # PAD
    assert apply_rule("PAD", {"side": "left", "length": 6, "char": "0"}, "42") == "000042"
    assert apply_rule("PAD", {"side": "right", "length": 5, "char": "*"}, "AB") == "AB***"

    # SUBSTRING
    assert apply_rule("SUBSTRING", {"start": 0, "length": 3}, "ABCDEF") == "ABC"
    assert apply_rule("SUBSTRING", {"start": 2}, "ABCDEF") == "CDEF"

    # REGEX_REPLACE — strip leading zeros
    assert apply_rule("REGEX_REPLACE", {"pattern": r"^0+", "replace": ""}, "00042") == "42"

    # REGEX_EXTRACT — pull capture group
    assert apply_rule(
        "REGEX_EXTRACT", {"pattern": r"ITEM-(\d+)", "group": 1}, "ITEM-1042"
    ) == "1042"

    # ARITHMETIC — multiply by 100 (USD → cents)
    assert apply_rule("ARITHMETIC", {"op": "multiply", "amount": 100}, "12.50") == 1250.0
    assert apply_rule("ARITHMETIC", {"op": "round", "decimals": 2}, "3.14159") == 3.14

    # COALESCE — first non-null
    row = {"a": "", "b": None, "c": "fallback", "d": "later"}
    assert apply_rule("COALESCE", {"columns": ["a", "b", "c", "d"]}, "", row=row) == "fallback"
    assert apply_rule("COALESCE", {"columns": ["a", "b"], "default": "z"}, "", row=row) == "z"

    # CASE_WHEN — multi-branch
    cfg = {
        "branches": [
            {"if_column": "status", "op": "eq", "value": "A", "then": "Active"},
            {"if_column": "status", "op": "eq", "value": "I", "then": "Inactive"},
            {"if_column": "qty", "op": "gt", "value": 100, "then": "Bulk"},
        ],
        "default": "Other",
    }
    assert apply_rule("CASE_WHEN", cfg, "", row={"status": "A", "qty": 5}) == "Active"
    assert apply_rule("CASE_WHEN", cfg, "", row={"status": "X", "qty": 200}) == "Bulk"
    assert apply_rule("CASE_WHEN", cfg, "", row={"status": "X", "qty": 5}) == "Other"

    # COMPUTED — row index from ctx
    out = apply_rule("COMPUTED", {"source": "row_index"}, None, ctx={"row_index": 7})
    assert out == 7
    out = apply_rule("COMPUTED", {"source": "today", "format": "%Y-%m-%d"}, None)
    assert len(out) == 10 and out[4] == "-"

    # CROSSWALK_LOOKUP
    out = apply_rule(
        "CROSSWALK_LOOKUP",
        {"crosswalk": "uom_map", "default": "?"},
        "ea",
        ctx={"crosswalks": {"uom_map": {"ea": "Each", "BOX": "Box"}}},
    )
    assert out == "Each"

    # Pipeline composition: TRIM → UPPERCASE → REGEX_REPLACE → PAD
    out = apply_pipeline(
        [
            {"rule_type": "TRIM", "config": {}},
            {"rule_type": "UPPERCASE", "config": {}},
            {"rule_type": "REGEX_REPLACE", "config": {"pattern": "-", "replace": ""}},
            {"rule_type": "PAD", "config": {"side": "left", "length": 8, "char": "0"}},
        ],
        " item-42 ",
    )
    assert out == "00ITEM42"


def test_rule_preview_endpoint():
    """The dry-run preview endpoint runs a rule pipeline against the
    conversion's dataset and returns source/output pairs."""
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]
    convs = client.get(f"/api/projects/{project_id}/conversions", headers=headers).json()
    item_conv = next(c for c in convs if c["target_object"] == "Item" and c["dataset_id"])
    cid = item_conv["id"]

    # Use the seeded Item dataset; ITEM_NUM is a known column.
    r = client.post(
        f"/api/conversions/{cid}/rules/preview",
        headers=headers,
        json={
            "source_column": "ITEM_NUM",
            "rules": [
                {"rule_type": "REMOVE_HYPHEN", "config": {}},
                {"rule_type": "UPPERCASE", "config": {}},
            ],
            "sample_size": 3,
        },
    )
    assert r.status_code == 200, r.text
    samples = r.json()["samples"]
    assert len(samples) == 3
    for s in samples:
        if s["source"]:
            assert "-" not in str(s["output"])
            assert str(s["output"]) == str(s["output"]).upper()


def test_manual_rule_lands_in_rule_library():
    """A manually-authored TransformationRule should appear in the Rule Library
    (kind='rule') so other conversions can discover it."""
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]
    convs = client.get(f"/api/projects/{project_id}/conversions", headers=headers).json()
    item_conv = next(c for c in convs if c["target_object"] == "Item" and c["dataset_id"])
    cid = item_conv["id"]

    fields = client.get(
        f"/api/fbdi/templates/{item_conv['template_id']}/fields", headers=headers
    ).json()
    field = fields[0]

    r = client.post(
        f"/api/conversions/{cid}/rules",
        headers=headers,
        json={
            "target_field_id": field["id"],
            "source_column": "ITEM_TYPE",
            "rule_type": "CASE_WHEN",
            "rule_config": {
                "branches": [
                    {"if_column": "ITEM_TYPE", "op": "eq", "value": "ACT", "then": "production"},
                    {"if_column": "ITEM_TYPE", "op": "eq", "value": "INACT", "then": "discontinued"},
                ],
                "default": "planning",
            },
            "description": "Map legacy ACT/INACT to new lifecycle codes",
        },
    )
    assert r.status_code == 200, r.text

    library = client.get(
        "/api/learned-mappings",
        headers=headers,
        params={"kind": "rule"},
    ).json()
    assert any(
        lm["rule_type"] == "CASE_WHEN"
        and lm["target_field"] == field["field_name"]
        and lm["target_object"] == "Item"
        for lm in library
    ), library


def test_sales_order_cascade_surfaces_unresolved_item_refs():
    """Simulating a Sales Order load against the seeded data should produce
    'Missing Dependency' errors for every SO row whose ITEM_NUM is absent
    from the Item Master extract — the demo path the Error Traceback drawer
    visualizes. Match comparison must be loose so auto-suggested
    REMOVE_HYPHEN/UPPERCASE transforms don't trigger false positives.
    """
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]
    convs = client.get(f"/api/projects/{project_id}/conversions", headers=headers).json()
    so_conv = next(
        c for c in convs if c["target_object"] == "Sales Order" and c["dataset_id"]
    )
    cid = so_conv["id"]

    # Drive the SO conversion through suggest → approve → cleanse → validate → output.
    suggestions = client.post(
        f"/api/conversions/{cid}/suggest-mapping", headers=headers
    ).json()
    for m in suggestions:
        if m["source_column"]:
            client.put(f"/api/mappings/{m['id']}/approve", headers=headers)
    client.post(f"/api/conversions/{cid}/profile-cleansing", headers=headers)
    client.post(f"/api/conversions/{cid}/validate", headers=headers)
    client.post(f"/api/conversions/{cid}/generate-output?fmt=csv", headers=headers)

    run = client.post(f"/api/conversions/{cid}/simulate-load", headers=headers).json()
    assert run["failed_count"] > 0, run

    errors = client.get(
        f"/api/conversions/{cid}/load-errors", headers=headers
    ).json()
    item_misses = [
        e
        for e in errors
        if e.get("error_category") == "Missing Dependency"
        and e.get("related_dependency") == "Item"
    ]
    # The seed CSV has 61 rows referencing items not in the Item Master extract.
    assert len(item_misses) >= 30, (
        f"Expected ~61 unresolved Item refs, got {len(item_misses)}: "
        f"{item_misses[:3]}"
    )
    # Every Missing-Dependency error must carry the actual reference value
    # (drives the visual chain on the frontend) and the suggested fix.
    for e in item_misses[:5]:
        assert e["reference_value"], e
        assert e["suggested_fix"], e
        assert "no matching record" in (e["error_message"] or "").lower(), e

    # Loose match must not flag legit refs — at least some refs should
    # resolve cleanly. (Other failure categories like UOM may still fire.)
    failed_legit_only = [
        e for e in item_misses
        if (e["reference_value"] or "").startswith("ITM-0")
    ]
    assert failed_legit_only == [], (
        "Legit ITM-0xxx references shouldn't be flagged as missing — "
        "normalize comparison must strip hyphens/case before matching."
    )


def test_reference_standard_propagates_from_master_to_downstream():
    """Saving REMOVE_HYPHEN on Item Master's InventoryItemNumber should
    auto-apply to the Sales Order conversion's InventoryItemNumber output —
    *without* the SO conversion having its own local rule. This is the
    Reference Standard inheritance path: master teaches once, every
    downstream conversion that references the same master inherits it.
    """
    headers = _headers()
    project_id = client.get("/api/projects", headers=headers).json()[0]["id"]
    convs = client.get(f"/api/projects/{project_id}/conversions", headers=headers).json()
    item_conv = next(
        c for c in convs if c["target_object"] == "Item" and c["dataset_id"]
    )
    so_conv = next(
        c for c in convs if c["target_object"] == "Sales Order" and c["dataset_id"]
    )

    # Find the Item Master's InventoryItemNumber field id.
    fields = client.get(
        f"/api/fbdi/templates/{item_conv['template_id']}/fields", headers=headers
    ).json()
    inv_field = next(
        f for f in fields
        if f["field_name"] in ("InventoryItemNumber", "Item Number", "Inventory Item Name")
    )

    # Teach a REMOVE_HYPHEN on the master's key column. The learning service
    # should auto-promote this to a Reference Standard.
    r = client.post(
        f"/api/conversions/{item_conv['id']}/rules",
        headers=headers,
        json={
            "target_field_id": inv_field["id"],
            "source_column": "ITEM_NUM",
            "rule_type": "REMOVE_HYPHEN",
            "rule_config": {},
            "description": "Strip hyphens from item identifiers",
        },
    )
    assert r.status_code == 200, r.text

    # Reference Standard should now exist for Item / InventoryItemNumber.
    standards = client.get(
        "/api/learned-mappings",
        headers=headers,
        params={"kind": "reference_standard"},
    ).json()
    assert any(
        s["target_object"] == "Item"
        and s["target_field"] == inv_field["field_name"]
        and s["rule_type"] == "REMOVE_HYPHEN"
        for s in standards
    ), standards

    # Drive the SO conversion to output (without adding any local rule on
    # its InventoryItemNumber). The inherited standard should fire.
    suggestions = client.post(
        f"/api/conversions/{so_conv['id']}/suggest-mapping", headers=headers
    ).json()
    for m in suggestions:
        if m["source_column"]:
            client.put(f"/api/mappings/{m['id']}/approve", headers=headers)

    preview = client.get(
        f"/api/conversions/{so_conv['id']}/output-preview?limit=5", headers=headers
    ).json()
    # The preview's lineage tells us which rules ran on each target column.
    inv_col = next(
        (c for c in preview["columns"] if "InventoryItemNumber" in c or "Item Number" in c),
        None,
    )
    assert inv_col, f"no item-ref column in SO output; cols={preview['columns']}"
    rules_applied = preview["lineage"][inv_col]["rules"]
    assert any(r["rule_type"] == "REMOVE_HYPHEN" for r in rules_applied), (
        f"Reference Standard didn't propagate. rules on {inv_col} were: {rules_applied}"
    )

    # And the actual cell values should have hyphens stripped.
    for row in preview["rows"][:5]:
        v = str(row.get(inv_col, ""))
        assert "-" not in v, f"hyphen still in SO output cell: {v!r}"

    # The same standard should NOT be re-applied to the master itself —
    # that would create an infinite "double transform" if the rule were
    # not idempotent. Verify the master's lineage has the rule once.
    master_preview = client.get(
        f"/api/conversions/{item_conv['id']}/output-preview?limit=3", headers=headers
    ).json()
    master_inv_col = next(
        (c for c in master_preview["columns"] if c == inv_field["field_name"]),
        None,
    )
    if master_inv_col:
        master_rules = master_preview["lineage"][master_inv_col]["rules"]
        assert sum(1 for r in master_rules if r["rule_type"] == "REMOVE_HYPHEN") == 1, master_rules


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
