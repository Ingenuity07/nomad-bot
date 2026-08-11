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
