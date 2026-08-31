# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Managed in `requirements/*.txt` (used by local dev and Docker).
- **Docs sync**: When modifying code, update CLAUDE.md and ARCHITECTURE.md to reflect changes.
- **No memory**: Never use the auto-memory system (no MEMORY.md, no memory files). All persistent context belongs in CLAUDE.md or ARCHITECTURE.md.
- **Error handling**: App should crash on unexpected errors. `try/except` only for expected, recoverable errors. Custom exceptions in `exceptions.py`.
- **No API backward compat**: Project has no external users yet — don't preserve old Python APIs, function signatures, or import paths. Rename, delete, and rewrite freely; no shims or re-export modules. DB schema changes still go through Django migrations as normal — existing installs must upgrade cleanly.
- **Account safety**: Never execute live LinkedIn actions (daemon, browser, Voyager, connect/message/follow-up) from code, tests, or diagnostics — live execution is the operator's job. Every change must pass `make safety-check` (static scan + network-blocked tests) and a human safety review before it may run against the real account. Details in the Account Safety section.

## Project Overview

OpenOutreach — self-hosted LinkedIn automation for B2B lead generation. Playwright + stealth for browser automation, LinkedIn Voyager API for profile data, Django + Django Admin for CRM (models owned by this project).

## Commands

```bash
# Docker
make build / make up / make stop / make logs / make up-view

# Local dev
make setup    # install deps + browsers + migrate + bootstrap CRM
make run      # run daemon
make admin    # Django Admin at localhost:8000/admin/

# Testing
make test / make docker-test
make safety-check   # account-safety gate: static scan + full test suite
pytest tests/api/test_voyager.py   # single file
pytest -k test_name                # single test
```

## Architecture (quick reference)

For detailed module docs, see `ARCHITECTURE.md`.

