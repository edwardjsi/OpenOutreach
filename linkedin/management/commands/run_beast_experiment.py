import csv
import logging
import random
from typing import Any, Dict

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead, Deal
from linkedin.models import Campaign
from linkedin.intent.models import IntentSignal, SignalConfiguration
from linkedin.intent.agents.job_change import JobChangeAgent
from linkedin.intent.agents.top_icp import TopICPAgent
from linkedin.browser.registry import get_first_active_profile
from linkedin.browser.session import AccountSession
from linkedin.pipeline.qualify import _fetch_intent_signals_text
from linkedin.ml.qualifier import qualify_with_llm
from linkedin.ml.profile_text import build_profile_text

logger = logging.getLogger(__name__)


class MockPlaywrightAPI:
    """A mock API that forwards get_profile directly to the Lead method to reuse the existing mechanism."""
    def __init__(self, session):
        self.session = session
        
    def get_profile(self, public_identifier: str = None, profile_url: str = None) -> tuple[Dict[str, Any], Any]:
        lead = Lead.objects.filter(public_identifier=public_identifier).first()
        if not lead:
            return None, None
        profile_data = lead.get_profile(self.session)
        return profile_data, None


class Command(BaseCommand):
    help = "Run the BEAST V1 vs CONTROL qualification experiment on the existing CRM universe"

    def add_arguments(self, parser):
        parser.add_argument('--campaign', required=True, type=str, help='Campaign name')
        parser.add_argument('--size', type=int, default=100, help='Total size of candidate population (split 50/50)')
        parser.add_argument('--seed', type=int, default=42, help='Random seed for reproducibility')
        parser.add_argument('--out', type=str, default='beast_experiment.csv', help='Output CSV file path')

    def handle(self, *args, **options):
        campaign_name = options['campaign']
        total_size = options['size']
        seed = options['seed']
        out_path = options['out']
        
        try:
            campaign = Campaign.objects.get(name=campaign_name)
        except Campaign.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Campaign '{campaign_name}' not found."))
            return
            
        lp = get_first_active_profile()
        if not lp:
            self.stdout.write(self.style.ERROR("No active LinkedInProfile found."))
            return
            
        session = AccountSession(lp)
        # We need session.campaign for _fetch_intent_signals_text to work
        session.campaign = campaign
        session.ensure_browser()

        # 1. Fetch strictly untouched population
        # Must have no Deal in ANY campaign to be completely fresh.
        eligible_leads = list(Lead.objects.filter(disqualified=False, deal__isnull=True).order_by('pk'))
        if len(eligible_leads) < total_size:
            self.stdout.write(self.style.WARNING(f"Only {len(eligible_leads)} eligible leads found (requested {total_size})."))
            total_size = len(eligible_leads)
            
        if total_size == 0:
            self.stdout.write(self.style.ERROR("No eligible candidates found. Experiment cannot run."))
            return

        # 2. Randomize and Split
        rng = random.Random(seed)
        rng.shuffle(eligible_leads)
        
        experiment_leads = eligible_leads[:total_size]
        half_size = len(experiment_leads) // 2
        
        control_cohort = experiment_leads[:half_size]
        beast_cohort = experiment_leads[half_size:]
        
        self.stdout.write(self.style.SUCCESS(f"Selected {len(control_cohort)} CONTROL and {len(beast_cohort)} BEAST candidates."))
        
        # We need the campaign's config to be enabled to allow ingestion temporarily.
        config, _ = SignalConfiguration.objects.get_or_create(campaign=campaign)
        original_enabled = config.enabled
        original_top_icp = config.top_icp_enabled
        original_job_change = config.job_change_enabled
        
        config.enabled = True
        config.top_icp_enabled = True
        config.job_change_enabled = True
        config.save()
        
        try:
            # 3. Detect signals for BEAST cohort using EXISTING `get_profile` capabilities
            api = MockPlaywrightAPI(session)
            top_icp_agent = TopICPAgent(api=api)
            job_change_agent = JobChangeAgent(api=api)
            
            self.stdout.write("Running signal detection for BEAST cohort...")
            
            for lead in beast_cohort:
                try:
                    top_icp_agent.execute(campaign, lead.public_identifier)
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Top ICP failed for {lead.public_identifier}: {e}"))
                    
                try:
                    job_change_agent.execute(campaign, lead.public_identifier)
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Job Change failed for {lead.public_identifier}: {e}"))
                    
            # 4. Qualify both cohorts and collect data
            self.stdout.write("Running qualification for both cohorts...")
            results = []
            
            for cohort_name, cohort_leads in [("CONTROL", control_cohort), ("BEAST", beast_cohort)]:
                for lead in cohort_leads:
                    profile_data = lead.get_profile(session)
                    if not profile_data:
                        self.stdout.write(self.style.WARNING(f"Profile unavailable for {lead.public_identifier} — skipping"))
                        continue
                        
                    profile_text = build_profile_text({"profile": profile_data})
                    
                    # For CONTROL, we strictly supply empty intent signals to guarantee isolation
                    if cohort_name == "CONTROL":
                        intent_signals_text = ""
                        signals = []
                    else:
                        intent_signals_text = _fetch_intent_signals_text(session, lead.public_identifier)
                        signals = list(IntentSignal.objects.filter(campaign=campaign, subject_id=lead.public_identifier))
                        
                    try:
                        label, reason = qualify_with_llm(
                            profile_text,
                            product_docs=campaign.product_docs,
                            campaign_objective=campaign.campaign_objective,
                            intent_signals=intent_signals_text,
                        )
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"LLM qualification failed for {lead.public_identifier}: {e}"))
                        label, reason = 0, f"Error: {e}"
                        
                    # Build Row
                    signal_types = [sig.get_signal_type_display() for sig in signals]
                    signal_sources = [sig.source for sig in signals]
                    signal_observed_ats = [str(sig.observed_at) if sig.observed_at else "" for sig in signals]
                    signal_evidences = [str(sig.evidence) for sig in signals]
                    signal_confidences = [str(sig.confidence) for sig in signals]
                    
                    # Clean the qualification text (LLM might output multiline)
                    icp_result = "PASS" if label == 1 else "FAIL"
                    
                    results.append({
                        "candidate": lead.public_identifier,
                        "cohort": cohort_name,
                        "icp_result": icp_result,
                        "icp_evidence": profile_data.get("headline", ""),
                        "signal_type": " | ".join(signal_types),
                        "signal_source": " | ".join(signal_sources),
                        "signal_observed_at": " | ".join(signal_observed_ats),
                        "signal_evidence": " | ".join(signal_evidences),
                        "signal_confidence": " | ".join(signal_confidences),
                        "qualification_label": label,
                        "qualification_reason": reason.replace("\n", " ")
                    })
                    
            # 5. Output CSV
            fieldnames = [
                "candidate", "cohort", "icp_result", "icp_evidence",
                "signal_type", "signal_source", "signal_observed_at", "signal_evidence", "signal_confidence",
                "qualification_label", "qualification_reason"
            ]
            
            with open(out_path, mode='w', newline='', encoding='utf-8') as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in results:
                    writer.writerow(row)
                    
            self.stdout.write(self.style.SUCCESS(f"Experiment completed. Report saved to {out_path}"))
            
        finally:
            # Always revert the configuration
            config.enabled = original_enabled
            config.top_icp_enabled = original_top_icp
            config.job_change_enabled = original_job_change
            config.save()
            session.close()
