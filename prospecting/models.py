import uuid
from django.db import models
from knowledge_base.models import UserProfile

class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=100, default='UTC')
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


def get_default_workspace():
    workspace, _ = Workspace.objects.get_or_create(
        name="Default Workspace",
        defaults={"timezone": "UTC"}
    )
    return workspace


class ProspectingCampaign(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('PAUSED', 'Paused'),
        ('COMPLETED', 'Completed'),
        ('ARCHIVED', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    product_description = models.TextField()
    problem_statement = models.TextField()
    geography = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='DRAFT')
    created_by = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='campaigns')
    prospecting_request = models.OneToOneField(
        'ProspectingRequest',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='campaign'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['workspace']),
            models.Index(fields=['workspace', 'status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"


class ICPProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='icp_profiles')
    version = models.IntegerField(default=1)
    industries = models.JSONField(default=list, blank=True)
    company_sizes = models.JSONField(default=list, blank=True)
    geographies = models.JSONField(default=list, blank=True)
    required_signals = models.JSONField(default=list, blank=True)
    negative_signals = models.JSONField(default=list, blank=True)
    target_roles = models.JSONField(default=list, blank=True)
    exclusions = models.JSONField(default=list, blank=True)
    search_terms = models.JSONField(default=list, blank=True)
    scoring_weights = models.JSONField(default=dict, blank=True)
    generated_by_model = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('campaign', 'version')

    def __str__(self):
        return f"ICP Profile for {self.campaign.name} (v{self.version})"


class ProblemSignal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='problem_signals', null=True, blank=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100)
    description = models.TextField()
    signal_type = models.CharField(max_length=100)
    detection_method = models.CharField(max_length=100)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class DiscoveryRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='discovery_runs')
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='discovery_runs', null=True, blank=True)
    keyword = models.CharField(max_length=1000)
    location = models.CharField(max_length=1000)
    status = models.CharField(max_length=50, default='pending')
    total_leads_found = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    discovery = models.ForeignKey('Discovery', on_delete=models.SET_NULL, null=True, blank=True, related_name='runs')
    prospecting_request = models.ForeignKey('ProspectingRequest', on_delete=models.SET_NULL, null=True, blank=True, related_name='discovery_runs')
    specification_version = models.ForeignKey('ProspectingSpecificationVersion', on_delete=models.SET_NULL, null=True, blank=True, related_name='discovery_runs')

    def __str__(self):
        return f"Discovery Run: {self.keyword} in {self.location} ({self.status})"


class LeadCompany(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery_run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name='companies', null=True, blank=True)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='companies', null=True, blank=True)
    name = models.CharField(max_length=255)
    website = models.URLField(max_length=2000, null=True, blank=True)
    phone = models.CharField(max_length=100, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    rating = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CompanySource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='sources')
    provider = models.CharField(max_length=100)
    source_type = models.CharField(max_length=100)
    source_url = models.URLField(max_length=2000, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    raw_reference = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('provider', 'external_id')

    def __str__(self):
        return f"{self.provider} source for {self.company.name}"


class LeadContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=100, null=True, blank=True)
    linkedin = models.URLField(max_length=2000, null=True, blank=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    source = models.CharField(max_length=2000, default='website')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} ({self.company.name})"


class WebsiteAnalysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(LeadCompany, on_delete=models.CASCADE, related_name='analysis')
    description = models.TextField(null=True, blank=True)
    has_delivery = models.BooleanField(default=False)
    has_scheduling = models.BooleanField(default=False)
    needs_routing = models.BooleanField(default=False)
    fleet_size_estimate = models.CharField(max_length=100, default='unknown')
    lead_score = models.FloatField(default=0.0)
    lead_score_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analysis for {self.company.name} (Score: {self.lead_score})"


