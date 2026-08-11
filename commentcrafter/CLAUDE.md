# CommentCrafter — CLAUDE.md

## Overview
Mindful LinkedIn commenter. Scrapes posts from feed, target accounts, and topic searches. Uses LLM to evaluate posts for comment-worthiness (ruthless filter — only high-value, specific, personal comments pass). Generates substantive comments and posts them with human-like rhythm via Playwright.

Never "Great article!" — every comment adds unique insight.

## Architecture

```
src/
├── main.py            # CLI entry point (setup/run/login/config/status)
├── config.py          # Paths, timing constants, rate limits
├── models.py          # SQLite models + Pydantic schemas for LLM output
├── app.py             # Daemon loop (scrape → evaluate → generate → post)
├── scheduler.py       # Human rhythm (bursts/breaks), daily rate limits
├── browser/
│   ├── session.py     # CommenterSession — Playwright lifecycle
│   ├── login.py       # Browser startup, fresh login, cookie persistence
│   └── nav.py         # goto_page, human_type, checkpoint handling
├── feed/
│   ├── scraper.py     # LinkedIn feed + search + profile post scraping
│   └── filter.py      # Post dedup and relevance pre-filtering
├── llm/
│   ├── engine.py      # LLM model factory (persistent asyncio runner)
│   ├── evaluator.py   # "Should I comment on this?" agent (score 0-10)
│   └── writer.py      # Comment generation agent
└── actions/
    └── comment.py     # Post comment via Playwright
```

## Data
- `data/commentcrafter.db` — SQLite (posts, comments, config, action_log)
- `data/session_cookies.json` — LinkedIn session cookies

## Commands
```bash
# Setup
python -m src.main setup              # Init DB + LLM config + background
make setup                             # Same + install deps

# Run
python -m src.main run                 # Start daemon
python -m src.main login               # Interactive LinkedIn login
python -m src.main scrape              # One-shot dry-run (no posting)

# Manage
python -m src.main add-target 'Name' 'https://linkedin.com/in/...'
python -m src.main add-topic 'topic'
python -m src.main status
python -m src.main config
```

## Key Design Decisions

### Mindful commenting
- Posts scored 0-10 by LLM evaluator. Only ≥7 proceed.
- Evaluator is ruthless: "Would my profile visits increase from this comment?"
- Writer enforces: no generic compliments, no self-promotion, max 3 sentences

### Rate limiting
- Default 10 comments/day (configurable in config.py)
- Human rhythm: 30-50 min work bursts, 10-20 min breaks between bursts
- 30-60s pause between individual comments

### LLM integration
- Uses pydantic-ai with structured output (EvaluationResult, CommentDraft)
- Persistent asyncio runner on daemon thread (same pattern as OpenOutreach)
- Supports OpenAI, Anthropic, Google, Groq, Mistral, Cohere, OpenAI-compatible

### No Django
- Zero Django dependency. Single-file SQLite via sqlite3.
- Thread-local connections with WAL mode.
