# linkedin/tasks/source_signals.py
import logging

from linkedin.intent.agents.job_change import JobChangeAgent
from linkedin.intent.agents.top_icp import TopICPAgent
from linkedin.models import Task

logger = logging.getLogger(__name__)

# Closed registry: only explicitly approved agents are instantiated.
AGENT_REGISTRY = {
    "top_icp": TopICPAgent,
    "job_change": JobChangeAgent,
}

def handle_source_signals(task: Task, session, qualifiers: dict) -> None:
    """
    Handle SOURCE_SIGNALS tasks by executing the specified agent.
    Expected payload keys: "campaign_id", "agent", "target_id".
    """
    payload = task.payload
    campaign_id = payload.get("campaign_id")
    agent_name = payload.get("agent")
    target_id = payload.get("target_id")

    if not campaign_id or not agent_name or not target_id:
        task.payload["retryable"] = False
        task.save(update_fields=["payload"])
        logger.error(f"Invalid SOURCE_SIGNALS payload: {payload}")
        raise ValueError(f"Invalid SOURCE_SIGNALS payload: {payload}")

    agent_class = AGENT_REGISTRY.get(agent_name)
    if not agent_class:
        task.payload["retryable"] = False
        task.save(update_fields=["payload"])
        logger.error(f"Unknown or deferred agent requested: {agent_name}")
        raise ValueError(f"Unknown or deferred agent requested: {agent_name}")

    # Use the existing authorized API session
    agent_instance = agent_class(api=session.api())
    
    # execute will internally handle expected exceptions (like DataUnavailableError)
    # by logging and returning gracefully. Unexpected exceptions will naturally bubble up.
    agent_instance.execute(session.campaign, target_id)
