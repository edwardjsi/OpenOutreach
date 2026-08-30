# Incident: LinkedIn temporary restriction (2026-08-29) — root cause, fixes, and hardening

**Severity:** account-level risk event — LinkedIn temporarily restricted the operator's
account for "an unusually high volume of LinkedIn profile data" access.
**Status:** restriction temporary, auto-lifts 2026-08-29 02:18 AM PDT (09:18 UTC).
Daemon stopped; resume only after the lift and only with the fixes below.

---

## 1. Timeline

| Date (UTC) | Event |
|---|---|
| Aug 27 | LinkedIn profile-page markup changed (new SDUi layout: hashed CSS-module classes, no `h1`, no `data-member-id`). The top-card selectors in `linkedin/browser/nav.py` stopped matching. Every connect/check_pending that reached the UI failed with `SkipProfile("Top Card section not found")`. |
| Aug 27 | 344 deals burned to `Failed` (vs 8–26/day before) — the connect handler marked each qualified deal `Failed` on the first miss. |
| Aug 28 | 118 more deals burned; 0 connection requests sent since ~02:47 UTC. Daily report shows "Connects sent: 0". |
| Aug 28 15:1x | Operator reports the zero-connect day. Diagnosis begins. |
| Aug 28 | Fix 1 landed: `find_top_card` waits up to 8s and cycles more selector candidates; `SkipProfile` no longer burns deals (demote to QUALIFIED, FAIL only after `MAX_CONNECT_ATTEMPTS`); `check_pending` keeps deals PENDING on skip; 396 burned deals resurrected to QUALIFIED. |
| Aug 29 07:1x | Operator restarts with Fix 1. Error persists — new selectors still don't match. Page-dump capture wired into both connect and check_pending paths (`DUMP_PAGES`). |
| Aug 29 07:36–08:00 | 17 real page dumps captured. Analysis: LinkedIn's new profile page uses hashed class names; the header card is the first `<section>` inside `section[aria-label="Primary content"]`. |
| Aug 29 | Fix 2 landed: `TOP_CARD_SELECTORS` updated to `section[aria-label="Primary content"] section`; verified against all 17 dumps (pending + invite states). `DUMP_PAGES` reverted to off; dumps kept as regression fixtures. |
| Aug 29 08:19 | LinkedIn serves a security checkpoint (`/checkpoint/challenge/`). Daemon pauses (designed). |
| Aug 29 ~08:25 | Operator solves via VNC → LinkedIn shows: **account temporarily restricted** for "unusually high volume of LinkedIn profile data"; lifts **Aug 29, 2:18 AM PDT (09:18 UTC)**. |
| Aug 29 | Daemon stopped (`docker compose stop app`). Hardening work: slow-start config + checkpoint circuit breaker. |

## 2. Root cause

**The account was restricted because the tool accessed profile data at a volume
LinkedIn's risk engine flags.** Two compounding factors:

1. **LinkedIn changed the profile page markup (Aug 27)** — the top-card selectors
   stopped matching. The connect flow treated this as a per-profile failure and
   **permanently Failed every qualified deal**, then immediately retried the next
   candidate. The daemon reloaded profile pages continuously: every connect task
   re-scraped the candidate profile via Voyager and re-visited the page in the
   browser; every check_pending re-visited its deal. Hundreds of visits per day.
2. **Default volume settings are aggressive for steady-state use** — enrichment
   scraped up to 10 full profiles per search page at 6–10s gaps; every connect and
   check_pending also fetched the full profile. Accumulated over weeks, this is the
   exact "high volume of profile data" pattern LinkedIn flags.

The checkpoint was LinkedIn's escalation; the temporary restriction followed.

## 3. Fixes landed (all in this working tree)

### A. Top-card selector (root cause of the failure storm)
- `linkedin/browser/nav.py:TOP_CARD_SELECTORS` — added
  `section[aria-label="Primary content"] section` (the first section inside the
  "Primary content" wrapper is the header card in the new layout), keeping all
  legacy selectors for older layouts.
