import pytest
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from crm.models import Lead, Deal
from linkedin.models import Campaign, LinkedInProfile
from linkedin.intent.models import IntentSignal, SignalConfiguration
import linkedin.management.commands.run_beast_experiment

@pytest.fixture
def test_campaign(db):
    return Campaign.objects.create(name="Beast Experiment Campaign")

@pytest.fixture
def mock_session():
    with patch("linkedin.management.commands.run_beast_experiment.AccountSession") as mock_session_class:
        with patch("linkedin.management.commands.run_beast_experiment.get_first_active_profile") as mock_get_profile:
            mock_lp = MagicMock(spec=LinkedInProfile)
            mock_get_profile.return_value = mock_lp
            yield mock_session_class.return_value

@pytest.fixture
def mock_api():
    with patch("linkedin.management.commands.run_beast_experiment.MockPlaywrightAPI") as mock_api_class:
        yield mock_api_class.return_value

@pytest.fixture
def mock_agents():
    with patch("linkedin.management.commands.run_beast_experiment.TopICPAgent") as top_icp, \
         patch("linkedin.management.commands.run_beast_experiment.JobChangeAgent") as job_change:
        yield top_icp.return_value, job_change.return_value

@pytest.fixture
def mock_qualify():
    with patch("linkedin.management.commands.run_beast_experiment.qualify_with_llm") as qualify:
        qualify.return_value = (1, "Great fit.")
        yield qualify

@pytest.mark.django_db
def test_beast_experiment_command(test_campaign, mock_session, mock_api, mock_agents, mock_qualify, tmp_path):
    # Create 4 leads. None have deals.
    l1 = Lead.objects.create(public_identifier="l1", linkedin_url="http://l1")
    l2 = Lead.objects.create(public_identifier="l2", linkedin_url="http://l2")
    l3 = Lead.objects.create(public_identifier="l3", linkedin_url="http://l3")
    l4 = Lead.objects.create(public_identifier="l4", linkedin_url="http://l4")
    
    # Create a 5th lead that HAS a deal (should be excluded)
    l5 = Lead.objects.create(public_identifier="l5", linkedin_url="http://l5")
    Deal.objects.create(lead=l5, campaign=test_campaign, state="qualified")

    with patch("crm.models.Lead.get_profile", return_value={"headline": "Test"}):
        out_path = tmp_path / "out.csv"
        call_command(
            'run_beast_experiment',
            '--campaign', test_campaign.name,
            '--size', '4',
            '--out', str(out_path)
        )
        
        # 4 leads total, split 2/2.
        # Check output
        assert out_path.exists()
        content = out_path.read_text()
        assert "candidate,cohort,icp_result,icp_evidence" in content
        
        lines = content.strip().split("\n")
        assert len(lines) == 5 # 1 header, 4 rows
        
        control_count = sum(1 for line in lines if "CONTROL" in line)
        beast_count = sum(1 for line in lines if "BEAST" in line)
        
        assert control_count == 2
        assert beast_count == 2
        
        # l5 should NOT be in the file
        assert "l5" not in content

        # Both agents should have been called twice (once for each BEAST candidate)
        top_icp_agent, job_change_agent = mock_agents
        assert top_icp_agent.execute.call_count == 2
        assert job_change_agent.execute.call_count == 2
        
        # Qualify should be called 4 times
        assert mock_qualify.call_count == 4
