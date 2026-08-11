# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
from datetime import date

from playwright.sync_api import Error as PlaywrightError
from pydantic_ai.exceptions import ModelHTTPError

from termcolor import colored
from urllib.parse import unquote

from linkedin.browser.nav import _is_still_blocked
from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.diagnostics import failure_diagnostics
from linkedin.exceptions import AuthenticationError, CheckpointChallengeError
from linkedin.ml.qualifier import BayesianQualifier, KitQualifier
from linkedin.models import SiteConfig, Task
from linkedin.tasks.check_pending import handle_check_pending
from linkedin.tasks.connect import handle_connect
from linkedin.tasks.follow_up import handle_follow_up

logger = logging.getLogger(__name__)

_HANDLERS = {
    Task.TaskType.CONNECT: handle_connect,
    Task.TaskType.CHECK_PENDING: handle_check_pending,
    Task.TaskType.FOLLOW_UP: handle_follow_up,
}

HEARTBEAT_INTERVAL = 300  # 5 minutes
HEARTBEAT_SLICE = 60      # wake every minute during long sleeps


# ── Cloud promo ──────────────────────────────────────────────────────

_CLOUD_MESSAGES = [
    "Tired of keeping your laptop open? Run your pipeline in the cloud for $49/mo",
    "You already trust the engine. Now let it run without you babysitting your laptop",
    "The AI gets smarter with every lead. Let it run 24/7 on Cloud instead of only when your laptop is open",
    "Miss a day and the pipeline stalls — follow-ups go cold, new candidates don't get discovered. Cloud keeps it running",
    "The tool got good enough that running it locally became a job. Cloud fixes that",
    "\u2601  OpenOutreach Cloud: same AI, same code, zero ops. One command and you're live",
    "\U0001f9e0 Your AI sales team, running in the cloud. $49/mo",
    "Smart founders shouldn't be acting like robots. Let the AI handle outreach while you build your product",
    "Your leads are compounding. Your laptop shouldn't be the bottleneck",
    "\u26a1 Competitors charge $50-100/mo for template bots. Cloud gives you autonomous AI discovery for $49/mo",
    "Other tools need you to build or buy contact lists. OpenOutreach discovers leads autonomously — describe your market and the AI does the rest",
    "Expandi and Waalaxy send templates. OpenOutreach's AI agent reads conversation history and writes personalized follow-ups",
    "Running Docker + VPN yourself? Cloud handles everything — dedicated server, VPN included",
    "Self-hosted setup: 30-60 min. Cloud setup: ~1 min. Same AI, same results",
    "The server costs ~$18/mo. The VPN costs ~$6/mo. You're paying $25/mo for managed ops — if your time is worth more, Cloud pays for itself",
    "Your data never leaves your machine. Cloud is just a disposable execution layer. $49/mo, cancel anytime",
    "mTLS encryption between your machine and the server. The control plane never sees your data",
    "100% open source. Inspect every line of code on GitHub. Cloud runs the exact same codebase — no black box, no lock-in",
    "Switch between self-hosted and Cloud with one command. Download your db.sqlite3 anytime — zero lock-in",
    "No annual commitment. No usage caps. No feature gating. $49/mo, cancel anytime",
    "openoutreach logs — stream live output from your cloud instance. Watch every lead, every message, every decision in real time",
    "openoutreach down saves your DB locally and destroys the server. No orphaned servers, no forgotten bills",
]

_CLOUD_COLORS = ["cyan", "green", "yellow", "magenta"]

_CLOUD_CTAS = [
    "curl -fsSL https://openoutreach.app/install | sh",
    "curl -fsSL https://openoutreach.app/install | sh && openoutreach signup",
    "https://openoutreach.app",
]


class _CloudPromoRotator:
    """Logs a Cloud promo message at most once every *interval* seconds."""

    def __init__(self, interval: float = 120):
        self._interval = interval
        self._last = 0.0

    def maybe_log(self):
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now
        msg = random.choice(_CLOUD_MESSAGES)
        color = random.choice(_CLOUD_COLORS)
        cta = random.choice(_CLOUD_CTAS)
        logger.info(
            colored(msg + " \u2192 ", color, attrs=["bold"])
            + colored(cta, "white", attrs=["bold"]),
        )


# ── Heartbeat ────────────────────────────────────────────────────────


