from typing import Optional

from knowledge_base.models import UserProfile
from prospecting.models import (
    DiscoveryLead,
    DiscoveryRun,
    ProspectingCampaign,
    ProspectingRequest,
    get_default_workspace,
)


def _campaign_name(keyword: str) -> str:
    value = " ".join((keyword or "").split())
    return (value[:255] or "Prospecting campaign")


def ensure_campaign_for_run(
    run: DiscoveryRun,
    *,
    user: Optional[UserProfile] = None,
    prospecting_request: Optional[ProspectingRequest] = None,
    product_description: str = "",
    problem_statement: str = "",
    geography: Optional[dict] = None,
) -> ProspectingCampaign:
    """Create/reuse the campaign that owns a discovery run and link its leads."""
    if run.campaign_id:
        campaign = run.campaign
    else:
        request = prospecting_request or run.prospecting_request
        campaign = None
        if request:
            campaign = ProspectingCampaign.objects.filter(
                prospecting_request=request
            ).first()

        if campaign is None:
            campaign = ProspectingCampaign.objects.create(
                workspace=get_default_workspace(),
                created_by=user or run.user_profile,
                prospecting_request=request,
                name=_campaign_name(run.keyword),
                description=(request.raw_target if request else "") or run.keyword,
                product_description=product_description or (
                    (request.raw_objective if request else "") or run.keyword
                ),
                problem_statement=problem_statement or (
                    (request.raw_qualification if request else "") or run.keyword
                ),
                geography=geography or {"location": run.location},
                status="ACTIVE",
            )

        run.campaign = campaign
        run.save(update_fields=['campaign'])

    # New companies get a direct campaign FK. DiscoveryLead remains the source
    # of truth for deduplicated companies that can appear in several campaigns.
    run.companies.filter(campaign__isnull=True).update(campaign=campaign)
    for company_id in run.companies.values_list('id', flat=True):
        DiscoveryLead.objects.get_or_create(
            discovery_run=run,
            company_id=company_id,
        )
    return campaign
