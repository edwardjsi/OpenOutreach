# Retirement Planning LinkedIn Outreach Strategy

> **Based on Ty Frankel's Masterclass System**
> *adapted for a retirement planning practice using OpenOutreach*

**Date**: 2026-07-24
**Status**: Draft — for team discussion
**Author**: Marketing Strategy Review

---

## Core Thesis from the Masterclasses

Ty Frankel's system rests on 4 pillars that apply directly to retirement planning:

1. **Internal state** — You cannot DM from fear or desperation. Retirement planning prospects (trusting you with life savings) sense neediness unconsciously.
2. **Frame control** — You control every conversation through questions, humor, and non-reactivity. In financial services, you *are* the authority — act like it.
3. **Conversation mechanics** — Initial DMs, follow-ups with asset drops, and objection handling are all formulaic once the framework is set. No improvisation needed.
4. **Sales infrastructure** — Calendly, CRM, pre-call prep, automations. Set once, runs forever.

---

## Ranked Ideas: Hardest → Easiest

---

### Tier 1: FOUNDATIONAL (High Difficulty)

---

#### 1. Internal State Mastery for Outreach Team

| | |
|---|---|
| **Difficulty** | 9/10 |
| **Impact** | 10/10 |
| **Effort** | High — personal work, not system config |
| **Source** | Masterclass 1 — *The 4 Sub-Conscious Hacks* |

**Core idea**: Ty's entire system depends on operating from 200+hz (courage, love, joy). DMs sent from fear (100), desire (125), anger (150), or pride (175) fail because prospects unconsciously detect the state. For retirement planning — where you ask for trust with life savings — this is magnified 10×.

**How to implement in OpenOutreach**:

- Add a `state_check` gating function before the daemon executes connection requests or follow-ups. If the operator checked in with a negative state that morning, the daemon pauses outreach for 2 hours.
- This is a human SOP wrapped around the system. The daemon can run a morning prompt: *"Rate your current state 1-10. If below 6, delay connect tasks by 120 minutes."*
- Inject the 4 reframes from the document (detachment from sale, abundance mentality) as a daily Telegram notification via the existing `notifications.py` infrastructure — reuse the `notify_checkpoint` pattern.

**Why OpenOutreach fits**: The system already has Telegram push, checkpoint pause/resume, and task scheduling. A state-check gate is a small extension of the existing pause pattern.

---

#### 2. Authority-Frame Content Engine for Retirement Planning

| | |
|---|---|
| **Difficulty** | 8/10 |
| **Impact** | 9/10 |
| **Effort** | High — consistent content creation time investment |
| **Source** | Masterclass 6 — *LinkedIn Content Masterclass* |

**Core idea**: Content drives inbound DMs. Ty's formula: strong emotional hook → one-line paragraphs → off-the-dome feel → specific numbers → transformation stories. For retirement planning:

> "I asked a 62-year-old client what keeps her up at night. Her answer changed how I think about retirement entirely."

**How to implement in OpenOutreach**:

- OpenOutreach has `linkedin/actions/feed.py` for content interaction. Extend to a content scheduling module.
- Use the existing LLM pipeline (`linkedin/llm.py`) to generate 7 posts/week in Ty's style. Prompt the LLM with: hooks must elicit emotion, break dense paragraphs, use specific numbers, feel off-the-dome, end with a question to drive comments.
- Post via `linkedin/actions/status.py`.
- Repurpose content into initial DMs: when a prospect engages with a post, the daemon detects the interaction and sends a personalized follow-up DM referencing the post — this is the "content + DM flywheel" Ty describes.
- **Constraint**: The LLM is supplemental. Ty emphasizes you must review and edit every post before publishing.

---

### Tier 2: STRUCTURAL (Medium Difficulty)

---

#### 3. Retirement-Specific Initial DM Sequences

