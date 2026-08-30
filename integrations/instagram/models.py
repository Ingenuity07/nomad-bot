import uuid
from datetime import timedelta
from django.db import models
from django.utils import timezone
from knowledge_base.models import UserProfile
from prospecting.models import Workspace
from integrations.instagram.security import encrypt_token, decrypt_token, generate_oauth_state


class InstagramAccount(models.Model):
    """
    Represents a connected Instagram Professional / Business account.
    """
    STATUS_CHOICES = [
        ('CONNECTED', 'Connected'),
        ('DISCONNECTED', 'Disconnected'),
        ('EXPIRED', 'Token Expired'),
        ('ERROR', 'Connection Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='instagram_accounts'
    )
    created_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='instagram_accounts'
    )
    instagram_user_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Meta Instagram-scoped user ID"
    )
    facebook_page_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Connected Facebook Page ID"
    )
    username = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Instagram handle/username"
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Account display name"
    )
    profile_picture_url = models.URLField(
        max_length=2000,
        blank=True,
        null=True
    )
    encrypted_access_token = models.TextField(
        blank=True,
        default='',
        help_text="Fernet-encrypted long-lived access token"
    )
    token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Expiry timestamp of current access token"
    )
    token_last_refreshed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last token refresh"
    )
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default='CONNECTED',
        db_index=True
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Last recorded error description if status is ERROR or EXPIRED"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Supplemental account metadata from Graph API (e.g. followers, biography)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workspace', 'status']),
            models.Index(fields=['username']),
        ]

    def __str__(self):
        return f"@{self.username} ({self.status})"

    def set_access_token(self, raw_token: str) -> None:
        """
        Encrypt and store the raw access token.
        """
        if raw_token:
            self.encrypted_access_token = encrypt_token(raw_token)
        else:
            self.encrypted_access_token = ''

    def get_access_token(self) -> str:
        """
        Decrypt and return the raw access token.
        """
        if not self.encrypted_access_token:
            return ''
        return decrypt_token(self.encrypted_access_token)

    @property
    def is_token_expired(self) -> bool:
        """
        Check if the access token is past its expiry date.
        """
        if not self.token_expires_at:
            return False
        return timezone.now() >= self.token_expires_at


