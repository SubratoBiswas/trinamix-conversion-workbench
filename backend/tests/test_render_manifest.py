"""render.yaml: every key it sets must be a key the app actually reads.

THE FAILURE THIS CATCHES
------------------------
The manifest set ``SECRET_KEY: generateValue: true``. Nothing in the codebase
reads ``SECRET_KEY`` — the settings model calls it ``JWT_SECRET``, and it is
configured ``extra="ignore"``, so pydantic-settings dropped the generated value
in silence. ``JWT_SECRET`` then fell back to its default,
``"trinamix-local-dev-secret-change-me"``, which is committed to this repository.
Every session token on the live backend was signed with a key anyone reading the
repo could copy, and the manifest was the reason nobody looked: it plainly said a
secret was being generated.

That is CODEBASE_GUIDE §7.1 wearing a different hat — a value that says something
and no code that asks — and configuration is where it is hardest to see, because
a misspelled key produces no error anywhere. `extra="ignore"` is right for the
model (Render injects PORT, RENDER_GIT_COMMIT and others the app must not choke
on) and it means the mistake cannot surface at runtime. So it surfaces here.

Pure: stdlib + PyYAML. PyYAML ships with uvicorn[standard], which is a hard
dependency, so it is imported rather than skipped — a config guard that skips
itself is the same silence in a new place.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml                                                      # noqa: E402

from app.config import Settings                                  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST = _ROOT / "render.yaml"

# Keys Render injects or that belong to the build rather than to Settings.
# VITE_* are read by Vite at build time and never reach pydantic.
_NOT_APP_SETTINGS = {"PORT", "PYTHON_VERSION", "NODE_VERSION"}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _manifest() -> dict:
    check("render.yaml exists", _MANIFEST.exists(), f"looked at {_MANIFEST}")
    return yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))


def _services() -> dict:
    return {s["name"]: s for s in _manifest()["services"]}


def _backend() -> dict:
    svc = [s for s in _manifest()["services"] if s.get("runtime") == "docker"]
    check("exactly one backend service", len(svc) == 1, f"got {len(svc)}")
    return svc[0]


def _static() -> dict:
    svc = [s for s in _manifest()["services"] if s.get("runtime") == "static"]
    check("exactly one static site", len(svc) == 1, f"got {len(svc)}")
    return svc[0]


def test_every_backend_env_key_is_a_real_setting():
    """The SECRET_KEY test. A key the app does not read is not configuration —
    it is a note to the reader that happens to look like configuration."""
    known = set(Settings.model_fields)
    unknown = []
    for var in _backend().get("envVars", []):
        key = var.get("key", "")
        if key in _NOT_APP_SETTINGS or key.startswith(("VITE_", "RENDER_")):
            continue
        if key not in known:
            unknown.append(key)
    check("no env key is silently ignored by Settings", not unknown,
          f"not fields on Settings: {unknown} — known: {sorted(known)}")


def test_the_signing_key_is_set_and_is_the_name_the_app_reads():
    """Named explicitly because getting this one wrong is not a typo, it is an
    authentication bypass: the fallback value is in the repository."""
    keys = {v.get("key") for v in _backend().get("envVars", [])}
    check("JWT_SECRET is in the manifest", "JWT_SECRET" in keys, f"got {sorted(keys)}")
    check("SECRET_KEY is gone", "SECRET_KEY" not in keys)
    check("and JWT_SECRET is what auth_service signs with",
          "JWT_SECRET" in Settings.model_fields)


def test_no_credential_is_written_into_the_manifest_in_plain_text():
    """A password with a literal `value:` is committed to git. The seed admin's
    was, as `admin123`."""
    for var in _backend().get("envVars", []):
        key = (var.get("key") or "").upper()
        if not any(w in key for w in ("PASSWORD", "SECRET", "TOKEN", "KEY")):
            continue
        check(f"{key} carries no literal value", "value" not in var,
              f"got value={var.get('value')!r}")


def test_the_static_site_carries_the_spa_rewrite():
    """Without it every refresh and every shared deep link returns a bare 404.

    NOTE this asserts the MANIFEST says so, which is not the same as the live
    site doing it — the services were created by hand and are not governed by
    this Blueprint. The header comment in render.yaml says so at the top, and
    the next test holds that warning in place.
    """
    routes = _static().get("routes") or []
    spa = [r for r in routes
           if r.get("source") == "/*" and r.get("destination") == "/index.html"]
    check("there is a /* rule", spa, f"got {routes}")
    check("and it is a rewrite, not a redirect", spa[0].get("type") == "rewrite",
          f"got {spa[0].get('type')!r} — a redirect changes the address bar")


def test_the_manifest_says_out_loud_that_it_does_not_govern_the_live_services():
    """The reason this entry sat open reading as done. A correct rule in a file
    that never applied is worse than no rule, because it answers the question."""
    text = _MANIFEST.read_text(encoding="utf-8").lower()
    for phrase in ("blueprint", "by hand", "dashboard"):
        check(f"the header mentions {phrase!r}", phrase in text)


def test_the_service_names_match_the_hosts_the_app_actually_uses():
    """`trinamix-backend` and `trinamix-frontend` existed nowhere. A manifest
    naming services that do not exist cannot be linked without someone first
    working out which entry means which."""
    names = set(_services())
    check("the backend is named for its host", "trinamix-conversion-backend" in names,
          f"got {sorted(names)}")
    check("the static site is named for its host", "tx-conversion-workbench" in names,
          f"got {sorted(names)}")
    api = [v.get("value") for v in _static().get("envVars", [])
           if v.get("key") == "VITE_API_URL"]
    check("the frontend points at the backend", api, "VITE_API_URL is not set")
    check("and at the service this manifest names",
          _backend()["name"] in api[0], f"VITE_API_URL={api[0]!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall render manifest checks passed")
