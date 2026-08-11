# CommentCrafter 🧠✍️

> **Mindful LinkedIn Commenting — never "Great article!" again.**

Automated LinkedIn commenter that actually adds value. Scrapes posts from your feed, target influencers, and topic searches. Uses an LLM to evaluate each post for comment-worthiness — only posts where you can add **unique, specific, personal insight** get a comment.

The result: profile visits. Conversations. Authority.

### How it works

1. **Scrape** — collects posts from your LinkedIn feed, specific target accounts, and search results by topic keyword
2. **Evaluate** — an LLM agent scores each post 0-10 for comment-worthiness:
   - 9-10: "Can I write something only I could write?"
   - 7-8: "Do I have a genuine perspective to add?"
   - 0-6: "Skip — this would be generic"
3. **Generate** — for worthy posts, the LLM writes a 1-3 sentence comment with personal experience, a counterpoint, or an insight the author missed
4. **Post** — comments are posted one at a time via Playwright, with human-like typing delays and natural rhythm

### What makes a good comment (according to CommentCrafter)

| ❌ Never | ✅ Always |
|----------|----------|
| "Great article!" | "I've seen the same pattern at Series B startups — the CTO who greenlit it left 6 months later and the project died." |
| "Thanks for sharing" | "Interesting framing. One thing most people miss is that the data pipeline itself becomes the bottleneck." |
| "Love this!" | "We tried this approach at my last company and hit a wall with X. Here's what worked instead..." |
| Any emoji-only or hashtag spam | A respectful counterpoint or extension of the author's point |

### Quick Start

```bash
# 1. Install
pip install -r requirements.txt
playwright install --with-deps chromium

# 2. First-time setup (configures LLM + creates DB)
python -m src.main setup

# 3. Login to LinkedIn (opens a browser window)
python -m src.main login

# 4. Add accounts to follow
python -m src.main add-target "Nicolas Dessaigne" "https://www.linkedin.com/in/nicolasdessaigne/"

# 5. Add topics to watch
python -m src.main add-topic "micro-SaaS"
python -m src.main add-topic "indie hacking"

# 6. Dry-run (scrape + evaluate, no posting)
python -m src.main scrape

# 7. Run for real
python -m src.main run
```

### Commands

| Command | Description |
|---------|-------------|
| `python -m src.main setup` | Initialize DB, set LLM provider/key, your background |
| `python -m src.main run` | Start daemon loop |
| `python -m src.main login` | LinkedIn login (saves cookies) |
| `python -m src.main status` | Stats, queue, recent comments |
| `python -m src.main config` | Show current LLM config |
| `python -m src.main scrape` | One-shot dry-run (scrape + evaluate only) |
| `python -m src.main add-target <name> <url>` | Add LinkedIn profile to watch |
| `python -m src.main add-topic <topic>` | Add topic keyword to search |
| `python -m src.main list-targets` | Show all tracked accounts |
| `python -m src.main list-topics` | Show all tracked topics |

### Configuration

All config stored in SQLite at `data/commentcrafter.db`:

| Key | Description |
|-----|-------------|
| `llm_provider` | `openai`, `anthropic`, `groq`, `google`, `mistral`, `cohere`, `openai_compatible` |
| `llm_api_key` | Your API key |
| `ai_model` | Model name (e.g. `gpt-4o-mini`, `claude-3-haiku-20240307`) |
| `llm_api_base` | Base URL for OpenAI-compatible providers |
| `background` | Your professional background (used to contextualize comments) |

### Architecture

See `CLAUDE.md` for the full architecture.

### Why not just use a generic bot?

Generic bots leave comments that nobody reads and nobody clicks on. They're noise.

CommentCrafter is designed around one principle: **a single insightful comment on a large creator's post drives more value than 100 "Great article!"s.** People click profiles on comments that teach them something, challenge their thinking, or share a real experience.

The evaluator is intentionally harsh. It would rather skip 100 posts than post one generic comment.