class InstagramOAuthState(models.Model):
    """
    Ephemeral state correlation record to prevent CSRF and correlate users during OAuth flow.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Cryptographically random token"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='instagram_oauth_states'
    )
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='instagram_oauth_states'
    )
    redirect_uri = models.URLField(
        max_length=2000,
        blank=True,
        null=True
    )
    is_used = models.BooleanField(
        default=False,
        db_index=True
    )
    expires_at = models.DateTimeField(
        db_index=True
    )
    metadata = models.JSONField(
        default=dict,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['state', 'is_used']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"OAuth State: {self.state[:12]}... (used={self.is_used})"

    @classmethod
    def create_state(
        cls,
        workspace: Workspace,
        user_profile: UserProfile = None,
        redirect_uri: str = None,
        ttl_minutes: int = 15,
        metadata: dict = None
    ) -> 'InstagramOAuthState':
        """
        Factory helper to create a new secure OAuth state record.
        """
        return cls.objects.create(
            state=generate_oauth_state(),
            workspace=workspace,
            user_profile=user_profile,
            redirect_uri=redirect_uri,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
            metadata=metadata or {}
        )

    @property
    def is_valid(self) -> bool:
        """
        Check whether this state is unused and has not expired.
        """
        return (not self.is_used) and (timezone.now() < self.expires_at)

    def mark_as_used(self) -> None:
        """
        Mark this state record as consumed.
        """
        self.is_used = True
        self.save(update_fields=['is_used'])


class InstagramWebhookEvent(models.Model):
    """
    Immutable ingestion log of incoming Instagram webhooks for idempotent background processing.
    """
    STATUS_CHOICES = [
        ('RECEIVED', 'Received'),
        ('PROCESSING', 'Processing'),
        ('PROCESSED', 'Processed'),
        ('IGNORED', 'Ignored'),
        ('FAILED', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instagram_account = models.ForeignKey(
        InstagramAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events'
    )
    event_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique event deduplication identifier"
    )
    event_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Event type (e.g. comments, mentions, messages)"
    )
    sender_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text="Instagram-scoped ID of the actor"
    )
    sender_username = models.CharField(
        max_length=255,
        null=True,
        blank=True
    )
    media_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text="Instagram Media/Post ID"
    )
    comment_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text="Instagram Comment ID if applicable"
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Sanitized webhook payload structure"
    )
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default='RECEIVED',
        db_index=True
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True
    )
    error_message = models.TextField(
        null=True,
        blank=True
    )
    retry_count = models.IntegerField(
        default=0
    )
    received_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['status', 'received_at']),
            models.Index(fields=['event_type', 'status']),
            models.Index(fields=['comment_id']),
            models.Index(fields=['media_id']),
        ]

    def __str__(self):
        return f"WebhookEvent {self.event_type} [{self.status}] (id={self.event_id})"


class InstagramAutomation(models.Model):
    """
    Configuration model for keyword-based comment replies & private message triggers.
    """
    TRIGGER_CHOICES = [
        ('ANY_COMMENT', 'Any Comment'),
        ('KEYWORD_COMMENT', 'Specific Keyword in Comment'),
    ]

    TARGET_MEDIA_CHOICES = [
        ('ALL_POSTS', 'All Posts & Reels'),
        ('SELECTED_POSTS', 'Specific Posts / Reels'),
    ]

    MATCH_CHOICES = [
        ('EXACT', 'Exact Match'),
        ('CONTAINS', 'Contains Keyword'),
        ('REGEX', 'Regular Expression'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='instagram_automations'
    )
    instagram_account = models.ForeignKey(
        InstagramAccount,
        on_delete=models.CASCADE,
        related_name='automations'
    )
    name = models.CharField(
        max_length=255,
        help_text="Descriptive automation title"
    )
    description = models.TextField(
        blank=True,
        null=True
    )
    trigger_type = models.CharField(
        max_length=50,
        choices=TRIGGER_CHOICES,
        default='KEYWORD_COMMENT'
    )
    target_media_type = models.CharField(
        max_length=50,
        choices=TARGET_MEDIA_CHOICES,
        default='ALL_POSTS'
    )
    keywords = models.JSONField(
        default=list,
        blank=True,
        help_text="List of case-insensitive trigger keywords (e.g. ['price', 'info', 'book'])"
    )
    match_type = models.CharField(
        max_length=50,
        choices=MATCH_CHOICES,
        default='CONTAINS'
    )
    public_reply_enabled = models.BooleanField(
        default=False,
        help_text="Whether to post a public reply to the user's comment"
    )
    public_reply_templates = models.JSONField(
        default=list,
        blank=True,
        help_text="Rotating list of public comment replies"
    )
    private_reply_enabled = models.BooleanField(
        default=True,
        help_text="Whether to send a private direct message to the commenter"
    )
    private_reply_message = models.TextField(
        blank=True,
        null=True,
        help_text="Direct message text content sent to the commenter"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True
    )
    priority = models.IntegerField(
        default=0,
        help_text="Evaluation priority order (higher priority evaluated first)"
    )
    stats = models.JSONField(
        default=dict,
        blank=True,
        help_text="Runtime statistics (triggers_count, dms_sent, replies_posted)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', '-created_at']
        indexes = [
            models.Index(fields=['workspace', 'is_active']),
            models.Index(fields=['instagram_account', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} (@{self.instagram_account.username}) [Active={self.is_active}]"


class InstagramAutomationMedia(models.Model):
    """
    Junction model mapping an automation to specific Instagram media posts/reels.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    automation = models.ForeignKey(
        InstagramAutomation,
        on_delete=models.CASCADE,
        related_name='selected_media'
    )
    instagram_media_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Meta Instagram Media ID"
    )
    media_url = models.URLField(
        max_length=2000,
        blank=True,
        null=True
    )
    permalink = models.URLField(
        max_length=2000,
        blank=True,
        null=True
    )
    caption = models.TextField(
        blank=True,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('automation', 'instagram_media_id')
        indexes = [
            models.Index(fields=['instagram_media_id']),
        ]

    def __str__(self):
        return f"Media {self.instagram_media_id} for {self.automation.name}"


class InstagramAutomationAction(models.Model):
    """
    Execution and idempotency record tracking actions dispatched for a specific comment/event.
    """
    ACTION_CHOICES = [
        ('PUBLIC_REPLY', 'Public Comment Reply'),
        ('PRIVATE_REPLY', 'Private Direct Message'),
        ('CRM_RECORD_CREATED', 'CRM Lead Created'),
        ('CRM_ACTIVITY_LOGGED', 'CRM Activity Logged'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('EXECUTED', 'Executed'),
        ('SKIPPED', 'Skipped'),
        ('FAILED', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    automation = models.ForeignKey(
        InstagramAutomation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actions'
    )
    webhook_event = models.ForeignKey(
        InstagramWebhookEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actions'
    )
    instagram_account = models.ForeignKey(
        InstagramAccount,
        on_delete=models.CASCADE,
        related_name='automation_actions'
    )
    action_type = models.CharField(
        max_length=50,
        choices=ACTION_CHOICES
    )
    recipient_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Instagram user ID of the recipient"
    )
    recipient_username = models.CharField(
        max_length=255,
        null=True,
        blank=True
    )
    comment_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True
    )
    media_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True
    )
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique key ensuring actions are not duplicated (e.g. comment_id:action_type)"
    )
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default='PENDING',
        db_index=True
    )
    execution_result = models.JSONField(
        default=dict,
        blank=True
    )
    error_message = models.TextField(
        null=True,
        blank=True
    )
    executed_at = models.DateTimeField(
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['idempotency_key']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['recipient_id', 'action_type']),
        ]

    def __str__(self):
        return f"{self.action_type} for {self.recipient_username or self.recipient_id} [{self.status}]"