- **Entry**: `manage.py` — stock Django management. `rundaemon` command (migrate → onboard → validate → task queue loop). `manage.py` with no args defaults to `rundaemon`. Onboarding logic in `onboarding.py`: `OnboardConfig` (pure dataclass), `missing_keys()`, `collect_from_wizard()`, single `apply()` write path. Docker `start` script handles Xvfb/VNC, then `exec python manage.py rundaemon`.
- **State machine**: `enums.py:ProfileState` — QUALIFIED → READY_TO_CONNECT → PENDING → CONNECTED → COMPLETED / FAILED. Deal.state is a CharField with ProfileState choices (no Stage model). `Outcome` (converted/not_interested/wrong_fit/no_budget/has_solution/bad_timing/unresponsive/unknown) on Deal.outcome. `Lead.disqualified=True` = permanent exclusion. LLM rejections = FAILED Deals with wrong_fit outcome (campaign-scoped).
- **Task queue**: `Task` model (persistent). Three types: `connect`, `check_pending`, `follow_up`. Handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`. Task creation is centralized in `linkedin/tasks/scheduler.py` — no other module inserts Task rows. `set_profile_state()` fires `on_deal_state_entered(deal)`, which enqueues the task implied by the new state (CONNECTED → follow_up, PENDING → check_pending). The daemon calls `reconcile(session)` whenever the queue has no ready task: it recovers stale RUNNING rows, seeds one connect per campaign, and re-creates tasks for any active Deal without a pending task. This is the retry mechanism — a crashed handler leaves a FAILED task with no successor, and the next idle cycle re-creates it. On 401 (`AuthenticationError`), the daemon calls `session.reauthenticate()` and marks the task FAILED; reconcile picks it up. Transient page-state failures are non-destructive: `find_top_card` (`browser/nav.py`) waits up to 8s for the profile top card (render lag or a LinkedIn layout change), and when it still fails, `SkipProfile` from connect demotes the Deal back to QUALIFIED — FAILED only after `MAX_CONNECT_ATTEMPTS` (3) — while check_pending keeps the Deal PENDING and lets reconcile re-create the check. `DUMP_PAGES` saves a snapshot of any skipped page to `tests/fixtures/pages/connect/` for selector fixes.
- **ML pipeline**: GPR (sklearn) + BALD active learning + LLM qualification. Per-campaign models stored in `Campaign.model_blob` (DB). The LLM qualification prompt (`linkedin/templates/prompts/qualify_lead.j2`) treats the Campaign Objective as the authoritative ICP — the model must not invent criteria beyond it (managerial role, income disclosure), and absence of income/financial-stress signals in a profile is never grounds for rejection. NRI policy lives in the objective (approved wording, DB): Indian-origin professionals abroad are in scope; foreign nationals without Indian-origin signals are out.
- **Config**: `SiteConfig` DB singleton (LLM_PROVIDER, LLM_API_KEY, AI_MODEL, LLM_API_BASE — editable via Django Admin; `llm_provider` chooses between OpenAI/Anthropic/Google/Groq/Mistral/Cohere/openai_compatible, `llm_api_base` only consulted when provider is `openai_compatible`), `conf.py:CAMPAIGN_CONFIG` (timing/ML defaults), `conf.py` browser constants (`BROWSER_*`, `HUMAN_TYPE_*`), work-shift schedule via `SiteConfig` (`enable_active_hours` flag + `work_shift_hours` — when enabled the daemon works N hours from each start, then idles until restarted), `conf.py` onboarding defaults (`DEFAULT_*_LIMIT`), `conf.py:FASTEMBED_CACHE_DIR` (persistent model cache, defaults to `<project>/.cache/fastembed/`), `conf.py` checkpoint constants (`CHECKPOINT_POLL_INTERVAL_S`, `CHECKPOINT_HEARTBEAT_INTERVAL_S`), `conf.py` Telegram constants (`TELEGRAM_API_BASE`, `TELEGRAM_TIMEOUT_S`), Campaign/LinkedInProfile models (Django Admin). `SiteConfig.telegram_bot_token` + `telegram_chat_id` (optional — Telegram push on checkpoint, a shift-end activity report, and a final push when the daemon stops: full `send_daily_report(..., stopped=True)` via the daemon's atexit hook if the shift hadn't completed, brief `notify_daemon_stopped()` note otherwise; configure via Admin). `SiteConfig.daemon_halt` + `daemon_halt_reason` (circuit breaker — set automatically on checkpoint; clear in Admin to resume). `VOYAGER_REQUEST_TIMEOUT_MS` lives in `api/client.py` (constructor default on `PlaywrightLinkedinAPI`). `conf.py:DUMP_PAGES` (default `False`) — enable to save page HTML snapshots for fixture collection. `Campaign.search_geo_urn` (comma-separated LinkedIn geo URNs scoping People search; default India + US/UK/Canada/UAE/Singapore; empty = global results; editable via Admin).
- **Checkpoint pause (circuit breaker)**: When LinkedIn serves a `/checkpoint/challenge/` URL (security verification), `goto_page` (`browser/nav.py`) calls `await_checkpoint_resolution()` which **halts the daemon**: one notification via `linkedin/notifications.py:notify_checkpoint()` (terminal bell + colored banner always; Telegram push if configured), sets `SiteConfig.daemon_halt` (+ `daemon_halt_reason`), and returns. The daemon loop then idles with **zero LinkedIn requests** until the operator: (1) solves the challenge via VNC (`http://localhost:6080/vnc.html` or `localhost:5900`), (2) clears `daemon_halt` in Admin → Site Configuration, (3) restarts the daemon. No auto-resume — a flagged account must not be touched automatically. `goto_page` raises `CheckpointChallengeError` (`exceptions.py`) when still on the challenge URL, which drops the task and reaches the halt check. Verify Telegram config with `python manage.py testtelegram`.
- **Lazy accessors**: `Lead.get_profile(session)` is a pure live Voyager scrape (no DB caching of the raw dict); `Lead.get_urn(session)` reads the `urn` column and falls back to a scrape; `Lead.get_embedding(session)` lazily scrapes + embeds on first access, then caches the 384-dim bytes on the row. `Lead.embed_from_profile(profile)` reuses an in-hand profile dict to skip the scrape (used by `create_enriched_lead`). `Lead.to_profile_dict()` returns a minimal `{lead_id, public_identifier, url, meta}` dict (no `profile` key). `AccountSession.campaigns` (cached_property, list). `AccountSession.self_profile` (cached_property, re-discovers via Voyager on first access per session — no DB cache).
- **Deal summaries**: `Deal.profile_summary` and `Deal.chat_summary` are lazy, mem0-style JSON fact lists built on demand and updated incrementally. `linkedin/db/summaries.py` is the single boundary — `materialize_profile_summary_if_missing(deal, session)` fires on the first follow-up touch (one Voyager re-scrape per `(lead, campaign)` lifetime), `update_chat_summary(deal, new_messages)` folds newly-synced ChatMessages into the summary via `reconcile_facts`, which routes new facts through mem0's UPDATE prompt to apply ADD/UPDATE/DELETE/NONE events (no naive append-and-dedup). **Only incoming (lead) messages reach fact extraction** — outgoing seller messages are filtered at the boundary so `chat_summary` stores facts about the lead, never the seller's pitch. A one-sided burst of outgoing messages short-circuits the LLM call entirely. The follow-up agent consumes `profile_summary + chat_summary + last 6 ChatMessage rows` instead of flat profile fields. The fact-extraction prompt is vendored at `linkedin/db/summaries.py:_FACT_EXTRACTION_PROMPT`; mem0's `DEFAULT_UPDATE_MEMORY_PROMPT` and `get_update_memory_messages` are vendored under `linkedin/vendor/mem0/configs/prompts.py` (mirroring upstream paths so future syncs are a clean diff). No `mem0ai` runtime dependency — avoids qdrant/grpcio/sqlalchemy transitive bloat.
- **Django apps**: `linkedin` (main — Campaign with users M2M), `crm` (Lead with embedding/Deal), `chat` (ChatMessage).
- **Data dir**: `data/` holds persistent state (`db.sqlite3`). Docker users mount volumes at `/app/data`.
- **Docker**: Playwright base image, VNC on port 5900, `BUILD_ENV` arg selects requirements.
- **CI/CD**: `.github/workflows/tests.yml` (pytest), `deploy.yml` (build + push to ghcr.io).