class CampaignLeadInsight(models.Model):
    """Current, campaign-specific LLM interpretation of a discovered company."""

    FIT_LEVEL_CHOICES = [
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('LOW', 'Low'),
        ('UNKNOWN', 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        LeadCompany,
        on_delete=models.CASCADE,
        related_name='campaign_insights',
    )
    campaign = models.ForeignKey(
        ProspectingCampaign,
        on_delete=models.CASCADE,
        related_name='lead_insights',
    )
    schema_version = models.PositiveSmallIntegerField(default=1)
    company_summary = models.TextField(blank=True, default='')
    industry = models.CharField(max_length=255, blank=True, default='')
    business_model = models.CharField(max_length=255, blank=True, default='')
    services = models.JSONField(default=list, blank=True)
    operational_profile = models.JSONField(default=dict, blank=True)
    fit_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fit_level = models.CharField(max_length=20, choices=FIT_LEVEL_CHOICES, default='UNKNOWN')
    fit_reason = models.TextField(blank=True, default='')
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    positive_factors = models.JSONField(default=list, blank=True)
    negative_factors = models.JSONField(default=list, blank=True)
    data_gaps = models.JSONField(default=list, blank=True)
    recommended_next_step = models.TextField(blank=True, default='')
    talking_points = models.JSONField(default=list, blank=True)
    source_urls = models.JSONField(default=list, blank=True)
    analyzed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'campaign'],
                name='unique_company_campaign_insight',
            ),
        ]
        indexes = [
            models.Index(fields=['campaign', '-fit_score'], name='prospectin_campaig_5a8612_idx'),
            models.Index(fields=['company', 'campaign'], name='prospectin_company_889c82_idx'),
        ]

    def __str__(self):
        score = self.fit_score if self.fit_score is not None else 'pending'
        return f"{self.company.name} for {self.campaign.name} ({score})"


class Evidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='evidence_records')
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='evidence_records', null=True, blank=True)
    signal = models.ForeignKey(ProblemSignal, on_delete=models.SET_NULL, related_name='evidence_records', null=True, blank=True)
    source_type = models.CharField(max_length=100)
    source_url = models.URLField(max_length=2000)
    source_title = models.CharField(max_length=255, null=True, blank=True)
    evidence_text = models.TextField()
    structured_value = models.JSONField(default=dict, blank=True, null=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    captured_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['company', 'captured_at']),
            models.Index(fields=['company', 'signal']),
            models.Index(fields=['campaign', 'signal']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"Evidence for {self.company.name} - {self.source_type}"


class CompanySignal(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('EXPIRED', 'Expired'),
        ('CONTRADICTED', 'Contradicted'),
        ('UNKNOWN', 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='signals')
    signal = models.ForeignKey(ProblemSignal, on_delete=models.CASCADE, related_name='company_links')
    value = models.JSONField(default=dict, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='ACTIVE')
    first_detected_at = models.DateTimeField(auto_now_add=True)
    last_detected_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company.name} - {self.signal.name} ({self.status})"


class Qualification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='qualifications')
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='qualifications')
    analysis_version = models.IntegerField(default=1)
    problem_fit_score = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    evidence_strength_score = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    buying_window_score = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    fit_class = models.CharField(max_length=50, default='UNKNOWN')
    buying_window_class = models.CharField(max_length=50, default='UNKNOWN')
    explanation = models.JSONField(default=dict, blank=True)
    positive_factors = models.JSONField(default=list, blank=True)
    negative_factors = models.JSONField(default=list, blank=True)
    unknowns = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'campaign', 'analysis_version')

    def __str__(self):
        return f"Qual: {self.company.name} for {self.campaign.name} (v{self.analysis_version})"


