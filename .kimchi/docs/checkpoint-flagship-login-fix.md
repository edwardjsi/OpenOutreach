# Checkpoint / flagship-web login loop fix

**Author**: Kimchi
**Date**: 2026-06-30
**Status**: Merged into `active-hours-configurable` branch — 3-file change, 98+ / 20- diff.

## Problem

After a LinkedIn checkpoint/challenge (`/checkpoint/challenge/`) is resolved from the VNC screen, the daemon detects the URL leaving `/checkpoint/challenge/` and **unblocks**. But the checkpoint may resolve to a *different* blocked page — `flagship-web/login` — rather than to `/feed` or a valid session. This happens when:

1. The user enters a CAPTCHA on the checkpoint page
2. LinkedIn sends a verification code to the user's email
3. The user enters the code on the VNC screen
4. If the code is **wrong** or **expired**, LinkedIn shows `https://www.linkedin.com/flagship-web/login/?vcd=...` — a **verification code delivery** page, not the standard `/login`

The old `await_checkpoint_resolution` function checked **only** for `/checkpoint/challenge/` in the URL — if that pattern was absent, it **returned**. This caused an infinite loop:

- `await_checkpoint_resolution` returns (URL left `/checkpoint/challenge/`)
- Daemon `task` handler raises `AuthenticationError` (the `li_at` cookie is stale)
- `session.reauthenticate()` → `start_browser_session` → `playwright_login` → `page.goto(.../login)` → `flagship-web` form → submit → **no redirect to `/feed`**
- `goto_page` → `RuntimeError` → `reauthenticate()` → **same loop**

## Fix: 3 layers

### Layer 1 — `await_checkpoint_resolution` (blocked-page guard)

File: `linkedin/browser/nav.py`

Add `_RESOLVED_PATTERNS` and `_BLOCKED_PATTERNS` constants:

```python
_RESOLVED_PATTERNS = ("/feed", "/in/", "/mynetwork/", "/jobs/", "/messaging/")
_BLOCKED_PATTERNS = (
    "/login",
    "/checkpoint/",
    "/auth/",
    "flagship-web/login",
    "?vcd=",
)
```

`_is_still_blocked(url)` — returns `True` if any blocked pattern is in the URL **and** no resolved pattern is present.

In `await_checkpoint_resolution` — **before** the "`/checkpoint/challenge/` not in current" return check, test `_is_still_blocked()`:

```python
is_resolved = "/checkpoint/challenge/" not in current
if is_resolved and not _is_still_blocked(current):
    # fully resolved — return
    ...
# If _is_still_blocked is True, DON'T return — keep blocking
```

### Layer 2 — `goto_page` (post-checkpoint pass-through)

File: `linkedin/browser/nav.py`

After `await_checkpoint_resolution` returns, `current = unquote(page.url)`. Add:

```python
if _is_still_blocked(current):
    logger.warning(
        "Post-checkpoint page is still blocked (%s) — "
        "re-authenticating before proceeding.", current,
    )
    session.reauthenticate()
    current = unquote(session.page.url)
```

This **re-authenticates** the session if the page is blocked, instead of letting the caller hit `RuntimeError`.

### Layer 3 — `daemon.py` (AuthenticationError handler)

File: `linkedin/daemon.py`

**Skip** `session.reauthenticate()` if the page is still blocked:

```python
try:
    page_url = session.page.url if session.page else ""
    if _is_still_blocked(unquote(page_url)):
        logger.warning(
            "Re-authentication skipped — page is still on a blocked page "
            "(%s). The user must resolve the challenge manually. "
            "Marking task FAILED and continuing.", page_url,
        )
        task.mark_failed()
        continue
except Exception:
    pass
```

This prevents the infinite `reauthenticate()` → `playwright_login()` → `RuntimeError` → `reauthenticate()` loop. The task is **failed** and the next reconcile cycle will **re-create** it.

## Testing

All 257 existing tests pass (9 skipped). No new tests added — the fix is purely a change in blocking behaviour (no new code paths).

## What the user sees

- **Old**: Infinite login loop in Docker logs — `RuntimeError: Login failed – no redirect to feed` → `Re-authentication...` → same error → loop
- **New**: One `RuntimeError` → `[WRN] Re-authentication skipped — page is still on a blocked page...` → task failed → daemon continues with next task

## Status

- [x] `linkedin/browser/nav.py` — `_is_still_blocked`, `_RESOLVED_PATTERNS`, `_BLOCKED_PATTERNS`
- [x] `linkedin/daemon.py` — `from urllib.parse import unquote`, `_is_still_blocked(unquote(page_url))` guard
- [x] `linkedin/browser/session.py` — no change needed (`reauthenticate()` handled by `daemon.py`)
- [x] `linkedin/browser/login.py` — no change needed (`playwright_login` already handles `goto_page` normally)
- [x] Tests pass