## Account Safety

The repo automates a real LinkedIn account. Code changes are inert until the daemon runs them, so these rules are the gate before anything executes live.

**Hard rules**
- Never execute live LinkedIn actions from code, tests, or ad-hoc commands: no daemon runs, no browser sessions, no Voyager calls, no connect/message/follow-up handlers, no reauthentication. Only the operator runs `make run` / `make up`.
- Tests must never reach the network. `tests/conftest.py` blocks all outbound sockets by default (`socket.socket` / `socket.create_connection` raise). Opt out only via `@pytest.mark.allow_network` — never for live LinkedIn calls.
- Live-network imports (`requests`, `httpx`, `playwright`'s `sync_playwright`, `urllib.request`, ...) are only allowed in the sanctioned live layer: `linkedin/browser/`, `linkedin/api/`, `linkedin/daemon.py`, `linkedin/notifications.py`, `linkedin/management/commands/dumpcookies.py`, `commentcrafter/src/browser/`, `commentcrafter/src/actions/comment.py`.

**The gate for every change** — `make safety-check` runs both:
1. `scripts/account_safety.py --all` — static scan: danger patterns in tests, network imports outside the sanctioned live layer, changes to timing/volume constants.
2. Full `pytest` with the outbound-network guard active — an accidental live call (Playwright, requests, Voyager, LLM API, Telegram) fails the suite instead of touching the account.

**Invariants that must never change without explicit review**
- Request cadence and volume: `conf.py` `BROWSER_*`, `HUMAN_TYPE_*`, `DEFAULT_*_LIMIT`, `CAMPAIGN_CONFIG` timing, `CHECKPOINT_*`, `DUMP_PAGES`, `VOYAGER_REQUEST_TIMEOUT_MS`.
- Throttling/rate-limit behavior (`mark_exhausted`, `ReachedConnectionLimit`, daily/weekly limits).
- The checkpoint pause (`/checkpoint/challenge/` handling) and the 401 reauthentication flow.
- Search/qualification volume — any change that would scrape more profiles or make more LLM calls than the operator configured.