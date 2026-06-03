"""mem0-style fact-list summaries for Deal profile and chat history.

Single LLM boundary for the lazy summary pipeline. Summaries are stored as
JSON fact lists on `Deal.profile_summary` and `Deal.chat_summary`. Both are
campaign-scoped derived caches: deleting them and re-running the lazy path
rebuilds them from source (a Voyager re-scrape for `profile_summary`,
`ChatMessage` rows for `chat_summary`).
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

from pydantic import BaseModel, Field

from linkedin.vendor.mem0.configs.prompts import get_update_memory_messages
from linkedin.vendor.mem0.memory.utils import extract_json, remove_code_blocks

logger = logging.getLogger(__name__)


# Vendored fact-extraction prompt — modeled on mem0's FACT_RETRIEVAL_PROMPT.
# Kept inline so we don't pull mem0ai's transitive deps (qdrant, grpcio,
# sqlalchemy, posthog, ~12 MB) just for one constant string.
_FACT_EXTRACTION_PROMPT = """\
You are an information-extraction assistant. Your job is to read the input
text and produce a flat list of atomic, self-contained factual statements
about the lead (the person we are talking to).

Rules:
- Each fact must be a complete sentence that stands on its own.
- Prefer concrete, durable facts (identity, role, employer, location, career
  arc, stated goals, expressed concerns) over fleeting commentary.
- Do not invent facts. If the text does not assert it, do not include it.
- Do not duplicate facts. Merge near-duplicates.
- Keep each fact short (under ~25 words).
- Return between 0 and 30 facts. Empty list is acceptable when there is
  nothing useful to extract.
- The input may contain messages from both sides of a conversation, tagged
  [Me] and [Lead]. Extract facts about the LEAD only. Use [Me] messages
  solely as context to disambiguate the lead's replies (e.g. if the lead
  says "yes", use the preceding [Me] message to understand what they agreed
  to). Never extract facts about what [Me] said, offered, or asked.

Output a JSON object matching the schema you have been given.
"""


class FactList(BaseModel):
    """Structured LLM output for fact extraction."""

    facts: list[str] = Field(
        default_factory=list,
        description="Atomic, self-contained factual statements extracted from the input text.",
    )


class _MemoryAction(BaseModel):
    """One entry in mem0's reconciliation response — matches DEFAULT_UPDATE_MEMORY_PROMPT schema."""

    id: str
    text: str
    event: str = Field(description='One of "ADD", "UPDATE", "DELETE", "NONE".')
    old_memory: str | None = None


class _ReconcileResponse(BaseModel):
    memory: list[_MemoryAction] = Field(default_factory=list)


# ── LLM boundary ──

def extract_facts(text: str, *, context: str = "") -> list[str]:
    """Extract a flat list of atomic facts from `text`.

    `context` is an optional preamble (campaign objective, product docs) that
    biases what counts as a relevant fact. Returns `[]` for empty inputs.
    """
    if not text or not text.strip():
        return []

    from pydantic_ai import Agent

    from linkedin.llm import get_llm_model, run_agent_sync

    system = _FACT_EXTRACTION_PROMPT
    if context:
        system = f"{system}\n\nContext for relevance:\n{context}"

    agent = Agent(
        get_llm_model(),
        system_prompt=system,
        output_type=FactList,
        model_settings={"temperature": 0.0, "timeout": 60},
    )
    result: FactList = run_agent_sync(agent.run(text)).output
    return list(result.facts)


# ── Market persona ──
#
# Extracts generalizations about the target market — patterns, pain points,
# tool stacks, shared frustrations, buying behaviors — that apply across
# multiple leads in the same role/industry. These accumulate in
# ``Campaign.market_persona`` and feed the follow-up agent so every lead
# feels known from the first message.

