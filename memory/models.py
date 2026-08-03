import uuid
from django.db import models

class UserProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    linkedin_url = models.URLField(max_length=500, blank=True, null=True)
    github_url = models.URLField(max_length=500, blank=True, null=True)
    portfolio_url = models.URLField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.username

class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='conversations')
    title = models.CharField(max_length=255, blank=True, null=True)
    selected_model = models.CharField(max_length=100, blank=True, null=True)
    selected_provider = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or str(self.id)

class Message(models.Model):
    ROLE_CHOICES = (
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('system', 'System'),
        ('tool', 'Tool'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    provider = models.CharField(max_length=100, blank=True, null=True)
    model = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.role}: {self.content[:50]}"

class AgentRun(models.Model):
    STATUS_CHOICES = (
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='agent_runs')
    agent_type = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0.000000)

    def __str__(self):
        return f"{self.agent_type} - {self.status}"

class ToolExecution(models.Model):
    STATUS_CHOICES = (
        ('success', 'Success'),
        ('error', 'Error'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name='tool_executions')
    tool_name = models.CharField(max_length=100)
    input_data = models.JSONField(default=dict)
    output_data = models.JSONField(default=dict, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    executed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.tool_name} - {self.status}"


class AgentCheckpoint(models.Model):
    thread_id = models.CharField(max_length=255)
    checkpoint_id = models.CharField(max_length=255)
    parent_checkpoint_id = models.CharField(max_length=255, null=True, blank=True)
    checkpoint_data = models.BinaryField()
    metadata_data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('thread_id', 'checkpoint_id')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.thread_id} - {self.checkpoint_id}"


class AgentCheckpointWrite(models.Model):
    thread_id = models.CharField(max_length=255)
    checkpoint_id = models.CharField(max_length=255)
    task_id = models.CharField(max_length=255)
    idx = models.IntegerField()
    channel = models.CharField(max_length=255)
    value = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('thread_id', 'checkpoint_id', 'task_id', 'idx')
        ordering = ['created_at']

    def __str__(self):
        return f"{self.thread_id} - {self.checkpoint_id} - {self.task_id} - {self.channel}"


class AgentMemory(models.Model):
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='memories')
    category = models.CharField(max_length=50)  # e.g., 'preference', 'profile', 'experience'
    key = models.CharField(max_length=100)       # e.g., 'blocked_companies', 'tech_stack'
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user_profile', 'category', 'key')

    def __str__(self):
        return f"{self.user_profile.username} - {self.category}:{self.key}"


# ==========================================
# NOMAD V3: PROFESSIONAL KNOWLEDGE BASE & RESUME OS
# ==========================================

class ProfessionalKnowledgeBase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.OneToOneField(UserProfile, on_delete=models.CASCADE, related_name='knowledge_base')
    title = models.CharField(max_length=255, default="Senior Software Engineer")
    headline = models.CharField(max_length=500, blank=True, null=True)
    summary = models.TextField(blank=True, null=True)
    years_of_experience = models.IntegerField(default=0)
    target_roles = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"KnowledgeBase for {self.user_profile.username}"


class Experience(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='experiences')
    company = models.CharField(max_length=255)
    role = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, null=True)
    start_date = models.CharField(max_length=50) # e.g. "Jan 2022"
    end_date = models.CharField(max_length=50, default="Present") # e.g. "Present" or "Dec 2023"
    is_current = models.BooleanField(default=False)
    summary = models.TextField(blank=True, null=True)
    bullet_points = models.JSONField(default=list) # List of achievement bullets
    tech_stack = models.JSONField(default=list)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']

    def __str__(self):
        return f"{self.role} at {self.company}"


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='projects')
    title = models.CharField(max_length=255)
    description = models.TextField()
    architecture_notes = models.TextField(blank=True, null=True)
    tech_stack = models.JSONField(default=list)
    impact_metrics = models.JSONField(default=list)
    project_url = models.URLField(max_length=500, blank=True, null=True)
    github_url = models.URLField(max_length=500, blank=True, null=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.title


class Skill(models.Model):
    CATEGORY_CHOICES = (
        ('languages', 'Languages'),
        ('frameworks', 'Frameworks & Libraries'),
        ('databases', 'Databases & Storage'),
        ('cloud_devops', 'Cloud & DevOps'),
        ('ai_ml', 'AI & Machine Learning'),
        ('tools', 'Tools & Platforms'),
        ('concepts', 'Concepts & Architecture'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='skills')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    name = models.CharField(max_length=100)
    proficiency = models.CharField(max_length=50, default="Expert") # Expert, Advanced, Intermediate
    years_experience = models.IntegerField(default=1)

    class Meta:
        unique_together = ('user_profile', 'name')
        ordering = ['category', 'name']

    def __str__(self):
        return f"{self.name} ({self.category})"


class JobPosting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='job_postings')
    company_name = models.CharField(max_length=255)
    job_title = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, null=True)
    job_url = models.URLField(max_length=500, blank=True, null=True)
    raw_text = models.TextField()
    parsed_summary = models.TextField(blank=True, null=True)
    required_skills = models.JSONField(default=list)
    preferred_skills = models.JSONField(default=list)
    responsibilities = models.JSONField(default=list)
    ats_keywords = models.JSONField(default=list)
    experience_years_required = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.job_title} at {self.company_name}"


class ResumeTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    latex_source = models.TextField()  # Jinja2 template content
    preview_image = models.CharField(max_length=500, blank=True, null=True)
    supported_sections = models.JSONField(default=list)
    variables = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ResumeVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='resume_versions')
    job_posting = models.ForeignKey(JobPosting, on_delete=models.SET_NULL, null=True, blank=True, related_name='resumes')
    resume_template = models.ForeignKey(ResumeTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='resumes')
    version_name = models.CharField(max_length=255)
    template_name = models.CharField(max_length=100, default="modern")
    structured_spec = models.JSONField(default=dict)
    latex_code = models.TextField()
    pdf_path = models.CharField(max_length=500, blank=True, null=True)
    ats_score = models.IntegerField(default=0)
    generation_latency_ms = models.IntegerField(default=0)
    llm_provider = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.version_name} (ATS: {self.ats_score}%)"


class ATSReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resume_version = models.OneToOneField(ResumeVersion, on_delete=models.CASCADE, related_name='ats_report')
    match_score = models.IntegerField(default=0)
    present_skills = models.JSONField(default=list)
    missing_skills = models.JSONField(default=list)
    weak_skills = models.JSONField(default=list)
    formatting_score = models.IntegerField(default=100)
    improvement_suggestions = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ATS Report for {self.resume_version.version_name}: {self.match_score}%"


class ApplicationTracker(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('tailored', 'Tailored'),
        ('applied', 'Applied'),
        ('interview', 'Interviewing'),
        ('rejected', 'Rejected'),
        ('offer', 'Offer Received'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='applications')
    job_posting = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name='applications')
    resume_version = models.ForeignKey(ResumeVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='applications')
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='draft')
    applied_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.job_posting.company_name} - {self.status}"


# ==========================================
# NOMAD V4: PROFESSIONAL KNOWLEDGE GRAPH & PROMPT AUDITING
# ==========================================

class SkillExperienceLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='experience_links')
    experience = models.ForeignKey(Experience, on_delete=models.CASCADE, related_name='skill_links')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('skill', 'experience')

    def __str__(self):
        return f"{self.skill.name} used in {self.experience.company}"


class SkillProjectLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='project_links')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='skill_links')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('skill', 'project')

    def __str__(self):
        return f"{self.skill.name} demonstrated in {self.project.title}"


class ProjectExperienceLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='experience_links')
    experience = models.ForeignKey(Experience, on_delete=models.CASCADE, related_name='project_links')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('project', 'experience')

    def __str__(self):
        return f"{self.project.title} done at {self.experience.company}"


class PromptRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=100)  # ingestion, tailoring, optimization
    prompt_text = models.TextField()
    response_text = models.TextField()
    model_name = models.CharField(max_length=100)
    temperature = models.FloatField(default=0.0)
    tokens_used = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    ats_score = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.purpose} run on {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class DiscoveryRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='discovery_runs')
    keyword = models.CharField(max_length=200)
    location = models.CharField(max_length=200)
    status = models.CharField(max_length=50, default='pending')  # pending, running, completed, failed
    total_leads_found = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Discovery Run: {self.keyword} in {self.location} ({self.status})"


class LeadCompany(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery_run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name='companies')
    name = models.CharField(max_length=255)
    website = models.URLField(max_length=500, null=True, blank=True)
    phone = models.CharField(max_length=100, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    rating = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class LeadContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=100, null=True, blank=True)
    linkedin = models.URLField(max_length=500, null=True, blank=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    source = models.CharField(max_length=255, default='website')
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