| | |
|---|---|
| **Difficulty** | 7/10 |
| **Impact** | 9/10 |
| **Effort** | Medium — one-time copywriting, then A/B test |
| **Sources** | Transcript 5, Masterclass 3 — *Reverse Engineering* |

**Core idea**: Most financial advisors DM wrong — they pitch immediately ("I help people retire comfortably"). Ty's approach: find common ground, demonstrate genuine care, foreshadow value without asking for anything. For retirement planning, the entry point is emotional — legacy, freedom, fear of outliving savings.

**How to implement in OpenOutreach**:

The connect task already sends a connection request + initial message. Modify the template system to use Ty's 3-step framework:

1. **Connection request**: Personal compliment about their profile or post. No pitch. Zero.
2. **DM 1** (after connect): Common ground + mirror their situation. *"Noticed you're in [industry]. I work with a lot of [industry] founders who are building a practice around [specific goal they mentioned on their profile]."*
3. **DM 2** (if no reply): Asset drop — a short retirement checklist or anonymized case study.

The `linkedin/tasks/scheduler.py` centralizes task creation. The follow-up cadence already exists — customize the message templates.

Use the LLM to personalize each message with the lead's profile data (already scraped via Voyager API in `linkedin/api/voyager.py`).

---

#### 4. Follow-Up Sequences with Asset Drops

| | |
|---|---|
| **Difficulty** | 6/10 |
| **Impact** | 8/10 |
| **Effort** | Medium — create 3-5 assets, set up message templates |
| **Sources** | Masterclass 2 — *LinkedIn DM Masterclass*, Transcript 5 |

**Core idea**: Ty says 20-50% of calls come from follow-ups alone. Most people give up after 1-2 messages. His formula: gauge interest level (1-10) → different cadence per level → drop value assets (case studies, guides) with social proof → give them rope to reply (never ask "are you interested?").

**How to implement in OpenOutreach**:

The `follow_up` task type and `linkedin/agents/follow_up.py` agent already exist. The follow-up agent consumes `profile_summary` + `chat_summary` + last 6 messages. Extend the agent prompt with Ty's framework:

- **Follow-up 1** (day 3): Asset drop — *"Thought of you when I put this together"* + short retirement guide link
- **Follow-up 2** (day 7): Social proof — *"Just helped a client in [similar situation] figure out [X]"*
- **Follow-up 3** (day 12): Foreshadow — *"I have an idea that might solve [their stated problem]"*
- **Follow-up 4** (day 18): Direct but giving rope — *"No pressure. If retirement planning is on your radar, happy to share what I'm seeing in the current market."*

Assets are tagged per deal/campaign. The follow-up agent picks the right asset based on lead profile.

The `reconcile` function in `scheduler.py` already re-creates tasks when none exist — to allow 5 follow-ups, set the Campaign config accordingly.

---

#### 5. Objection Handling Playbook in the Follow-Up Agent

| | |
|---|---|
| **Difficulty** | 6/10 |
| **Impact** | 8/10 |
| **Effort** | Medium — one-time playbook creation, prompt engineering |
| **Sources** | Masterclass 4 — *Reply & Objection Handling*, Document 4 |

**Core idea**: When a prospect says "not interested" or "I already have an advisor," Ty's approach is **never** to ask why. Instead: give them rope, show value, plant a seed. The transcript breaks down 10 real conversations line-by-line.

**How to implement in OpenOutreach**:

The follow-up agent (`linkedin/agents/follow_up.py`) already generates responses using the LLM. Extend the agent prompt with Ty's objection-handling patterns:

- **"I already have a financial advisor"** → *"That's smart. A good advisor changes everything. Out of curiosity, what made you pick them?"* (gives rope, stays curious)
- **"Not interested"** → *"Completely fair. I'll leave you with one thing — [short value statement]. If that resonates down the line, my door's open."*
- **"Send me info"** → Don't send a PDF dump. *"Happy to. What specifically are you most curious about? Retirement timeline? Tax strategy? Something else?"*

