""""It says it is downloading, then after many seconds it stops and nothing appears."

Reported against the per-interface FBDI downloads on Project Overview. The .zip
bundle worked; the individual CSV / FBDI Excel links did not.

SIXTY SECONDS WAS THE WHOLE BUG. The axios instance defaults to timeout: 60_000,
and `OutputApi.download` — the per-conversion file download — was the ONLY binary
endpoint that never overrode it. Its siblings all do: the dataset download asks
for 300s, the duplicate export 180s, the mapping export 90s.

A wide supplier interface streams for longer than a minute from a free-tier
instance, and the first request after idle spends ~45s of that cold-starting
before a byte moves. Axios aborts; the browser has already opened a download, so
it shows one running and then writes nothing. Indistinguishable from a dead
button, which is how it was reported.

The .zip bundle was immune because it was deliberately built to generate in the
BACKGROUND and poll, so its final fetch is a fast reuse of a file already on
disk. The per-file path streams the real work inside the request — the same
lesson, one layer down, and the comment in downloadAll had already named it.

Checked as source, the way the other frontend guarantees here are; there is no JS
runtime in this suite.
"""
import re
from pathlib import Path

_API = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "api"


def _client() -> str:
    return (_API / "client.ts").read_text(encoding="utf-8")


def _index() -> str:
    return (_API / "index.ts").read_text(encoding="utf-8")


def test_the_shared_default_is_still_a_minute():
    """The premise. If this ever rises, the reasoning below changes and these
    tests should be re-read rather than silently kept."""
    assert re.search(r"timeout:\s*60_?000", _client())


def test_the_output_download_asks_for_longer_than_the_default():
    if not (_API / "index.ts").exists():
        return
    # The window is what FOLLOWS the URL, and the LAST occurrence of it: the
    # options object is written after the url, and the name appears twice —
    # `downloadUrl` builds the same path a few lines above. Slicing on the first
    # match reads that one's surroundings and passes or fails for the wrong
    # reason.
    body = _index().split("download-output")[-1][:400]
    assert "timeout: 300_000" in body, \
        "the per-conversion download is back on the 60s default"


def test_every_binary_download_overrides_the_default():
    """A blob endpoint on the shared 60s default is this bug waiting to recur.
    Each responseType: "blob" call must name its own timeout."""
    src = _index()
    missing = []
    for i, chunk in enumerate(src.split('responseType: "blob"')[1:]):
        window = chunk[:260]
        if "timeout" not in window:
            head = src.split('responseType: "blob"')[i][-160:].strip().splitlines()
            missing.append(head[-1] if head else f"blob call #{i + 1}")
    assert not missing, f"blob downloads with no timeout of their own: {missing}"


def test_the_bundle_still_generates_in_the_background_first():
    """Why the .zip never showed this. Losing it would put every interface back
    inside one request."""
    src = _index()
    assert "generateMergedAllAndWait" in src
    body = src.split("downloadAll: async (")[1][:900]
    assert "generateMergedAllAndWait" in body


def test_the_per_row_buttons_generate_before_they_download():
    """Both row actions wait for a background generate, so the download itself is
    a read of a finished file rather than the work."""
    page = (_API.parent / "pages" / "ProjectOverviewPage.tsx")
    if not page.exists():
        return
    src = page.read_text(encoding="utf-8")
    for fn in ("downloadFbdi", "downloadTemplate"):
        body = src.split(f"const {fn} = async")[1][:520]
        assert "generateAndWait" in body, fn
        assert "OutputApi.download(" in body, fn
