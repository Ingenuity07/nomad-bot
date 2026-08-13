import logging
from decimal import Decimal
from typing import Dict, Any, List
from prospecting.models import LeadCompany, ProspectingCampaign, Qualification, Evidence, CompanySignal

logger = logging.getLogger(__name__)

class ProblemFitScorer:
    @staticmethod
    def calculate_score(company: LeadCompany, campaign: ProspectingCampaign) -> Dict[str, Any]:
        """
        Evaluate how well the company matches target problem definitions.
        """
        positive_factors = []
        negative_factors = []
        unknowns = []
        
        # Check matching active signals in the database
        signals = CompanySignal.objects.filter(company=company, status='ACTIVE')
        
        # Default starting base score
        score = 50.0
        
        if not signals.exists():
            unknowns.append("No active problem signals detected for this account.")
            score = 30.0
        else:
            for cs in signals:
                weight = float(cs.signal.weight) if cs.signal else 1.0
                score_delta = 15.0 * weight * float(cs.confidence)
                score = min(score + score_delta, 100.0)
                positive_factors.append(f"Confirmed Signal: {cs.signal.name if cs.signal else 'Signal'} (Confidence: {float(cs.confidence) * 100}%)")

        # Cap minimum/maximum bounds
        score = max(min(score, 100.0), 0.0)
        
        # Classification
        if score >= 75.0:
            fit_class = "HIGH"
        elif score >= 50.0:
            fit_class = "MEDIUM"
        else:
            fit_class = "LOW"

        return {
            "score": score,
            "classification": fit_class,
            "positive_factors": positive_factors,
            "negative_factors": negative_factors,
            "unknowns": unknowns
        }


class EvidenceStrengthScorer:
    @staticmethod
    def calculate_score(company: LeadCompany) -> Dict[str, Any]:
        """
        Evaluate source independent credibility, count, and confidence levels.
        """
        positive_factors = []
        negative_factors = []
        
        evidence = Evidence.objects.filter(company=company)
        
        if not evidence.exists():
            return {
                "score": 0.0,
                "classification": "WEAK",
                "positive_factors": [],
                "negative_factors": ["No supporting evidence records found in the system."],
                "evidence_ids": []
            }
            
        total_confidence = 0.0
        unique_urls = set()
        evidence_ids = []
        
        for ev in evidence:
            total_confidence += float(ev.confidence)
            unique_urls.add(ev.source_url)
            evidence_ids.append(str(ev.id))

        # Base calculations: count of unique urls and average confidence
        avg_confidence = total_confidence / max(len(evidence), 1)
        sources_multiplier = min(len(unique_urls) * 20.0, 50.0) # up to 50% for multiple urls
        
        score = (avg_confidence * 50.0) + sources_multiplier
        score = max(min(score, 100.0), 0.0)
        
        positive_factors.append(f"Verifiable sources count: {len(unique_urls)}")
        positive_factors.append(f"Average source confidence: {avg_confidence * 100:.1f}%")
        
        if score >= 70.0:
            classification = "STRONG"
        elif score >= 40.0:
            classification = "MODERATE"
        else:
            classification = "WEAK"
            
        return {
            "score": score,
            "classification": classification,
            "positive_factors": positive_factors,
            "negative_factors": negative_factors,
            "evidence_ids": evidence_ids
        }


class BuyingWindowScorer:
    @staticmethod
    def calculate_score(company: LeadCompany) -> Dict[str, Any]:
        """
        Assess operational momentum triggers (hiring, locations growth, etc.).
        """
        positive_factors = []
        negative_factors = []
        
        signals = CompanySignal.objects.filter(company=company, status='ACTIVE')
        
        score = 50.0 # base score showing neutral timing
        hiring_found = False
        expansion_found = False
        
        for cs in signals:
            cat = cs.signal.category.upper() if cs.signal else "OTHER"
            if cat == "HIRING":
                hiring_found = True
                score = min(score + 25.0, 100.0)
                positive_factors.append("Active recruitment expansion detected.")
            elif cat == "EXPANSION":
                expansion_found = True
                score = min(score + 20.0, 100.0)
                positive_factors.append("New location expansion detected.")

        if not hiring_found and not expansion_found:
            score = 40.0
            negative_factors.append("No active hiring or growth indicators found.")
            
        if score >= 75.0:
            classification = "OPTIMAL"
        elif score >= 50.0:
            classification = "ACTIVE"
        else:
            classification = "COLD"
            
        return {
            "score": score,
            "classification": classification,
            "positive_factors": positive_factors,
            "negative_factors": negative_factors
        }


class OverallQualificationScorer:
    @staticmethod
    def run_scoring(
        company: LeadCompany,
        campaign: ProspectingCampaign,
        weights: Dict[str, float] = None
    ) -> Qualification:
        """
        Orchestrate scoring pipelines, compile recommendations, and persist score record.
        """
        if weights is None:
            # Default scoring weights matching campaign defaults
            weights = {
                "problem_fit": 0.5,
                "evidence_strength": 0.3,
                "buying_window": 0.2
            }
            
        # 1. Run individual scoring engines
        fit_res = ProblemFitScorer.calculate_score(company, campaign)
        ev_res = EvidenceStrengthScorer.calculate_score(company)
        timing_res = BuyingWindowScorer.calculate_score(company)
        
        # 2. Compute weighted overall score
        overall = (
            (fit_res["score"] * weights["problem_fit"]) +
            (ev_res["score"] * weights["evidence_strength"]) +
            (timing_res["score"] * weights["buying_window"])
        )
        
        # 3. Determine recommended action
        if overall >= 75.0:
            action = "HIGH_PRIORITY_OUTREACH"
        elif overall >= 50.0:
            action = "ENRICH_AND_MONITOR"
        else:
            action = "ARCHIVE_OR_SKIP"

        # 4. Find next analysis version increment
        latest_version = Qualification.objects.filter(
            company=company,
            campaign=campaign
        ).order_by('-analysis_version').values_list('analysis_version', flat=True).first()
        
        next_version = (latest_version or 0) + 1
        
        # 5. Persist to DB
        qual = Qualification.objects.create(
            company=company,
            campaign=campaign,
            analysis_version=next_version,
            problem_fit_score=Decimal(str(round(fit_res["score"], 2))),
            evidence_strength_score=Decimal(str(round(ev_res["score"], 2))),
            buying_window_score=Decimal(str(round(timing_res["score"], 2))),
            overall_score=Decimal(str(round(overall, 2))),
            fit_class=fit_res["classification"],
            buying_window_class=timing_res["classification"],
            explanation={
                "overall_classification": action,
                "weights_used": weights,
                "evidence_ids": ev_res["evidence_ids"],
                "positive_factors": fit_res["positive_factors"] + ev_res["positive_factors"] + timing_res["positive_factors"],
                "negative_factors": fit_res["negative_factors"] + ev_res["negative_factors"] + timing_res["negative_factors"],
                "unknowns": fit_res["unknowns"]
            },
            positive_factors=fit_res["positive_factors"] + ev_res["positive_factors"] + timing_res["positive_factors"],
            negative_factors=fit_res["negative_factors"] + ev_res["negative_factors"] + timing_res["negative_factors"],
            unknowns=fit_res["unknowns"]
        )
        
        return qual
