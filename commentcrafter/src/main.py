"""CommentCrafter — CLI entry point.

Usage:
    python -m src.main setup         # Initialize DB and config
    python -m src.main run            # Run the daemon
    python -m src.main login          # Interactive LinkedIn login
    python -m src.main config         # Show/edit configuration
    python -m src.main status         # Show daily stats and queue
    python -m src.main add-target <name> <linkedin_url> [public_identifier]
    python -m src.main add-topic <topic>
    python -m src.main list-targets
    python -m src.main list-topics
    python -m src.main scrape         # One-shot scrape + evaluate (dry-run)
"""
from __future__ import annotations

import json
import logging
import sys

from termcolor import colored

# ── Logging setup ─────────────────────────────────────────────

def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


def _print_banner():
    print(colored("""
   ___                              _  __
  / __ \\___ _   _  ___  _ __  _   _| |/ /__ _  ___ ___
 / / _` / __| | | |/ _ \\| '_ \\| | | | ' // _` |/ __/ __|
| | (_| \\__ \\ |_| | (_) | | | | |_| | . \\ (_| | (__\\__ \\
 \\ \\__,_|___/\\__, |\\___/|_| |_|\\__,_|_|\\_\\__,_|\\___|___/
  \____/     |___/

Mindful LinkedIn Commenter — never "Great article!" again.
""", "cyan"))


# ── Subcommands ──────────────────────────────────────────────


def cmd_setup():
    """Initialize the database and prompt for LLM config."""
    from src.models import init_db, set_config, get_config

    init_db()
    print(colored("✓ Database initialized at data/commentcrafter.db", "green"))

    # Prompt for LLM config if not set
    api_key = get_config("llm_api_key")
    if not api_key:
        print("\nLLM Configuration (needed for comment generation):")
        provider = input(f"  LLM provider [{get_config('llm_provider', 'openai')}]: ").strip()
        if provider:
            set_config("llm_provider", provider)

        model = input(f"  AI model [{get_config('ai_model', 'gpt-4o-mini')}]: ").strip()
        if model:
            set_config("ai_model", model)

        key = input("  API key: ").strip()
        if key:
            set_config("llm_api_key", key)

        base = input("  API base URL (optional, for OpenAI-compatible): ").strip()
        if base:
            set_config("llm_api_base", base)

    # Prompt for background/context
    bg = get_config("background")
    if not bg:
        print("\nDescribe your professional background (used to contextualize comments):")
        bg_text = input("  > ").strip()
        if bg_text:
            set_config("background", bg_text)

    print(colored("\n✓ Setup complete. Run `python -m src.main run` to start.", "green"))
    print("  Or add target accounts: python -m src.main add-target 'Name' 'https://linkedin.com/in/...'")
    print("  Or add topics:          python -m src.main add-topic 'topic to follow'")


def cmd_login():
    """Interactive LinkedIn login — saves session cookies."""
    from src.browser.session import CommenterSession
    from src.browser.login import start_browser

    session = CommenterSession()
    try:
        start_browser(session)
        print(colored("✓ Login successful. Cookies saved to data/session_cookies.json", "green"))
    finally:
        session.close()


def cmd_config():
    """Show and edit configuration."""
    from src.models import get_config, set_config, _get_conn

    print(colored("\nCurrent Configuration:", "cyan"))
    conn = _get_conn()
    rows = conn.execute("SELECT key, value FROM config ORDER BY key").fetchall()

    if not rows:
        print("  (no configuration set — run `python -m src.main setup`)")
        return

    for row in rows:
        key = row["key"]
        value = row["value"]
        if "key" in key.lower() and len(value) > 8:
            masked = value[:4] + "..." + value[-4:]
            print(f"  {key}: {masked}")
        else:
            print(f"  {key}: {value}")

    print(colored("\nTo change a value:", "yellow"))
    print("  src.main setup  (interactive)")


def cmd_status():
    """Show daily stats, queue, and configuration summary."""
    from src.models import (
        get_daily_stats, count_daily_comments, list_target_accounts,
        list_target_topics, get_config, get_recent_comments,
    )
    from src.scheduler import comments_remaining_today

    stats = get_daily_stats()
    print(colored("\n=== Daily Stats ===", "cyan"))
    print(f"  Comments today:     {stats.get('comment', 0)} / 10")
    print(f"  Remaining:          {comments_remaining_today()}")
    print(f"  Total all-time:     {stats.get('total_posted', 0)}")
    print(f"  Drafts in queue:    {stats.get('queue', 0)}")

    targets = list_target_accounts()
    print(colored(f"\n=== Target Accounts ({len(targets)}) ===", "cyan"))
    for t in targets:
        print(f"  {t['name']} → {t['linkedin_url']}")

    topics = list_target_topics()
    print(colored(f"\n=== Target Topics ({len(topics)}) ===", "cyan"))
    for t in topics:
        print(f"  {t['topic']}")

    recent = get_recent_comments(limit=5)
    if recent:
        print(colored(f"\n=== Recent Comments ({len(recent)}) ===", "cyan"))
        for c in recent:
            status_sym = "✓" if c["status"] == "posted" else "✗" if c["status"] == "failed" else "○"
            print(f"  {status_sym} [{c['status']}] to {c['author_name']}: {c['body'][:80]}...")

    print()


