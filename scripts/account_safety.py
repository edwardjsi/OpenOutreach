#!/usr/bin/env python3
"""Static account-safety scanner for OpenOutreach.

The repo automates a real LinkedIn account. This scanner is the static half
of the safety gate; the pytest outbound-network guard (tests/conftest.py) is
the dynamic half. Together with a human review they form `make safety-check`.

Rules
-----
HARD (exit 1)
  * tests/ (or any *test*.py): constructing a real AccountSession,
    instantiating PlaywrightLinkedinAPI outside a patch(), launching a real
    browser (sync_playwright), using an HTTP client
    (requests/httpx/urllib.request/aiohttp), or navigating pages (.goto).
    Exception: tests/browser/conftest.py — it launches a local headless
    Chromium that renders SAVED HTML fixtures via page.set_content()
    (no navigation, no network); it is the documented DUMP_PAGES path.
  * non-test code: network imports (requests/httpx/aiohttp/websockets/
    selenium/urllib.request, playwright's sync_playwright) outside the
    sanctioned live layer.
WARN (exit 0)
  * change mode only (files vs HEAD): edits to linkedin/conf.py or
    commentcrafter/src/config.py (timing/volume/stealth constants).
  * a test opting out of the network block via @pytest.mark.allow_network
    (loopback test doubles only — never for live LinkedIn calls).

Usage
-----
  .venv/bin/python scripts/account_safety.py          # changed vs HEAD
  .venv/bin/python scripts/account_safety.py --all    # whole repo
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Modules where direct network usage is expected (the live automation layer).
SANCTIONED_NETWORK = (
    "linkedin/browser/",
    "linkedin/api/",
    "linkedin/daemon.py",
    "linkedin/notifications.py",
    "linkedin/management/commands/dumpcookies.py",
    "commentcrafter/src/browser/",
    "commentcrafter/src/actions/comment.py",
)

# Test files exempt from the test-branch danger patterns (see module docstring):
# tests/conftest.py is the network guard itself — its comments and error text
# deliberately name the danger words; tests/browser/conftest.py launches a
# local headless Chromium for saved HTML fixtures (no navigation, no network).
SANCTIONED_TEST_FILES = ("tests/conftest.py", "tests/browser/conftest.py")

# Files whose contents control request cadence/volume/stealth — warn on change.
TIMING_OR_VOLUME_FILES = ("linkedin/conf.py", "commentcrafter/src/config.py")

# Direct HTTP-client imports (urllib.parse is NOT network — intentionally absent).
HTTP_IMPORT_RE = re.compile(
    r"^\s*(?:"
    r"import\s+(requests|httpx|aiohttp|websockets|selenium|urllib\.request)\b"
    r"|from\s+(requests|httpx|aiohttp|websockets|selenium|urllib\.request)\s+import"
    r")"
)

# Launching a real browser: from playwright... import sync_playwright.
# Exception-type imports (e.g. playwright.sync_api.Error) are harmless and
# intentionally NOT flagged. playwright_stealth is a behavior plugin, not a
# launcher, and does not match (no dotted path from `playwright`).
LAUNCH_RE = re.compile(r"from\s+playwright(?:\.[a-zA-Z_]\w*)*\s+import")

TEST_REAL_SESSION_RE = re.compile(r"(?<!Fake)AccountSession\(")


class Violation:
    def __init__(self, path: str, line_no: int, message: str, hard: bool):
        self.path = path
        self.line_no = line_no
        self.message = message
        self.hard = hard


def is_test_file(rel: str, name: str) -> bool:
    return rel.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py")


def scan_file(path: Path, violations: list[Violation], warn_timing: bool) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(ROOT).as_posix()
    test = is_test_file(rel, path.name)

    if test:
        if rel in SANCTIONED_TEST_FILES:
            return
        for i, line in enumerate(text.splitlines(), 1):
            if TEST_REAL_SESSION_RE.search(line):
                violations.append(Violation(rel, i, "test constructs a real AccountSession (live LinkedIn)", True))
            if "PlaywrightLinkedinAPI(" in line and "patch(" not in line:
                violations.append(Violation(rel, i, "test instantiates the live API client outside a patch()", True))
            if "sync_playwright" in line:
                violations.append(Violation(rel, i, "test launches a real browser", True))
            if re.search(r"\b(requests|httpx|urllib\.request|aiohttp)\b", line):
                violations.append(Violation(rel, i, "test uses an HTTP client (would hit the network)", True))
            if ".goto(" in line:
                violations.append(Violation(rel, i, "test navigates a page (live browser navigation)", True))
            if "allow_network" in line and "pytest.mark" in line:
                violations.append(Violation(rel, i, "test opts out of the network block — loopback test doubles only, never live LinkedIn", False))
    else:
        for i, line in enumerate(text.splitlines(), 1):
            if HTTP_IMPORT_RE.search(line) or (LAUNCH_RE.search(line) and "sync_playwright" in line):
                if not rel.startswith(SANCTIONED_NETWORK):
                    violations.append(Violation(rel, i, "network import outside the sanctioned live layer", True))
        if warn_timing and rel in TIMING_OR_VOLUME_FILES:
            violations.append(Violation(rel, 1, "timing/volume/stealth constants changed — human review required", False))


def collect_files(all_files: bool) -> list[Path]:
    if all_files:
        return sorted(
            p for p in ROOT.rglob("*.py")
            if ".venv" not in p.parts and "vendor" not in p.parts
        )
    proc = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=ROOT, capture_output=True, text=True
    )
    return sorted(ROOT / n for n in proc.stdout.splitlines() if n.endswith(".py") and (ROOT / n).exists())


def main() -> int:
    all_files = "--all" in sys.argv[1:]
    files = collect_files(all_files)
    if not files:
        print("account-safety: no Python files to scan")
        return 0

    violations: list[Violation] = []
    for path in files:
        scan_file(path, violations, warn_timing=not all_files)

    hard = [v for v in violations if v.hard]
    warn = [v for v in violations if not v.hard]

    for v in hard:
        print(f"ERROR {v.path}:{v.line_no}  {v.message}")
    for v in warn:
        print(f"WARN  {v.path}:{v.line_no}  {v.message}")

    print()
    if hard:
        print(f"account-safety: {len(hard)} HARD violation(s) — fix before proceeding.")
    else:
        print(f"account-safety: OK — {len(files)} file(s) scanned, {len(warn)} warning(s).")

    print()
    print("Human review checklist (required before any live run):")
    print("  1. Does this change emit new requests to LinkedIn (new loops, scrapes, calls)?")
    print("  2. Does it change request cadence/volume/timing (conf.py BROWSER_*, HUMAN_TYPE_*, limits)?")
    print("  3. Does it touch auth (login, cookies, reauthenticate) or the checkpoint pause?")
    print("  4. Does it weaken throttling / rate-limit handling (mark_exhausted, ReachedConnectionLimit)?")
    print("  5. Do tests pass with the network guard active (make test / make safety-check)?")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