The `linkedin/db/summaries.py` pipeline handles fact extraction from chat. Extend the fact extractor to flag objections so the agent can reference past conversations.

---

#### 6. Sales Call Prep System (Calendly + Pre-Call SOP)

| | |
|---|---|
| **Difficulty** | 5/10 |
| **Impact** | 7/10 |
| **Effort** | Low-Medium — set up once |
| **Source** | Masterclass 7 — *Sales Systems* |

**Core idea**: Ty's sales system is set once, runs forever. Calendly questionnaire captures value prop and disqualifies unqualified leads early. Pre-call SOP: create custom copy per prospect, ask "can I take notes?", speak slowly, future pace, disqualify hard.

**How to implement in OpenOutreach**:

OpenOutreach doesn't manage Calendly directly, but the CRM (Lead, Deal in `crm/models/`) tracks prospect state. Extend the workflow:

- When a Deal moves to `CONNECTED` state (call booked), the daemon sends a pre-call prep to Slack/Telegram with the lead's `profile_summary` and `chat_summary`.
- The pre-call template includes: 3 questions to ask, 1 disqualification question, the prospect's biggest stated concern (from `chat_summary`), and the most relevant case study.
- The "future pacing" technique: LLM generates 3 future-pacing questions specific to retirement planning — *"If we fast-forward 12 months and your retirement plan was on autopilot, what would a Tuesday morning look like?"*
- `Lead.disqualified=True` already exists for permanent exclusion. Use it.

---

### Tier 3: LEVERAGE (Low Difficulty, Quick Wins)

---

#### 7. Frame-Control DM Templates for the Retirement Niche

| | |
|---|---|
| **Difficulty** | 4/10 |
| **Impact** | 7/10 |
| **Effort** | Low — copy templates into existing system |
| **Sources** | Masterclass 1 (Hack 2), Masterclass 3 |

**Core idea**: Frame is "view of reality." Every DM is a frame battle. The person who controls the frame controls the conversation. For retirement planners: you are the expert helping them — this must come through in every message.

**How to implement in OpenOutreach**:

Create a template bank in `linkedin/templates/prompts/`:

- **Authority frame**: *"I've helped 40+ business owners structure their retirement. Here's what the smartest ones all do differently..."*
- **Curiosity frame**: *"Most people think retirement planning is about money. It's actually about time. When was the last time you thought about what you'd do with yours?"*
- **Value-first frame**: Open with a specific insight they can't Google.

These templates feed into `linkedin/tasks/scheduler.py` task creation. The LLM personalizes the frame for each lead.

---

#### 8. Social Proof Engine — Case Study Assets in Follow-Ups

| | |
|---|---|
| **Difficulty** | 3/10 |
| **Impact** | 7/10 |
| **Effort** | Low — write 3-5 case studies, upload to system |
| **Sources** | Masterclass 3, Masterclass 7 |

**Core idea**: Every follow-up should either drop an asset or foreshadow an intro. For retirement planning: anonymized case studies showing specific before/after numbers.

**How to implement in OpenOutreach**:

- Store 3-5 case study PDFs/links in the campaign config (`Campaign.model_blob` — already stores per-campaign data).
- Extend the follow-up agent prompt to select a case study in follow-up 2 or 3 based on the lead's profile: *"Just closed a case study with a [industry] founder who was in a similar spot — [specific problem]. Here's what we found..."*
- The `linkedin/db/summaries.py` fact extraction pipeline picks the most relevant asset.

---

#### 9. CRM Automation — Qualified/Unqualified Tagging with Notifications

| | |
|---|---|
| **Difficulty** | 2/10 |
| **Impact** | 6/10 |
| **Effort** | Low — configure existing CRM fields |
| **Source** | Masterclass 7 (Sales Systems Doc) |

