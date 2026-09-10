# Incident follow-up: backlog deletion and account recovery (2026-08-30)

**Status:** recovery complete — account backlog purged, hardening committed, fresh start planned.
**Related:** [2026-08-29 checkpoint restriction incident](./2026-08-29-checkpoint-restriction-incident.md)
covers the restriction itself; this document records the follow-up: the backlog audit,
the deletion, and the recovery state.

---

## 1. The mishap — recap

On 2026-08-29 LinkedIn temporarily restricted the operator's account for "an unusually
high volume of LinkedIn profile data" access. Root causes (documented in the incident
report):

1. **Selector break (Aug 27)** — LinkedIn changed the profile-page markup; the top-card
   selectors stopped matching. The then-live connect/check_pending code treated every
   miss as a per-profile failure, **permanently Failed the deal, and moved on** —
   reloading profiles continuously. 344 + 118 deals burned in two days with zero
   connection requests sent.
2. **Aggressive steady-state volume** — enrichment scraped up to 10 full profiles per
   search page at 6–10s gaps; every connect and check_pending also re-fetched the full
   profile.

The daemon was stopped; a checkpoint circuit breaker and slow-start volume settings were
added (committed 2026-08-30, see §6).

## 2. Why the backlog mattered

`check_pending` is the only task loop **without a daily cap**. Every check does:

- 1 full Voyager profile scrape (`get_profile_dict_for_public_id` → `Lead.get_profile`)
- 1 full Voyager scrape for connection degree (`_fetch_degree` → `api.get_profile`)
- 1 browser visit to the profile page (`_inspect_ui` → `visit_profile`)

So each check touches the profile 2–3 times. Worse, the retry mechanism amplifies it:
a failed check marks its task FAILED, and `reconcile` re-creates a fresh task on the next
idle cycle — which re-visits the profile again. A backlog of pending invites plus the
failure storm produced **thousands of accumulated task rows**, each a potential profile
visit. This retry loop was the volume amplifier behind the restriction.

## 3. The audit (2026-08-30)

Before deciding whether to resume, the pending state was counted:

| Item | Count |
|---|---|
| `PENDING` Deal rows | **456** |
| `CHECK_PENDING` Task rows (all statuses, accumulated) | **5,290** |

456 pending invites had generated 5,290 queued check rows — roughly 11 retries per deal.
Each retry re-visited the profile. This was the standing request stream that would keep
hitting LinkedIn on any resume.

## 4. The fix — backlog deletion

Performed in one transaction (`manage.py shell`):

1. **Deleted all 456 `PENDING` Deal rows** — the deals the daemon would otherwise
   re-check every 36h+ (doubling while still pending).
2. **Deleted all 5,290 `CHECK_PENDING` Task rows** (any status) — nothing left to
   re-create the loop.
3. **Set `disqualified=True` on the affected Leads** — permanent, account-level
   exclusion. Without this, the deletion would be undone: those Leads would re-enter
   qualification (`get_leads_for_qualification` only excludes Leads that still have a
   Deal in the campaign) and be re-connect-attempted — duplicate invites to people
   already contacted.

**Verified after the transaction:** `PENDING = 0`, `CHECK_PENDING tasks = 0`.

**Consequence, accepted:** any of those 456 invites that get accepted later will not be
followed up by the tool, and their Deal history is gone from the CRM. That was the
trade of deleting — the account's protection took priority.

## 5. State before / after

| | Before (Aug 29 stop) | After (Aug 30 cleanup) |
|---|---|---|
| PENDING deals | 456 | 0 |
| Queued check_pending tasks | 5,290 | 0 |
| Leads of deleted deals | eligible for re-qualification | `disqualified=True` (permanent) |
| Connect pool (QUALIFIED deals) | intact | intact — unchanged |
| FAILED (genuine LLM rejections) | intact | intact — unchanged |

Nothing else was touched: QUALIFIED deals (the connect pool), FAILED deals, Leads,
conversations, rate-limit fields, and all configuration stayed as they were.

## 6. Committed fixes

Both commits landed 2026-08-30 on `manual-session-dump`:

- **`8f25fa4` — "Harden account safety: checkpoint circuit breaker, slow-start volume,
  non-destructive failures"** (16 files, +549/−177): `daemon_halt` circuit breaker
  (migration 0019, models, nav, daemon, admin), new-layout top-card selector with 8s
  wait, non-destructive connect/check_pending skips (demote instead of burn), slow-start
  enrichment (4/page @ 25–40s) and check interval (36h), shift-end report guard (once
  per rest period, not hourly), corrected checkpoint banner (no auto-resume), incident
  report.
- **`0fadfbc` — "Ignore local stock-journal staging directories"**: `.journal-staging/`
  and `.mosi_input/` gitignored.

**Deliberately not committed:** the 60 profile-page dumps under
`tests/fixtures/pages/connect/` — they contain real people's LinkedIn profile data and
this repo is public. They remain gitignored, local-only; tests that need them skip on a
fresh checkout.

## 7. Fresh-start plan (tomorrow, 2026-08-31)

1. Confirm the restriction is lifted: manual login, normal browsing, no banner.
2. Admin (DB fields, no code change): `connect_daily_limit` **15**,
   `follow_up_daily_limit` **15**, `enable_active_hours` on, `work_shift_hours` **4**.
3. `make up` — rebuilds the image from the committed tree (plain `up -d` would run the
   stale pre-fix image).
4. Watch `make logs`: expects `▶ connect` → status → `PENDING`/`CONNECTED`, no
   "Top card not found", no `/checkpoint/`. First live connect may still trip on the
   Invite-click/modal selectors — now non-destructive (demote + retry, no burn).

Volume profile at these settings: ~15 connects (each 1 scrape + 1 visit) + ~15 follow-ups
(mostly messaging) + occasional discovery cycles (≤4 scrapes @ 25–40s) inside a hard
4-hour window — roughly 10× below the storm volume.

## 8. Lessons and open items

- **The backlog was the amplifier, not the trigger.** The restriction came from the
  failure storm; but the 456-deal/5,290-task backlog was the standing stream of repeated
  profile visits that any resume would have kept feeding.
- **`check_pending` has no daily cap.** It is bounded only by backoff (36h, doubling).
  A per-day check limit is a candidate hardening if checkpoints recur.
- **Deleting state without disqualifying the Lead is not a deletion** — the profile
  re-enters the pipeline. Disqualification is what makes a removal permanent.
- **Retry loops need bounds.** The reconcile re-create mechanism is the retry safety
  net; it is also what turns a broken handler into a request storm. A general
  consecutive-failure breaker (N failures → halt) remains an open item.
- **Commit early, commit reviewed.** All safety-critical work is now in history; the
  working tree is clean.
