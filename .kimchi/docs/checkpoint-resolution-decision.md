# LinkedIn Checkpoint Resolution — Problem & Solution Decision

**Date:** 2026-06-29
**Branch:** `active-hours-configurable`
**Status:** Implementing

---

## 1. Problem Statement

### Symptom
The daemon entered an infinite re-authentication loop during a `follow_up`
task run. The log showed the same pattern repeating every ~2 minutes:

```
[ERR] LinkedIn API → 401 Unauthorized (session expired or blocked)
[WRN] Session expired during follow_up — re-authenticating
[WRN] Re-authenticating <email> — clearing saved session
[INF] Fresh login sequence starting for <email>
[ERR] Re-authentication failed for follow_up
RuntimeError: Login failed – no redirect to feed → expected '/feed'
    | got 'https://www.linkedin.com/checkpoint/challenge/<TOKEN>'
```

### Root Cause
LinkedIn served a **security checkpoint challenge** (`/checkpoint/challenge/...`)
instead of redirecting to `/feed`. This is LinkedIn's anti-automation defense —
it detected the Playwright browser automation and demanded human verification
(OTP, identity confirmation, etc.).

The account is **safe** — a checkpoint is a *security verification*, not a ban.
The user confirmed they could log in normally from their regular browser.

### The Dangerous Code Path
The retry loop in `linkedin/daemon.py:337-344` amplified the problem:

```python
except AuthenticationError:
    logger.warning("Session expired during %s — re-authenticating", task)
    try:
        session.reauthenticate()   # full fresh login → checkpoint → fail
    except Exception:
        logger.exception("Re-authentication failed for %s", task)
    task.mark_failed()
    continue                       # immediately grabs the NEXT follow_up task
```

For **every** queued task (6+ follow_ups were queued):
1. Task runs → 401 (stuck on checkpoint page)
2. → `reauthenticate()` → fresh browser + login → hits checkpoint again → RuntimeError
3. → next task → repeat

This produced **6 full login attempts (email + password typed) in ~10 minutes**
(03:25 → 03:34). Rapid-fire automated retries are exactly the behavior that
escalates a checkpoint into an account restriction.

---

## 2. Solutions Discussed

| # | Solution | Verdict | Rationale |
|---|----------|---------|-----------|
| 1 | **Long auto-cooldown + keep running** | ❌ Rejected | Still re-attempts login after cooldown; if checkpoint persists, re-hammers LinkedIn. |
| 2 | **Stop the daemon entirely** | ❌ Rejected | No auto-recovery; requires manual restart. User declined this. |
| 3 | **Short retry cooldown (~30 min)** | ❌ Rejected | Riskier — if checkpoint persists, re-hammers LinkedIn. |
| 4 | **Generic webhook (ntfy/Slack/Discord)** | ❌ Superseded | User specifically requested Telegram as a dedicated first-class feature. |
| 5 | **Email via SMTP** | ❌ Rejected | Higher config burden (SMTP host/port/user/pass) than a single Telegram token + chat_id. |
| 6 | **Block + wait for manual VNC resolution + Telegram notification** | ✅ **Finalized** | No automated retry can pass a checkpoint. User is willing to solve challenges manually. Daemon blocks (zero LinkedIn requests), notifies via Telegram push + terminal bell, and resumes automatically once the user solves it via VNC. |

### Why blocking (not retrying) is correct
A checkpoint requires **human** verification. No amount of automated retry will
clear it — each retry only increases the risk of account restriction. The only
safe action is to **stop all automated requests** and wait for the human.

---

## 3. Finalized Solution

### Architecture
```
LinkedIn checkpoint detected (in goto_page)
        │
        ▼
await_checkpoint_resolution(session, page)
        │
        ├─ notify_checkpoint(session)  ← fires ONCE
        │      ├─ Terminal bell (\a) + colored banner to stderr/logs
        │      └─ Telegram Bot API POST (if configured)
        │
        ├─ Poll page.url every 5s
        │      └─ Heartbeat log every 60s ("waiting for checkpoint")
        │
        └─ Returns when URL leaves /checkpoint/challenge/
                │
                ▼
        goto_page completes → cookies saved → daemon resumes
```

### Components

1. **`CheckpointChallengeError`** (`linkedin/exceptions.py`)
   New exception for the edge case where resolution doesn't take.

2. **Conf constants** (`linkedin/conf.py`)
   - `CHECKPOINT_POLL_INTERVAL_S = 5`
   - `CHECKPOINT_HEARTBEAT_INTERVAL_S = 60`
   - `TELEGRAM_API_BASE = "https://api.telegram.org"`
   - `TELEGRAM_TIMEOUT_S = 10`