_MARKET_FACT_EXTRACTION_PROMPT = """\
You are a market-intelligence assistant. Your job is to read conversation
transcripts and LinkedIn profile text and extract generalizations about the
target market — patterns, pain points, workflows, tools, common frustrations,
buying behaviors — that apply to multiple people in this role or industry.

Rules:
- Each fact must be a generalization about the market/role/industry, NOT about
  the specific individual you're reading about.
  WRONG: "Sarah uses Datadog and finds it expensive"
  RIGHT: "VP Engineering at Series B SaaS often use Datadog but find the
  per-host pricing model unsustainable as they scale"
- Strip ALL personally identifiable information. Never include names, company
  names, locations, or specific identifying details.
- Prefer concrete, specific generalizations over vague ones.
  WRONG: "Engineers care about observability"
  RIGHT: "Engineers at mid-market SaaS companies struggle with alert fatigue
  from juggling 3+ monitoring tools that don't integrate well"
- Look for: recurring pain points, common tool stacks, shared frustrations,
  budget constraints, decision-making patterns, buying triggers,
  industry jargon and how people talk about their problems.
- If the input is a profile (not a conversation), extract what the person's
  role, career arc, and company context imply about their market segment.
- If the input is a conversation, extract what the lead's stated problems,
  questions, and reactions reveal about broader market patterns.
- Do not duplicate existing facts. Return genuinely new insights.
- Keep each fact under ~30 words.
- Return between 0 and 20 facts. Empty list is acceptable when there is
  nothing generalizable to extract.
- The input may contain messages from both sides, tagged [Me] and [Lead].
  Extract generalizations from the lead's side only. [Me] messages are
  context for understanding what the lead is responding to.

Output a JSON object matching the schema you have been given.
"""


def extract_market_facts(text: str, *, context: str = "") -> list[str]:
    """Extract market-level generalizations from ``text``.

    Same shape as ``extract_facts`` but the prompt instructs the LLM to
    generalize beyond the individual — stripping PII and extracting
    patterns that hold across the market segment.

    ``context`` biases what counts as a relevant market fact (campaign
    objective, product docs). Returns ``[]`` for empty inputs.
    """
    if not text or not text.strip():
        return []

    from pydantic_ai import Agent

    from linkedin.llm import get_llm_model, run_agent_sync

    system = _MARKET_FACT_EXTRACTION_PROMPT
    if context:
        system = f"{system}\n\nContext for relevance:\n{context}"

    agent = Agent(
        get_llm_model(),
        system_prompt=system,
        output_type=FactList,
        model_settings={"temperature": 0.0, "timeout": 60},
    )
    result: FactList = run_agent_sync(agent.run(text)).output
    return list(result.facts)


# Once a campaign persona reaches this many facts, stop extracting —
# the persona is mature and further LLM calls have diminishing returns.
_MARKET_PERSONA_MAX_FACTS = 15


def update_market_persona(deal, new_messages) -> None:
    """Fold newly-synced messages into ``deal.campaign.market_persona``.

    Extracts market-level generalizations from the new messages, then
    reconciles them into the campaign's accumulated market persona using
    the same mem0 reconciliation as ``update_chat_summary``.

    No-op when the persona is already mature (≥``_MARKET_PERSONA_MAX_FACTS``
    facts), when there are no incoming (lead) messages, and when the LLM
    returns no new market facts.
    """
    new_messages = list(new_messages)
    if not new_messages:
        return

    campaign = deal.campaign
    existing = (campaign.market_persona or {}).get("facts", [])
    if len(existing) >= _MARKET_PERSONA_MAX_FACTS:
        return

    formatted = _format_messages_for_extraction(new_messages)
    if not formatted:
        return
    context_parts = []
    if getattr(campaign, "campaign_objective", None):
        context_parts.append(f"Campaign objective: {campaign.campaign_objective}")
    if getattr(campaign, "product_docs", None):
        context_parts.append(f"Product context: {campaign.product_docs}")
    context = "\n\n".join(context_parts)

    new_facts = extract_market_facts(formatted, context=context)
    if not new_facts:
        return

    reconciled = reconcile_facts(existing, new_facts)
    campaign.market_persona = {"facts": reconciled}
    campaign.save(update_fields=["market_persona"])
    logger.info(
        "market_persona updated for campaign=%s (+%d new facts → %d total)",
        campaign.name, len(new_facts), len(reconciled),
    )


