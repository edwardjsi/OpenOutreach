# Incident: Connect crash-loop from wedged Chromium renderer (2026-08-31)

**Severity:** low — no account-level event (unlike the 2026-08-29 restriction), but a
design gap allowed an unbounded browser relaunch storm. Self-resolved: the wedged
renderer cleared ~8 minutes later and the pipeline resumed normally.
**Status:** root-caused; fix landed in this tree (`daemon.py` + `tasks/connect.py` + regression test).

---

## 1. Timeline (UTC)

| Time (UTC) | Event |
|---|---|
| 04:02:59–04:03:29 | Two transient Voyager enrichment failures — `Voyager API failed … — skipping`; `Enriched 1/3`. Normal, handled. |
| 04:04:19 | Connect task for `arihant-jain-005394343` (originally scheduled 08-29, so it had been retrying since then) times out on the Connect-button click — `send_btn.first.click(force=True)` in `actions/connect.py:116`. Diagnostic snapshot (`page.html`, 165 KB) shows the profile loaded fine: `<title>Arihant Jain | LinkedIn</title>`, no authwall/checkpoint markers. Daemon logs **"Browser crash during connect"**, closes the session, marks the task FAILED. |
| 04:04:51 → 04:08:57 | 7 more cycles, ~37s apart, each a full browser relaunch. The failure migrates to `page.goto("https://www.linkedin.com/feed/")` timing out at 30s inside `ensure_browser()` (`browser/login.py:138`) — the renderer is wedged. Not the network: `curl` from the container returns an instant 302. |
| 04:08:57 | Loop stops on its own — the next relaunch works. |
| ~04:16 onward | Normal operation: connects land `PENDING` every ~60s; `arihant-jain-005394343` eventually reaches PENDING (request sent). |
| Since ~04:26 | Voyager enrichment degrades to `Enriched 0/2` repeatedly while browser actions work — LinkedIn-side throttling of the Voyager endpoint. Separate, non-blocking, still to watch. |

Eight snapshots retained in the container at `/tmp/openoutreach-diagnostics/2026-08-31_040419..040857_TimeoutError/`.

## 2. Root cause

Two compounding design gaps:

1. **`daemon.py` mislabels timeouts as browser crashes.** Playwright's `TimeoutError` is a
   subclass of `PlaywrightError`, and the daemon's `except PlaywrightError:` branch assumes a
   crashed renderer: it logs "Browser crash", closes the session, and marks the task FAILED.
   A page that merely fails to finish loading in 30s gets the same destructive treatment as a
   dead target — and `session.close()` burns the launch cost for nothing.

2. **No retry bound for daemon-level exceptions on connect.** The connect task payload carries
   only `campaign_id` (`scheduler.enqueue_connect`); the deal is chosen inside the handler.
   When a `TimeoutError` escapes the handler, the deal stays `READY_TO_CONNECT`, so
   `reconcile._seed_connect_tasks` immediately reseeds the same connect task,
   `find_candidate` returns the same top deal, and the cycle repeats. `MAX_CONNECT_ATTEMPTS`
   is only consulted inside the handler (SkipProfile / no-Connect-button paths) — never for
   exceptions caught at the daemon level.

Result: 8 full browser launches against the live account in 4 minutes, one per ~37s. Had the
wedge persisted, the loop would have run indefinitely — the exact abnormal-browser /
high-volume pattern LinkedIn's risk engine flagged on 08-29.

## 3. Fixes landed (all in this working tree)

- **`linkedin/daemon.py`** — new `except PlaywrightTimeoutError:` branch, ordered before
  `except PlaywrightError:`. Accurate "Timeout during …" log, `session.close()` (fresh
  relaunch is still the right recovery for a wedged renderer), `task.mark_failed()`, and for
  CONNECT tasks a call to `connect.demote_after_timeout(session)` so the loop is bounded.
- **`linkedin/tasks/connect.py`** — `handle_connect` records `session.current_public_id`
  (the daemon needs the deal identity; the task payload doesn't carry it). New
  `demote_after_timeout(session)` mirrors the existing SkipProfile policy: increments
  `connect_attempts`, demotes the deal back to QUALIFIED (re-promoted later by the GP gate in
  `ready_pool.promote_to_ready`), and FAILs it after `MAX_CONNECT_ATTEMPTS` (3).
- **`tests/test_daemon_timeout_bound.py`** — regression tests for the demotion ladder
  (QUALIFIED → FAILED at cap, no-op when the handler never set `current_public_id`).

## 4. Residual gaps (deliberately not fixed here)

- `check_pending` / `follow_up` timeouts still reseed via reconcile, but each is bounded by an
  existing mechanism (deal `backoff_hours`; exponential backoff on `failure_count`) — no
  relaunch storm possible.
- Voyager enrichment `0/2` degradation is a separate LinkedIn-side issue; monitor, no code
  change warranted.