3. **SiteConfig fields** (`linkedin/models.py` + migration)
   - `telegram_bot_token` (CharField) — from @BotFather
   - `telegram_chat_id` (CharField) — from @userinfobot

4. **`linkedin/notifications.py`** (new module)
   - `_send_telegram(token, chat_id, text)` — POST to Bot API `/sendMessage`.
     Never raises; returns True/False.
   - `notify_checkpoint(session)` — bell + banner always; Telegram push if
     configured.

5. **Detection in `goto_page`** (`linkedin/browser/nav.py`)
   - When landed URL contains `/checkpoint/challenge/`, call
     `await_checkpoint_resolution()` instead of raising `RuntimeError`.
   - Single interception point catches both fresh-login and saved-session
     resume paths (both funnel through `goto_page` expecting `/feed`).

6. **Daemon guard** (`linkedin/daemon.py`)
   - `except CheckpointChallengeError` → mark task failed + close browser
     (so next `ensure_browser()` does a full restart, not a tight 401 cycle).

7. **`testtelegram` management command** (`manage.py testtelegram`)
   - Sends a test message to verify Telegram config end-to-end.

8. **Cookie persistence** (`linkedin/browser/login.py`)
   - After checkpoint resolves on the saved-session path, call
     `_save_cookies(session)` so the now-valid session persists.

### Key Design Decisions
- **Detection point = `goto_page`**: Single interception catches both login
  paths. Rejected scoping to `login.py` only (would miss saved-session resume).
- **Indefinite wait (no hard timeout)**: Since Telegram push notifies the
  user, indefinite blocking is practical. Escape via Ctrl+C. No finite
  timeout needed — avoids auto-retry hammering.
- **Telegram via stdlib `urllib`**: No new dependencies. Bot API is a simple
  HTTP POST.
- **Terminal bell as fallback**: Always fires, even if Telegram isn't
  configured. Zero-config safety net.
- **`notify_checkpoint` fires once**: Called at the top of
  `await_checkpoint_resolution`, not per poll. One push, not spam.

---

## 4. Telegram Setup Steps

### For the user (documented in CLAUDE.md / docs/docker.md):

1. **Create a Telegram bot:**
   - Open Telegram, search for **@BotFather**
   - Send `/newbot`
   - Choose a name (e.g., "OpenOutreach Alerts")
   - Choose a username (e.g., `openoutreach_alerts_bot`)
   - Copy the **bot token** (e.g., `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`)

2. **Get your chat ID:**
   - Search for **@userinfobot** in Telegram
   - Send any message
   - Copy the numeric **Id** (e.g., `123456789`)

3. **Configure in Django Admin:**
   - Open `http://localhost:8000/admin/linkedin/siteconfig/1/`
   - Find the **"Telegram Alerts"** section
   - Paste the bot token and chat ID
   - Save

4. **Test the configuration:**
   ```bash
   make shell
   python manage.py testtelegram
   ```
   You should receive a Telegram message: "✅ OpenOutreach Telegram alerts
   are configured correctly."

5. **Done!** Next time LinkedIn raises a checkpoint, you'll get a Telegram
   push. Open VNC (`http://localhost:6080/vnc.html`), solve the challenge,
   and the daemon resumes automatically.

---

## 5. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Telegram API unreachable / wrong token | Low | Low (no push) | `_send_telegram` returns False + WARNING log; bell+banner still fire. `testtelegram` command catches this during setup. |
| Wrong chat_id | Low | Low (no push) | Bot API returns `ok:false` with description; logged as WARNING. `testtelegram` catches this. |
| User away + Telegram unconfigured | Medium | Medium (daemon blocks silently) | Bell fires if terminal open; documented setup steps recommend configuring Telegram. |
| Checkpoint on non-login navigation | Low | Low (daemon blocks) | `goto_page` blocks there too — correct behavior; user solves via VNC, resumes. |
| Cookie not persisted after resolution | Low | Low (re-login next run) | Explicit `_save_cookies` after checkpoint resolution; re-login is acceptable fallback. |

---

## 6. Implementation Order

1. Chunk 1 — Foundation: exception, conf constants, SiteConfig fields + migration, notify module
2. Chunk 2 — Detection + manual-resolution wait in `goto_page`
3. Chunk 3 — Daemon guard for the raise path
4. Chunk 4 — `testtelegram` management command + tests
5. Chunk 5 — Docs sync (CLAUDE.md, ARCHITECTURE.md, docs/docker.md)