class Heartbeat:
    """Logs an ``alive — <context>`` line at most once every *interval* seconds.

    The first call won't log (``_last`` starts at now) — quiet gaps begin
    counting from daemon start, not the Unix epoch.
    """

    def __init__(self, interval: float = HEARTBEAT_INTERVAL):
        self._interval = interval
        self._last = time.monotonic()

    def maybe_log(self, context: str) -> None:
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now
        logger.info(colored("alive", "cyan") + " — %s", context)


def sleep_with_heartbeat(seconds: float, heartbeat: Heartbeat, context: str) -> None:
    """``time.sleep(seconds)`` that wakes every ``HEARTBEAT_SLICE`` seconds to
    let *heartbeat* fire. Use for any idle sleep longer than the heartbeat
    interval so the daemon never goes silent for more than 5 minutes.
    """
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(HEARTBEAT_SLICE, remaining))
        heartbeat.maybe_log(context)


# ── Human-rhythm pacing ──────────────────────────────────────────────


class _HumanRhythmBreak:
    """Wall-clock burst timer that injects a random break between bursts.

    Call ``reset()`` after idle sleeps (active-hours pause, waiting for
    the next scheduled task) so the burst timer tracks real work, not
    wall-clock. Call ``maybe_break()`` after each successful task —
    it sleeps a random break duration when the current burst is done.
    """

    def __init__(self, heartbeat: Heartbeat):
        self._heartbeat = heartbeat
        self._new_burst()

    def _new_burst(self):
        self._burst_start = time.monotonic()
        self._burst_duration = random.uniform(
            CAMPAIGN_CONFIG["burst_min_seconds"],
            CAMPAIGN_CONFIG["burst_max_seconds"],
        )

    def reset(self):
        """Start a fresh burst without taking a break. Use after idle gaps."""
        self._new_burst()

    def maybe_break(self):
        """Sleep a random break and start a new burst if the current one is done."""
        if time.monotonic() - self._burst_start < self._burst_duration:
            return
        break_seconds = random.uniform(
            CAMPAIGN_CONFIG["break_min_seconds"],
            CAMPAIGN_CONFIG["break_max_seconds"],
        )
        logger.info("Taking a %dm break", int(break_seconds // 60))
        sleep_with_heartbeat(
            break_seconds,
            self._heartbeat,
            f"on break, {int(break_seconds // 60)}m total",
        )
        self._new_burst()


def _build_qualifiers(campaigns, cfg, kit_model=None):
    """Create a qualifier for every campaign, keyed by campaign PK."""
    from crm.models import Lead

    qualifiers: dict[int, BayesianQualifier | KitQualifier] = {}
    n_regular = 0
    for campaign in campaigns:
        if campaign.is_freemium:
            if kit_model is None:
                continue
            qualifiers[campaign.pk] = KitQualifier(kit_model)
        else:
            q = BayesianQualifier(
                seed=42,
                n_mc_samples=cfg["qualification_n_mc_samples"],
                campaign=campaign,
            )
            X, y = Lead.get_labeled_arrays(campaign)
            if len(X) > 0:
                q.warm_start(X, y)
                logger.info(
                    colored("GP qualifier warm-started", "cyan")
                    + " on %d labelled samples (%d positive, %d negative)"
                    + " for campaign %s",
                    len(y), int((y == 1).sum()), int((y == 0).sum()), campaign,
                )
            qualifiers[campaign.pk] = q
            n_regular += 1

    return qualifiers


# ------------------------------------------------------------------
# Work-shift guard
# ------------------------------------------------------------------


def work_shift_active(config, started_at: float) -> bool:
    """True when the daemon should keep working.

    Shift model: when ``enable_active_hours`` is on, the daemon works for
    ``work_shift_hours`` measured from ``started_at`` (``time.monotonic()``
    captured when the daemon started), then idles until the daemon is
    restarted — no fixed wall-clock window, no timezone, no rest days.
    When the flag is off the daemon runs 24/7.
    """
    if not config.enable_active_hours:
        return True
    shift_seconds = float(config.work_shift_hours or 0) * 3600.0
    return time.monotonic() - started_at < shift_seconds


# ------------------------------------------------------------------
# Task queue worker
# ------------------------------------------------------------------


def run_daemon(session):
    from linkedin.models import Campaign

    cfg = CAMPAIGN_CONFIG

    # Freemium campaign removed — user requested it gone
    kit = None

    qualifiers = _build_qualifiers(
        session.campaigns, cfg, kit_model=kit["model"] if kit else None,
    )

    campaigns = session.campaigns
    if not campaigns:
        logger.error("No campaigns found — cannot start daemon")
        return

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — %d campaigns, task queue worker",
        len(campaigns),
    )

    _daily_report_sent_for: date | None = None

    cloud_promo = _CloudPromoRotator(interval=60)
    heartbeat = Heartbeat()
    rhythm = _HumanRhythmBreak(heartbeat)

    # Single-threaded: one task at a time, no concurrent enqueuing,
    # so sleeping until the next scheduled_at is safe.
    shift_started = time.monotonic()
    while True:
        site = SiteConfig.load()
        if not work_shift_active(site, shift_started):
            # ── Daily report when the shift ends ──
            # Fire once per day at the end of the first shift. A daemon
            # restarted mid-day must not report a zero-activity day (the
            # bug that produced empty Telegram reports).
            today = date.today()
            if _daily_report_sent_for != today:
                _daily_report_sent_for = today
                from linkedin.notifications import send_daily_report
                send_daily_report(session)

            logger.info(
                colored("Work shift complete", "yellow", attrs=["bold"])
                + " — worked %dh, idle until the daemon is restarted "
                  "(restart to run another shift)",
                site.work_shift_hours,
            )
            sleep_with_heartbeat(
                3600, heartbeat, "shift complete — restart the daemon to run again",
            )
            rhythm.reset()
            continue

        task = Task.objects.claim_next()
        if task is None:
            # Nothing ready — reconcile the queue from CRM state. Any deal
            # stuck without a pending task (e.g. because a prior handler
            # crashed) gets a fresh task here; this is the retry mechanism.
            from linkedin.tasks.scheduler import reconcile
            reconcile(session)

            wait = Task.objects.seconds_to_next()
            if wait is None:
                logger.info("Queue empty after reconcile — sleeping 1h")
                sleep_with_heartbeat(3600, heartbeat, "queue empty")
                rhythm.reset()
                continue
            if wait > 0:
                h, m = int(wait // 3600), int(wait % 3600 // 60)
                logger.info("Next task in %dh%02dm — sleeping", h, m)
                sleep_with_heartbeat(
                    wait, heartbeat, f"next task in {h}h{m:02d}m",
                )
                rhythm.reset()
            continue

        campaign = Campaign.objects.filter(pk=task.payload.get("campaign_id")).first()
        if not campaign:
            logger.error("Campaign %s not found", task.payload.get("campaign_id"))
            task.mark_failed()
            continue

        session.campaign = campaign
        task.mark_running()

        handler = _HANDLERS.get(task.task_type)
        if handler is None:
            logger.error("Unknown task type: %s", task.task_type)
            task.mark_failed()
            continue

        try:
            with failure_diagnostics(session):
                handler(task, session, qualifiers)
        except AuthenticationError:
            logger.warning("Session expired during %s — re-authenticating", task)
            # ── Guard: if the page is still on a blocked page (flagship-web/login),
            # re-authenticating will just loop. Check the current URL.
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
            try:
                session.reauthenticate()
            except Exception:
                logger.exception("Re-authentication failed for %s", task)
            # Either way, mark this task FAILED; reconcile will re-create a
            # fresh task for the deal on the next idle cycle.
            task.mark_failed()
            continue
        except ModelHTTPError as e:
            task.mark_failed()
            logger.error(
                colored("Daemon stopped — LLM API error", "red", attrs=["bold"])
                + "\n%s\nCheck llm_provider, ai_model, llm_api_key, and llm_api_base in Admin → Site Configuration.", e,
            )
            from linkedin.notifications import notify_error
            notify_error(
                session,
                title="LLM API Error — Daemon Stopped",
                body=f"{e}",
            )
            return
        except PlaywrightError:
            # Browser page/renderer crashed — close session so the next
            # ensure_browser() launches a fresh browser.
            logger.warning(
                "Browser crash during %s — closing session to force re-launch", task,
            )
            session.close()
            task.mark_failed()
            continue
        except CheckpointChallengeError:
            # Checkpoint was detected but not resolved (e.g. user closed VNC
            # without solving, or the page changed back to checkpoint after
            # resolution attempt). Close the browser so the next ensure_browser()
            # does a full restart instead of a tight 401→reauth cycle.
            logger.warning(
                "Checkpoint not resolved for %s — closing browser to force clean restart", task,
            )
            session.close()
            task.mark_failed()
            continue
        except Exception:
            task.mark_failed()
            logger.exception("Task %s failed", task)
            continue

        task.mark_completed()
        cloud_promo.maybe_log()
        rhythm.maybe_break()
