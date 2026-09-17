import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

from linkedin.models import Campaign
from linkedin.intent.ingest import ingest_signal
from linkedin.intent.models import IntentSignal

logger = logging.getLogger(__name__)

class DataUnavailableError(Exception):
    """Raised when an agent encounters explicitly unavailable or restricted data."""
    pass

class BaseIntentAgent(ABC):
    """
    Abstract base class for High-Intent Signal Agents.
    
    Each agent must define its signal_type and implement the logic
    to fetch data and detect signals deterministically.
    """
    
    def __init__(self, api: Optional[Any] = None):
        self.api = api
        
    @property
    @abstractmethod
    def signal_type(self) -> str:
        """The IntentSignal.SignalType that this agent detects."""
        pass
        
    @property
    @abstractmethod
    def requires_llm(self) -> bool:
        """
        Explicit declaration of whether this agent requires LLM processing.
        If True, the agent must document its semantic extraction task.
        If False, detection must be purely deterministic.
        """
        pass

    def is_eligible(self, campaign: Campaign) -> bool:
        """
        Checks if the agent is enabled for the campaign AND if data access
        is authorized and available.
        """
        config = getattr(campaign, "signal_config", None)
        if not config or not config.enabled:
            return False
            
        # Check specific feature flag (e.g. competitor_enabled)
        flag_field = f"{self.signal_type.lower()}_enabled"
        if hasattr(config, flag_field) and not getattr(config, flag_field):
            return False
            
        return self._check_data_access()
        
    @abstractmethod
    def _check_data_access(self) -> bool:
        """
        Verifies that the required official, authorized LinkedIn API endpoints
        are available for this agent's operation.
        Must return False if data is unavailable (fail closed).
        """
        pass

    def execute(self, campaign: Campaign, target_identifier: str) -> Optional[Tuple[IntentSignal, str]]:
        """
        Orchestrates the discovery and ingestion of a signal.
        Returns the (IntentSignal, status) if successfully ingested,
        or None if no signal was found or if the agent was ineligible.
        """
        if not self.is_eligible(campaign):
            logger.info("AGENT_SKIPPED: %s is not eligible or lacks data access for campaign %s", self.__class__.__name__, campaign.pk)
            return None
            
        try:
            signal_data = self._detect(campaign, target_identifier)
            if not signal_data:
                logger.info("NO_SIGNAL: No signal detected by %s for target %s", self.__class__.__name__, target_identifier)
                return None
                
            evidence, confidence, stable_event_id, source, observed_at = signal_data
            
            # Use the shared, race-safe ingestion infrastructure
            from linkedin.url_utils import public_id_to_url
            signal, _, status = ingest_signal(
                campaign=campaign,
                signal_type=self.signal_type,
                linkedin_url=public_id_to_url(target_identifier),
                source=source,
                evidence=evidence,
                confidence=confidence,
                stable_event_id=stable_event_id,
                observed_at=observed_at
            )
            return signal, status
            
        except DataUnavailableError as e:
            logger.info("AGENT_DEFERRED: %s encountered unavailable/restricted data for target %s: %s", self.__class__.__name__, target_identifier, str(e))
            return None
            
        except Exception as e:
            logger.error("AGENT_FAILED: %s failed for target %s: %s", self.__class__.__name__, target_identifier, str(e), exc_info=True)
            raise e

    @abstractmethod
    def _detect(self, campaign: Campaign, target_identifier: str) -> Optional[Tuple[Dict[str, Any], float, str, str, Optional[Any]]]:
        """
        Executes the specific detection logic.
        
        Returns:
            Optional tuple containing:
            - evidence (Dict)
            - confidence (float 0.0-1.0)
            - stable_event_id (str)
            - source (str)
            - observed_at (Optional[datetime])
        """
        pass