- `find_top_card(session, timeout_s=8)` now waits for the top card to render
  instead of a one-shot check.
- **Regression corpus:** 17 real page dumps committed under
  `tests/fixtures/pages/connect/`; the auto-discovered selector tests now run
  against them (previously they silently skipped).

### B. Non-destructive failure handling
- `linkedin/tasks/connect.py` — `SkipProfile` no longer burns the deal: increments
  `connect_attempts`, demotes back to QUALIFIED, FAILs only after
  `MAX_CONNECT_ATTEMPTS` (3), and dumps the page (when `DUMP_PAGES`).
- `linkedin/tasks/check_pending.py` — a skipped check keeps the Deal PENDING
  (reconcile re-creates the check) instead of failing it; also dumps the page.

### C. Data repair
- 396 deals burned Aug 27–28 (Failed with empty outcome and positive qualification
  rationales) were resurrected to QUALIFIED. 66 genuine LLM rejections (`wrong_fit`)
  left untouched.

### D. Shift-end Telegram report (operator request)
- `notifications.send_daily_report(session, since=None)` is window-scoped; the
  daemon fires it at **every shift end** covering exactly that shift
  ("Shift Report — start → end"), not once per day.

## 4. Hardening (this session)

### Circuit breaker: `SiteConfig.daemon_halt`
A security checkpoint now **halts the daemon** instead of pausing-and-resuming:

- New `SiteConfig` fields: `daemon_halt` (bool) + `daemon_halt_reason` (text)
  (migration `0019_siteconfig_daemon_halt`).
- `nav.await_checkpoint_resolution` — on checkpoint detection: one notification
  (bell + Telegram), sets the halt flag, returns. No auto-resume.
- `daemon.run_daemon` — when halted, idles with **zero requests**, logging the
  reason, until the operator clears the flag.
- Resume procedure: solve the challenge via VNC → uncheck `daemon_halt` in
  Admin → Site Configuration → restart the daemon.
- Admin fieldset added ("Daemon Halt (circuit breaker)").

### Slow-start volume settings (`linkedin/conf.py`)
These are deliberate, operator-approved reductions (the safety scanner warns on
any change to these — they are the request-cadence invariants):

| Setting | Before | After |
|---|---|---|
| `enrich_max_per_page` | 10 | 4 |
| `enrich_min_delay_seconds` | 6 | 25 |
| `enrich_max_delay_seconds` | 10 | 40 |
| `check_pending_recheck_after_hours` | 24 | 36 |

## 5. Resume checklist (after the lift)

1. Confirm the restriction is lifted (login works, normal feed).
2. **Do not** rush: first day or two, watch logs for any new `/checkpoint/`.
3. Start the daemon: `make up` (or `docker compose -f local.yml up -d`).
4. Confirm connects flow: expect `▶ connect` → status check → `PENDING` and a
   `CONNECTED`/`follow_up` sequence, with **no** "Top card not found" warnings.
5. If the connect submission step (Invite button click / "Send without a note"
   modal) misbehaves on the new layout, a fresh page dump will show it — same
   loop as before: enable `DUMP_PAGES`, capture, fix, revert.

## 6. Lessons

- **A broken selector is not a per-profile failure.** Treat page-structure
  failures as transient/global: retry, don't fail, and capture the page.
- **Volume is the risk.** The tool's core activity (profile-data scraping) is
  exactly what LinkedIn monitors. Keep enrichment conservative; ramp slowly.
- **A checkpoint must stop everything until a human decides.** The circuit
  breaker encodes this.
- **Never auto-resume on a flagged account.** Human acknowledgment is the gate.

## 7. Open items

- Connect-state UI selectors on the new layout (Invite click, send-without-note
  modal, weekly-limit popup) are **not yet verified against a live connect** —
  static dumps only covered status detection. First successful connect after
  resume will confirm (or produce the dump to fix).
- Consider a general failure breaker (N consecutive task failures → halt) if
  checkpoints recur — currently the breaker is checkpoint-only.
