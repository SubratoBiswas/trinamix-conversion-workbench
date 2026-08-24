"""Entity resolution split into domain policy + services adapter (Phase 4, slice 4).

The pure duplicate-clustering policy moved to ``app.domain.entity.resolution``; the
best-effort AI adjudication (an LLM/HTTP call) stays in ``app.services.entity_resolution``
and the policy is re-exported there so callers are unchanged. These tests pin the seam
(policy is pure and re-exported by identity; the adapter is NOT in the domain) and
spot-check the deterministic matching.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import entity_resolution as SVC
from app.domain.entity import resolution as DOM


# --- the split itself --------------------------------------------------------

def test_pure_policy_reexported_from_services_by_identity():
    for name in ("find_duplicate_clusters", "detect_identity_fields", "_str_sim", "_norm"):
        assert getattr(SVC, name) is getattr(DOM, name), name


def test_the_llm_adapter_stays_in_services_and_is_not_in_the_domain():
    assert hasattr(SVC, "ai_adjudicate_clusters")          # the I/O piece is still callable here
    assert not hasattr(DOM, "ai_adjudicate_clusters")      # ...but never entered the pure domain


def test_domain_module_is_pure_no_network_imports():
    # the domain policy must not import httpx / the app config at module load
    import inspect
    src = inspect.getsource(DOM)
    assert "import httpx" not in src and "from app.config" not in src


# --- deterministic matching behaviour (via the re-exported policy) -----------

def _frame():
    return pd.DataFrame({
        "Supplier Name": ["Acme Inc", "Acme, Inc.", "ACME INCORPORATED", "Zeta Corp"],
        "Tax ID": ["12-3456789", "12-3456789", "123456789", "55-5555555"],
        "Address Line 1": ["1 Main St", "1 Main Street", "1 Main St.", "3 Elm"],
        "City": ["Boston", "Boston", "boston", "Miami"],
    })


def test_string_similarity_reflects_name_variants():
    assert SVC._str_sim("Acme Inc", "Acme, Inc.") > SVC._str_sim("Acme Inc", "Zeta Corp")
    assert SVC._str_sim("same", "same") == 1.0


def test_detect_identity_fields_finds_name_and_tax_id():
    fields = SVC.detect_identity_fields(_frame(), "Supplier")
    cols = {f["column"] for f in fields}
    assert "Supplier Name" in cols and "Tax ID" in cols


def test_find_duplicate_clusters_groups_the_acme_variants():
    result = SVC.find_duplicate_clusters(_frame(), "Supplier", threshold=0.86)
    clusters = result.get("clusters") or []
    # the three Acme spellings should land in one cluster; Zeta stays out
    sizes = sorted(len(c["members"]) for c in clusters)
    assert sizes and max(sizes) >= 3


def test_find_duplicate_clusters_empty_frame_is_safe():
    result = SVC.find_duplicate_clusters(pd.DataFrame(), "Supplier")
    assert (result.get("clusters") or []) == []