def update_market_persona_from_profile(deal, profile_text: str) -> None:
    """Extract market facts from a lead's LinkedIn profile text.

    Called once per (lead, campaign) lifetime alongside
    ``materialize_profile_summary_if_missing``. Extracts what the lead's
    role, company, and career arc imply about the broader market segment.

    No-op when the persona is already mature.
    """
    if not profile_text or not profile_text.strip():
        return

    campaign = deal.campaign
    existing = (campaign.market_persona or {}).get("facts", [])
    if len(existing) >= _MARKET_PERSONA_MAX_FACTS:
        return
    context_parts = []
    if getattr(campaign, "campaign_objective", None):
        context_parts.append(f"Campaign objective: {campaign.campaign_objective}")
    if getattr(campaign, "product_docs", None):
        context_parts.append(f"Product context: {campaign.product_docs}")
    context = "\n\n".join(context_parts)

    new_facts = extract_market_facts(profile_text, context=context)
    if not new_facts:
        return

    reconciled = reconcile_facts(existing, new_facts)
    campaign.market_persona = {"facts": reconciled}
    campaign.save(update_fields=["market_persona"])
    logger.info(
        "market_persona updated from profile for campaign=%s lead=%s (+%d facts → %d total)",
        campaign.name, deal.lead.public_identifier, len(new_facts), len(reconciled),
    )


# ── Profile summary ──

def materialize_profile_summary_if_missing(deal, session) -> None:
    """Build `deal.profile_summary` lazily on first follow-up touch.

    Re-scrapes the lead via Voyager once per `(lead, campaign)` lifetime,
    extracts facts conditioned on the campaign objective + product docs,
    persists them on the Deal. No-op if already built.
    """
    if deal.profile_summary:
        return

    lead = deal.lead
    profile = lead.get_profile(session)
    if not profile:
        logger.warning(
            "materialize_profile_summary: empty profile for deal=%s lead=%s",
            deal.pk, lead.public_identifier,
        )
        return

    from linkedin.ml.profile_text import build_profile_text

    profile_text = build_profile_text({"profile": profile})
    context_parts = []
    campaign = deal.campaign
    if getattr(campaign, "campaign_objective", None):
        context_parts.append(f"Campaign objective: {campaign.campaign_objective}")
    if getattr(campaign, "product_docs", None):
        context_parts.append(f"Product context: {campaign.product_docs}")
    context = "\n\n".join(context_parts)

    facts = extract_facts(profile_text, context=context)
    deal.profile_summary = {"facts": facts}
    deal.save(update_fields=["profile_summary"])
    logger.info(
        "profile_summary built for deal=%s lead=%s (%d facts)",
        deal.pk, lead.public_identifier, len(facts),
    )

    update_market_persona_from_profile(deal, profile_text)


# ── Chat summary ──

def _format_messages_for_extraction(messages: Iterable) -> str:
    """Render ChatMessages as a labeled transcript for fact extraction.

    Both sides are included so the LLM can disambiguate anaphoric lead
    replies ("yes", "that sounds good") using the preceding outgoing
    context. The extraction prompt instructs the LLM to extract facts
    about the lead only.

    Returns an empty string when there are no incoming (lead) messages,
    so a one-sided outgoing burst still short-circuits the LLM call.
    """
    lines: list[str] = []
    has_incoming = False
    for m in messages:
        content = (m.content or "").strip()
        if not content:
            continue
        tag = "[Me]" if m.is_outgoing else "[Lead]"
        if not m.is_outgoing:
            has_incoming = True
        lines.append(f"{tag} {content}")
    if not has_incoming:
        return ""
    return "\n".join(lines)