class Person(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='people')
    name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100, null=True, blank=True)
    title = models.CharField(max_length=255, null=True, blank=True)
    linkedin_url = models.URLField(max_length=2000, null=True, blank=True)
    other_public_profile = models.URLField(max_length=2000, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ContactPoint(models.Model):
    TYPE_CHOICES = [
        ('EMAIL', 'Email'),
        ('PHONE', 'Phone'),
        ('LINKEDIN', 'Linkedin'),
        ('OTHER', 'Other'),
    ]

    VERIFICATION_CHOICES = [
        ('UNKNOWN', 'Unknown'),
        ('VALID', 'Valid'),
        ('INVALID', 'Invalid'),
        ('RISKY', 'Risky'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='contact_points')
    type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    value = models.CharField(max_length=255)
    source = models.CharField(max_length=2000, default='website')
    verification_status = models.CharField(max_length=50, choices=VERIFICATION_CHOICES, default='UNKNOWN')
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.type}: {self.value}"


class BuyingGroupMember(models.Model):
    ROLE_CHOICES = [
        ('DECISION_MAKER', 'Decision Maker'),
        ('PROBLEM_OWNER', 'Problem Owner'),
        ('CHAMPION', 'Champion'),
        ('INFLUENCER', 'Influencer'),
        ('UNKNOWN', 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='buying_group_members')
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='buying_group_members')
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='buying_group_members')
    role_type = models.CharField(max_length=50, choices=ROLE_CHOICES, default='UNKNOWN')
    relevance_score = models.IntegerField(default=0)
    reason = models.TextField(null=True, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.person.name} - {self.role_type} ({self.company.name})"


class ResearchRun(models.Model):
    STATUS_CHOICES = [
        ('QUEUED', 'Queued'),
        ('RUNNING', 'Running'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='research_runs')
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='research_runs', null=True, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='QUEUED')
    workflow_version = models.IntegerField(default=1)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.JSONField(default=dict, blank=True, null=True)
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0.0)
    token_usage = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Research: {self.company.name} ({self.status})"


class ProviderExecution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='provider_executions')
    provider = models.CharField(max_length=100)
    operation = models.CharField(max_length=100)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='provider_executions', null=True, blank=True)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='provider_executions', null=True, blank=True)
    request_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=50)
    latency_ms = models.IntegerField(default=0)
    units = models.JSONField(default=dict, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0.0, null=True, blank=True)
    error = models.JSONField(default=dict, blank=True, null=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Execution: {self.provider} - {self.operation}"


class CampaignEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='events')
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='events', null=True, blank=True)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='events', null=True, blank=True)
    event_type = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_type} - {self.campaign.name}"


class TargetList(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='target_lists', null=True, blank=True)
    name = models.CharField(max_length=255)
    is_smart = models.BooleanField(default=False)
    criteria = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='target_lists')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ListMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target_list = models.ForeignKey(TargetList, on_delete=models.CASCADE, related_name='memberships')
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='list_memberships')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('target_list', 'company')

    def __str__(self):
        return f"{self.company.name} in {self.target_list.name}"


class CampaignEnrollment(models.Model):
    STATUS_CHOICES = [
        ('ELIGIBLE', 'Eligible'),
        ('ENROLLED', 'Enrolled'),
        ('PAUSED', 'Paused'),
        ('EXCLUDED', 'Excluded'),
        ('COMPLETED', 'Completed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='enrollments')
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='campaign_enrollments')
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='ELIGIBLE')
    enrolled_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('campaign', 'company')

    def __str__(self):
        return f"{self.company.name} enrolled in {self.campaign.name} ({self.status})"


class SalesGuidance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='guidance_records')
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='guidance_records')
    person = models.ForeignKey(Person, on_delete=models.SET_NULL, related_name='guidance_records', null=True, blank=True)
    talking_points = models.JSONField(default=list, blank=True)
    recommended_angle = models.CharField(max_length=255)
    recommended_next_step = models.CharField(max_length=255)
    message_draft = models.TextField()
    risks = models.JSONField(default=list, blank=True)
    unknowns = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Guidance: {self.company.name} - {self.campaign.name}"


class EmailSequence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(ProspectingCampaign, on_delete=models.CASCADE, related_name='sequences')
    name = models.CharField(max_length=255)
    steps = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} for {self.campaign.name}"


class EmailMessage(models.Model):
    STATUS_CHOICES = [
        ('PENDING_APPROVAL', 'Pending Approval'),
        ('APPROVED', 'Approved'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sequence = models.ForeignKey(EmailSequence, on_delete=models.CASCADE, related_name='messages', null=True, blank=True)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='email_messages')
    recipient_email = models.EmailField(max_length=255)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    is_approved = models.BooleanField(default=False)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='PENDING_APPROVAL')
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Email to {self.recipient_email} - Status: {self.status}"


