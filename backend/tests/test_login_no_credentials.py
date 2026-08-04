"""The sign-in page must not print an account and its password.

It carried a panel reading "Default: admin@trinamix.com / admin123" under the
form, on a page that is public by definition — anyone who can reach the site can
read it, and the site is on the open internet.

The hint was only half of it. The same credentials were the form's INITIAL STATE,
so removing the panel alone would have moved the disclosure rather than ended it:
the string still ships in the JS bundle, view-source still shows it, and the form
still submits it on a stray Enter. Both are gone.

This is a source-reading test because there is no JS runtime here — the same
approach as test_hook_order.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
_LOGIN = _FRONTEND / "pages" / "LoginPage.tsx"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_login_page_prints_no_password():
    src = _LOGIN.read_text(encoding="utf-8")
    check("the default password is gone", "admin123" not in src)
    check("and the hint panel with it", "Default:" not in src)


def test_the_form_does_not_arrive_pre_filled():
    """Removing the panel and leaving the fields populated moves the disclosure
    into the bundle instead of ending it."""
    src = _LOGIN.read_text(encoding="utf-8")
    check("email starts empty", 'useState("")' in src)
    check("no account is seeded", "admin@trinamix.com" not in src)


def test_no_other_screen_prints_it_either():
    """One page was fixed; the string must not survive somewhere else in the app.
    node_modules is excluded — this is about what we ship, not what we depend on.
    """
    offenders = []
    for p in _FRONTEND.rglob("*.ts*"):
        if "node_modules" in str(p):
            continue
        if "admin123" in p.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(p.relative_to(_FRONTEND)))
    check("the password appears nowhere in the frontend", not offenders,
          f"still in {offenders}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall login credential checks passed")
