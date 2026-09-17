import pytest
from unittest.mock import MagicMock, patch

from linkedin.models import Task
from linkedin.tasks.source_signals import handle_source_signals, AGENT_REGISTRY
from linkedin.tasks.scheduler import enqueue_source_signals
from linkedin.intent.agents.base import DataUnavailableError

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.campaign = MagicMock()
    session.api.return_value = MagicMock()
    return session

@pytest.fixture
def task_payload():
    return {
        "campaign_id": 1,
        "agent": "top_icp",
        "target_id": "user1"
    }

def create_task(payload):
    task = MagicMock(spec=Task)
    task.payload = payload
    task.task_type = Task.TaskType.SOURCE_SIGNALS
    return task

def test_missing_payload_fields(mock_session):
    # E. malformed payload (empty)
    with pytest.raises(ValueError, match="Invalid SOURCE_SIGNALS payload"):
        handle_source_signals(create_task({}), mock_session, {})

    # F. missing campaign_id
    with pytest.raises(ValueError, match="Invalid SOURCE_SIGNALS payload"):
        handle_source_signals(create_task({"agent": "top_icp", "target_id": "u1"}), mock_session, {})

    # G. missing target_id
    with pytest.raises(ValueError, match="Invalid SOURCE_SIGNALS payload"):
        handle_source_signals(create_task({"campaign_id": 1, "agent": "top_icp"}), mock_session, {})

    # H. missing agent
    with pytest.raises(ValueError, match="Invalid SOURCE_SIGNALS payload"):
        handle_source_signals(create_task({"campaign_id": 1, "target_id": "u1"}), mock_session, {})

def test_unknown_agent(mock_session, task_payload):
    # C. unknown agent
    task_payload["agent"] = "unknown_agent"
    with pytest.raises(ValueError, match="Unknown or deferred agent requested"):
        handle_source_signals(create_task(task_payload), mock_session, {})

def test_deferred_agent(mock_session, task_payload):
    # D. deferred agent
    task_payload["agent"] = "funding"
    with pytest.raises(ValueError, match="Unknown or deferred agent requested"):
        handle_source_signals(create_task(task_payload), mock_session, {})

@patch.dict("linkedin.tasks.source_signals.AGENT_REGISTRY")
def test_valid_top_icp_task(mock_session, task_payload):
    # A. valid top_icp task
    # O. feature flag OFF / I. NO_SIGNAL result / J. successful signal result are handled by the agent internally
    task = create_task(task_payload)
    mock_agent_class = MagicMock()
    mock_instance = mock_agent_class.return_value
    AGENT_REGISTRY["top_icp"] = mock_agent_class
    
    handle_source_signals(task, mock_session, {})
    
    mock_agent_class.assert_called_once_with(api=mock_session.api())
    mock_instance.execute.assert_called_once_with(mock_session.campaign, "user1")

@patch.dict("linkedin.tasks.source_signals.AGENT_REGISTRY")
def test_valid_job_change_task(mock_session, task_payload):
    # B. valid job_change task
    task_payload["agent"] = "job_change"
    task = create_task(task_payload)
    mock_agent_class = MagicMock()
    mock_instance = mock_agent_class.return_value
    AGENT_REGISTRY["job_change"] = mock_agent_class
    
    handle_source_signals(task, mock_session, {})
    
    mock_agent_class.assert_called_once_with(api=mock_session.api())
    mock_instance.execute.assert_called_once_with(mock_session.campaign, "user1")

@patch.dict("linkedin.tasks.source_signals.AGENT_REGISTRY")
def test_unexpected_exception_propagation(mock_session, task_payload):
    # L. unexpected exception propagation
    # Should propagate naturally up to daemon
    task = create_task(task_payload)
    mock_agent_class = MagicMock()
    mock_instance = mock_agent_class.return_value
    mock_instance.execute.side_effect = RuntimeError("DB failure")
    AGENT_REGISTRY["top_icp"] = mock_agent_class
    
    with pytest.raises(RuntimeError, match="DB failure"):
        handle_source_signals(task, mock_session, {})

@pytest.mark.django_db
def test_duplicate_enqueue():
    # M. duplicate enqueue
    # Enqueue same payload multiple times -> should yield 1 task row
    enqueue_source_signals(campaign_id=999, agent_name="top_icp", target_id="duplicate_user")
    initial_count = Task.objects.filter(task_type=Task.TaskType.SOURCE_SIGNALS).count()
    
    # Enqueue again
    enqueue_source_signals(campaign_id=999, agent_name="top_icp", target_id="duplicate_user")
    new_count = Task.objects.filter(task_type=Task.TaskType.SOURCE_SIGNALS).count()
    
    assert initial_count == 1
    assert new_count == 1

@pytest.mark.django_db
def test_enqueue_after_completion():
    # N. retry behavior / enqueue after completion
    enqueue_source_signals(campaign_id=888, agent_name="top_icp", target_id="retry_user")
    task = Task.objects.get(task_type=Task.TaskType.SOURCE_SIGNALS, payload__campaign_id=888)
    
    # Mark as completed (terminal state)
    task.status = Task.Status.COMPLETED
    task.save()
    
    # Now we should be able to enqueue a new task since the previous is no longer PENDING
    enqueue_source_signals(campaign_id=888, agent_name="top_icp", target_id="retry_user")
    
    # Total tasks should now be 2 (one COMPLETED, one PENDING)
    assert Task.objects.filter(task_type=Task.TaskType.SOURCE_SIGNALS, payload__campaign_id=888).count() == 2