**Core idea**: Ty's Zapier setup: prospect books a call → notification. Prospect tagged "unqualified" → auto-cancel notice. Don't waste time chasing unlikely leads.

**How to implement in OpenOutreach**:

- OpenOutreach already has `Lead.disqualified=True` and the `ProfileState` state machine (`QUALIFIED → READY_TO_CONNECT → PENDING → CONNECTED → COMPLETED / FAILED`).
- The `Outcome` field on Deal (`crm/models/deal.py` — choices: `converted / not_interested / wrong_fit / no_budget / has_solution / bad_timing / unresponsive / unknown`) is already in the DB.
- Create a Telegram push (via existing `notifications.py`) when a Deal reaches `CONNECTED` (call booked) or when `Outcome` is set — you know instantly without checking the admin panel.
- Add `max_follow_ups` per campaign in `conf.py` so the system drops leads after X unreplied follow-ups, conserving daily send limits for fresh prospects.

---

#### 10. Engagement Interception — Reply to Prospect Content

| | |
|---|---|
| **Difficulty** | 3/10 |
| **Impact** | 6/10 |
| **Effort** | Low — daemon already has feed interaction |
| **Sources** | Masterclass 3 Transcript, Masterclass 6 |

**Core idea**: Comment on prospect posts during follow-ups — *"Hey [Name], I commented on your post about [topic]. Great take."* This humanizes you and reactivates the DM thread. Also: when a prospect engages with your content, DM them immediately.

**How to implement in OpenOutreach**:

- OpenOutreach has `linkedin/actions/feed.py` and `linkedin/actions/like.py`. The `like` task type already exists (see migration `0009_add_engage_action_type.py`).
- When the system detects a prospect liked/commented on your post (via feed scrape), trigger a personalized DM within 24 hours referencing their engagement.
- In follow-ups: the agent checks if the prospect has posted recently and references it.
- Note: LinkedIn may limit excessive comment activity — use this sparingly, 3-5 interactions per day per account.

---

## Summary Table

| # | Idea | Difficulty | Impact | Effort | OpenOutreach Leverage |
|---|------|-----------|--------|--------|----------------------|
| 1 | State Mastery Gate | 9/10 | 10/10 | High | Extend pause/resume pattern |
| 2 | Content Engine | 8/10 | 9/10 | High | LLM pipeline + feed module |
| 3 | DM Sequences | 7/10 | 9/10 | Medium | Template system + voyager data |
| 4 | Follow-up with Assets | 6/10 | 8/10 | Medium | Existing follow_up agent + scheduler |
| 5 | Objection Handling | 6/10 | 8/10 | Medium | Agent prompt + summary pipeline |
| 6 | Sales Call Prep | 5/10 | 7/10 | Low-Med | Deal state machine + notifications |
| 7 | Frame-Control Templates | 4/10 | 7/10 | Low | Templates directory |
| 8 | Case Study Assets | 3/10 | 7/10 | Low | Campaign model_blob storage |
| 9 | CRM Zaps/Tags | 2/10 | 6/10 | Low | Existing Outcome + notifications |
| 10 | Engagement Interception | 3/10 | 6/10 | Low | Feed + like modules exist |

---

## Recommended Launch Sequence

**Phase 1 (Week 1-2)** — Quick wins first
- #7 Frame-control templates for retirement niche
- #8 Case study assets in campaign config
- #9 CRM Outcome tagging + notifications

**Phase 2 (Week 3-4)** — Core DM engine
- #3 Retirement-specific DM sequences
- #4 Follow-up sequences with asset drops
- #10 Engagement interception

**Phase 3 (Week 5-6)** — Sophistication
- #5 Objection handling playbook in agent
- #6 Sales call prep automation

**Phase 4 (Ongoing)** — Internal leverage
- #2 Content engine (consistent publishing)
- #1 State mastery practice (personal, not system)

---

*This document is a living strategy. After team discussion, update the status and mark decisions.*