def update_chat_summary(deal, new_messages) -> None:
    """Fold newly-synced ChatMessages into `deal.chat_summary` incrementally.

    Existing facts are preserved; only new messages are sent to the LLM.
    Empty input is a no-op (e.g., a sync that found no new messages).
    """
    new_messages = list(new_messages)
    if not new_messages:
        return

    formatted = _format_messages_for_extraction(new_messages)
    if not formatted:
        return

    new_facts = extract_facts(formatted)
    if not new_facts:
        return

    existing = (deal.chat_summary or {}).get("facts", [])
    reconciled = reconcile_facts(existing, new_facts)
    deal.chat_summary = {"facts": reconciled}
    deal.save(update_fields=["chat_summary"])
    logger.info(
        "chat_summary updated for deal=%s (+%d new facts → %d total)",
        deal.pk, len(new_facts), len(reconciled),
    )


# ── Reconciliation ──
#
# Mirrors mem0/memory/main.py::Memory._add_to_vector_store reconciliation
# (pinned commit c239d8a4, upstream lines 594-700) with two substitutions:
#   - vector-store ops → in-memory dict (Deal.chat_summary is a flat list)
#   - mem0's `self.llm.generate_response` → pydantic-ai Agent.run via run_agent_sync

def reconcile_facts(existing: list[str], new_facts: list[str]) -> list[str]:
    """Reconcile `new_facts` against `existing` via mem0's UPDATE prompt.

    Returns the new flat fact list after applying ADD/UPDATE/DELETE/NONE.
    """
    if not new_facts:
        return list(existing)
    actions = _request_memory_actions(existing, new_facts)
    return _apply_memory_actions(existing, actions)


def _request_memory_actions(existing: list[str], new_facts: list[str]) -> list[_MemoryAction]:
    """Run mem0's UPDATE prompt and return the parsed event list.

    Calls the LLM in raw text mode and then routes the response through the
    vendored `remove_code_blocks` / `extract_json` fallback chain (mirroring
    upstream lines 545-556) so providers that wrap JSON in markdown or emit
    `<think>` blocks still parse cleanly.
    """
    from pydantic_ai import Agent

    from linkedin.llm import get_llm_model, run_agent_sync

    old_memory = [{"id": str(idx), "text": fact} for idx, fact in enumerate(existing)]
    prompt = get_update_memory_messages(old_memory, new_facts, None)

    agent = Agent(get_llm_model(), model_settings={"temperature": 0.0, "timeout": 60})
    text = run_agent_sync(agent.run(prompt)).output
    return _ReconcileResponse.model_validate(_parse_memory_response(text)).memory


def _parse_memory_response(text: str) -> dict:
    """Parse mem0's UPDATE prompt response, mirroring upstream's two-step fallback."""
    cleaned = remove_code_blocks(text)
    if not cleaned.strip():
        return {"memory": []}
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        return json.loads(extract_json(text), strict=False)


def _apply_memory_actions(existing: list[str], actions: list[_MemoryAction]) -> list[str]:
    """Apply ADD/UPDATE/DELETE/NONE events to a flat fact list keyed by index."""
    store: dict[str, str] = {str(idx): fact for idx, fact in enumerate(existing)}
    next_id = len(existing)

    for action in actions:
        if not action.text:
            continue
        if action.event == "ADD":
            store[str(next_id)] = action.text
            next_id += 1
        elif action.event == "UPDATE":
            if action.id in store:
                store[action.id] = action.text
            else:
                logger.warning("UPDATE skipped: unknown id %r", action.id)
        elif action.event == "DELETE":
            if store.pop(action.id, None) is None:
                logger.warning("DELETE skipped: unknown id %r", action.id)
        # NONE: explicit no-op

    return list(store.values())