def cmd_add_target(name: str, linkedin_url: str, public_id: str | None = None):
    """Add a LinkedIn profile to monitor for posts."""
    from src.models import add_target_account

    if public_id is None:
        # Extract public identifier from URL
        if "/in/" in linkedin_url:
            public_id = linkedin_url.split("/in/")[-1].rstrip("/").split("?")[0]
        else:
            public_id = linkedin_url

    add_target_account(name, linkedin_url, public_id)
    print(colored(f"✓ Added target: {name} ({linkedin_url})", "green"))


def cmd_add_topic(topic: str):
    """Add a topic/keyword to monitor."""
    from src.models import add_target_topic

    add_target_topic(topic)
    print(colored(f"✓ Added topic: {topic}", "green"))


def cmd_run():
    """Start the daemon."""
    from src.models import init_db, get_config
    from src.app import run_daemon

    init_db()

    # Verify config
    api_key = get_config("llm_api_key")
    if not api_key:
        print(colored("✗ LLM API key not configured. Run `python -m src.main setup` first.", "red"))
        sys.exit(1)

    _print_banner()
    run_daemon()


def cmd_scrape():
    """One-shot: scrape feed + evaluate — dry run (no posting)."""
    from src.models import init_db, get_config
    from src.browser.session import CommenterSession
    from src.app import run_scrape_cycle, process_pending_posts

    init_db()
    session = CommenterSession()
    try:
        session.ensure_browser()
        bg = get_config("background", "")
        session._background = bg  # noqa: SLF001

        print(colored("Scraping feed + targets + topics...", "cyan"))
        new_posts = run_scrape_cycle(session)
        print(colored(f"Found {new_posts} new posts.", "cyan"))

        print(colored("\nEvaluating posts...", "cyan"))
        drafted = process_pending_posts(session, max_per_cycle=10)
        print(colored(f"Drafted {drafted} comments (none posted — this was a dry run).", "green"))
    finally:
        session.close()


def cmd_list_targets():
    """List all target accounts."""
    from src.models import list_target_accounts

    targets = list_target_accounts(active_only=False)
    if not targets:
        print("No target accounts configured.")
        return
    for t in targets:
        active = "✓" if t["active"] else "✗"
        print(f"  {active} {t['name']:30s} {t['linkedin_url']}")


def cmd_list_topics():
    """List all target topics."""
    from src.models import list_target_topics

    topics = list_target_topics(active_only=False)
    if not topics:
        print("No target topics configured.")
        return
    for t in topics:
        active = "✓" if t["active"] else "✗"
        print(f"  {active} {t['topic']}")


# ── Main ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        _print_banner()
        print("Usage:")
        print("  python -m src.main setup              # First-time setup")
        print("  python -m src.main run                 # Run the daemon")
        print("  python -m src.main login               # Login to LinkedIn")
        print("  python -m src.main config              # Show config")
        print("  python -m src.main status              # Show stats")
        print("  python -m src.main scrape              # One-shot dry-run")
        print("  python -m src.main add-target <name> <url> [pid]")
        print("  python -m src.main add-topic <topic>")
        print("  python -m src.main list-targets")
        print("  python -m src.main list-topics")
        return

    _setup_logging(verbose="--verbose" in sys.argv)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "setup":
        cmd_setup()
    elif cmd == "login":
        cmd_login()
    elif cmd == "run":
        cmd_run()
    elif cmd == "config":
        cmd_config()
    elif cmd == "status":
        cmd_status()
    elif cmd == "scrape":
        cmd_scrape()
    elif cmd == "add-target":
        if len(args) < 2:
            print("Usage: python -m src.main add-target <name> <linkedin_url> [public_identifier]")
            sys.exit(1)
        cmd_add_target(args[0], args[1], args[2] if len(args) > 2 else None)
    elif cmd == "add-topic":
        if len(args) < 1:
            print("Usage: python -m src.main add-topic <topic>")
            sys.exit(1)
        cmd_add_topic(args[0])
    elif cmd == "list-targets":
        cmd_list_targets()
    elif cmd == "list-topics":
        cmd_list_topics()
    else:
        print(colored(f"Unknown command: {cmd}", "red"))
        print("Run `python -m src.main` for help.")
        sys.exit(1)


if __name__ == "__main__":
    main()
