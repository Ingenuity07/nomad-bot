import uuid
from django.db import models
from knowledge_base.models import UserProfile

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