class EmailBounce(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=255, unique=True)
    bounce_type = models.CharField(max_length=50, default='HARD')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Bounced: {self.email} ({self.bounce_type})"


class EmailUnsubscribe(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Unsubscribed: {self.email}"


class InboundReply(models.Model):
    CLASSIFICATION_CHOICES = [
        ('INTERESTED', 'Interested'),
        ('QUESTION', 'Question'),
        ('NOT_NOW', 'Not Now'),
        ('NOT_INTERESTED', 'Not Interested'),
        ('WRONG_PERSON', 'Wrong Person'),
        ('UNSUBSCRIBE', 'Unsubscribe'),
        ('OUT_OF_OFFICE', 'Out Of Office'),
        ('UNKNOWN', 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email_message = models.ForeignKey(EmailMessage, on_delete=models.CASCADE, related_name='replies')
    reply_text = models.TextField()
    classification = models.CharField(max_length=50, choices=CLASSIFICATION_CHOICES, default='UNKNOWN')
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    requires_review = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reply from {self.email_message.recipient_email} - Class: {self.classification}"


class LeadFeedback(models.Model):
    FEEDBACK_CHOICES = [
        ('USEFUL', 'Useful'),
        ('WRONG_MATCH', 'Wrong Match'),
        ('BAD_EVIDENCE', 'Bad Evidence'),
        ('GOOD_EVIDENCE', 'Good Evidence'),
        ('GOOD_SIGNAL', 'Good Signal'),
        ('BAD_SIGNAL', 'Bad Signal'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='feedback')
    feedback_type = models.CharField(max_length=50, choices=FEEDBACK_CHOICES)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Feedback: {self.company.name} - {self.feedback_type}"


class CRMIntegrationRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='crm_records', null=True, blank=True)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='crm_records', null=True, blank=True)
    external_crm = models.CharField(max_length=100, default='MockCRM')
    external_id = models.CharField(max_length=255)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('external_crm', 'external_id')

    def __str__(self):
        return f"CRM Sync: {self.external_crm} - ID: {self.external_id}"


class ProspectingRequest(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PARSING', 'Parsing'),
        ('NEEDS_CLARIFICATION', 'Needs Clarification'),
        ('READY_FOR_REVIEW', 'Ready For Review'),
        ('CONFIRMED', 'Confirmed'),
        ('EXECUTING', 'Executing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='prospecting_requests')
    raw_objective = models.TextField(null=True, blank=True)
    raw_target = models.TextField(null=True, blank=True)
    raw_qualification = models.TextField(null=True, blank=True)
    clarification_history = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='DRAFT')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Request {self.id} ({self.status})"


class ProspectingSpecificationVersion(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('READY_FOR_REVIEW', 'Ready For Review'),
        ('CONFIRMED', 'Confirmed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(ProspectingRequest, on_delete=models.CASCADE, related_name='spec_versions')
    version = models.IntegerField(default=1)
    schema_version = models.CharField(max_length=50, default='v1')
    specification_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='DRAFT')
    parser_model = models.CharField(max_length=255, null=True, blank=True)
    parser_provider = models.CharField(max_length=255, null=True, blank=True)
    prompt_version = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='confirmed_specifications')

    class Meta:
        unique_together = ('request', 'version')

    def __str__(self):
        return f"Spec version {self.version} for Request {self.request.id} ({self.status})"


class Discovery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='discoveries')
    prospecting_request = models.ForeignKey(ProspectingRequest, on_delete=models.CASCADE, related_name='discoveries')
    specification_version = models.ForeignKey(ProspectingSpecificationVersion, on_delete=models.CASCADE, related_name='discoveries')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Discovery {self.id} (Spec Version: {self.specification_version.version})"


class DiscoveryLead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery_run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name='discovery_leads')
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='discovery_leads')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('discovery_run', 'company')

    def __str__(self):
        return f"DiscoveryLead Link: Run {self.discovery_run.id} <-> Company {self.company.name}"

