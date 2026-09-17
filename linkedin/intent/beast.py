"""Passive Beast observation layer.

This module intentionally contains NO LinkedIn access, discovery, browser
automation, profile fetching, or activity fetching.  It only analyzes an
observation that an already-approved data source has supplied to it.

Account-safety boundary:
    Beast must never create a new LinkedIn data-access path. Callers must
    supply the observation explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class BeastObservation:
    """An observation supplied by an existing/approved source."""

    campaign: Any
    profile_data: Mapping[str, Any]
    post_text: str
    post_url: str = ""
    observed_at: datetime | None = None


@dataclass(frozen=True)
class BeastResult:
    """Result of passive observation analysis."""

    signal_type: str | None
    evidence: str
    confidence: float
    stable_event_id: str


def observe(observation: BeastObservation) -> BeastResult | None:
    """Analyze supplied data without accessing LinkedIn.

    Phase 7 deliberately starts with a safe seam rather than an LLM or
    LinkedIn integration.  The observer is therefore a pure, fail-closed
    placeholder until a reviewed signal-classification implementation is
    added and tested.
    """

    # No network access, browser access, profile lookup, post lookup, or
    # discovery belongs in this function.
    return None


def observe_and_ingest(observation: BeastObservation) -> BeastResult | None:
    """Observe an already-supplied observation and ingest only a valid result.

    This wrapper is intentionally inert until ``observe`` has a reviewed
    classifier.  It exists to establish the integration seam without changing
    any existing LinkedIn workflow.
    """

    return observe(observation)
