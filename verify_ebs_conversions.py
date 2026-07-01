#!/usr/bin/env python3
"""
Verify every conversion in a project end-to-end against the LIVE backend,
sourcing from Oracle EBS.

For each conversion it checks, in order:
  1. source-columns   -> live EBS columns resolve (count > 0)
  2. suggest-mapping   -> AI mapping runs and auto-maps some fields
  3. generate-output   -> FBDI artifact is produced (column_count > 0)
  4. download-output   -> artifact downloads (HTTP 200)

Usage (PowerShell):
    $env:TX_EMAIL="admin@..."; $env:TX_PASSWORD="..."; python verify_ebs_conversions.py
Or just run it and enter email/password when prompted. The password is never
stored; it's read with getpass and only sent to your own backend to log in.

Optional env vars:
    TX_API      backend base (default https://trinamix-conversion-backend.onrender.com/api)
    TX_PROJECT  project name  (default "Trinamix EBS 2")
"""
import getpass
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("This script needs 'requests'. Install with:  pip install requests")

API = os.environ.get("TX_API", "https://trinamix-conversion-backend.onrender.com/api").rstrip("/")
PROJECT_NAME = os.environ.get("TX_PROJECT", "Trinamix EBS 2")
EMAIL = os.environ.get("TX_EMAIL") or input("Email: ").strip()
PASSWORD = os.environ.get("TX_PASSWORD") or getpass.getpass("Password: ")

s = requests.Session()
s.timeout = 120


def _get(path, **kw):
    return s.get(f"{API}{path}", timeout=120, **kw)


def _post(path, **kw):
    return s.post(f"{API}{path}", timeout=180, **kw)


def main() -> int:
    # ---- login ----
    print(f"\nLogging in to {API} as {EMAIL} ...")
    r = _post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        sys.exit(f"Login failed ({r.status_code}): {r.text[:300]}")
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    # ---- find project ----
    projects = _get("/projects").json()
    proj = next((p for p in projects if p.get("name") == PROJECT_NAME), None)
    if not proj:
        names = ", ".join(p.get("name", "?") for p in projects)
        sys.exit(f"Project '{PROJECT_NAME}' not found. Available: {names}")
    convs = _get(f"/projects/{proj['id']}/conversions").json()
    print(f"Project '{PROJECT_NAME}' — {len(convs)} conversions\n")

    rows = []
    for c in convs:
        cid, name = c["id"], c["name"]
        row = {
            "name": name,
            "table": c.get("ebs_table_hint") or "-",
            "src": 0, "mapped": 0, "targets": 0,
            "out_rows": "-", "out_cols": 0, "dl": "-", "status": "",
        }
        try:
            sc = _get(f"/conversions/{cid}/source-columns")
            row["src"] = len(sc.json().get("columns", [])) if sc.ok else 0

            mp = _post(f"/conversions/{cid}/suggest-mapping")
            if mp.ok:
                maps = mp.json()
                row["targets"] = len(maps)
                row["mapped"] = sum(1 for m in maps if m.get("source_column"))
            else:
                raise RuntimeError(f"suggest-mapping {mp.status_code}: {mp.text[:120]}")

            go = _post(f"/conversions/{cid}/generate-output", params={"fmt": "csv"})
            if go.ok:
                out = go.json()
                row["out_rows"] = out.get("row_count")
                row["out_cols"] = out.get("column_count", 0)
            else:
                raise RuntimeError(f"generate-output {go.status_code}: {go.text[:120]}")

            dl = _get(f"/conversions/{cid}/download-output")
            row["dl"] = dl.status_code

            ok = row["src"] > 0 and row["out_cols"] > 0 and row["dl"] == 200
            row["status"] = "PASS" if ok else "CHECK"
        except Exception as e:  # noqa: BLE001
            row["status"] = f"FAIL: {e}"
        rows.append(row)
        print(f"  [{row['status'][:24]:<24}] {name:<28} table={row['table']:<26} "
              f"src={row['src']:<3} mapped={row['mapped']}/{row['targets']:<3} "
              f"out={row['out_cols']}cols/{row['out_rows']}rows dl={row['dl']}")

    npass = sum(1 for r in rows if r["status"] == "PASS")
    print(f"\n==== {npass}/{len(rows)} conversions PASS ====")
    for r in rows:
        if r["status"] != "PASS":
            print(f"  - {r['name']}: {r['status']}")
    return 0 if npass == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
