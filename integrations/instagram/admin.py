from django.contrib import admin
from unfold.admin import ModelAdmin
from integrations.instagram.models import (
    InstagramAccount,
    InstagramOAuthState,
    InstagramWebhookEvent,
    InstagramAutomation,
    InstagramAutomationMedia,
    InstagramAutomationAction,
)
from integrations.instagram.security import mask_token


@admin.register(InstagramAccount)
class InstagramAccountAdmin(ModelAdmin):
    list_display = ('username', 'name', 'status', 'token_status', 'created_at')
    list_filter = ('status', 'workspace')
    search_fields = ('username', 'name', 'instagram_user_id')
    readonly_fields = (
        'id',
        'masked_access_token',
        'token_expires_at',
        'token_last_refreshed_at',
        'created_at',
        'updated_at',
    )
    fields = (
        'workspace',
        'created_by',
        'instagram_user_id',
        'facebook_page_id',
        'username',
        'name',
        'profile_picture_url',
        'status',
        'masked_access_token',
        'token_expires_at',
        'token_last_refreshed_at',
        'error_message',
        'metadata',
        'created_at',
        'updated_at',
    )

    @admin.display(description="Access Token (Encrypted)")
    def masked_access_token(self, obj):
        if not obj.encrypted_access_token:
            return "No token set"
        return mask_token(obj.encrypted_access_token)

    @admin.display(description="Token Valid")
    def token_status(self, obj):
        if not obj.encrypted_access_token:
            return "Missing"
        if obj.is_token_expired:
            return "Expired"
        return "Valid"


@admin.register(InstagramOAuthState)
class InstagramOAuthStateAdmin(ModelAdmin):
    list_display = ('state_preview', 'workspace', 'is_used', 'is_valid_state', 'expires_at', 'created_at')
    list_filter = ('is_used', 'workspace')
    search_fields = ('state',)
    readonly_fields = ('id', 'state', 'created_at')

    @admin.display(description="State")
    def state_preview(self, obj):
        return f"{obj.state[:16]}..."

    @admin.display(boolean=True, description="Active / Valid")
    def is_valid_state(self, obj):
        return obj.is_valid


@admin.register(InstagramWebhookEvent)
class InstagramWebhookEventAdmin(ModelAdmin):
    list_display = ('event_id', 'event_type', 'instagram_account', 'sender_username', 'status', 'received_at')
    list_filter = ('status', 'event_type', 'instagram_account')
    search_fields = ('event_id', 'sender_username', 'comment_id', 'media_id')
    readonly_fields = (
        'id',
        'event_id',
        'event_type',
        'sender_id',
        'sender_username',
        'media_id',
        'comment_id',
        'raw_payload',
        'received_at',
        'created_at',
        'updated_at',
    )


class InstagramAutomationMediaInline(admin.TabularInline):
    model = InstagramAutomationMedia
    extra = 0
    readonly_fields = ('created_at',)


@admin.register(InstagramAutomation)
class InstagramAutomationAdmin(ModelAdmin):
    list_display = ('name', 'instagram_account', 'trigger_type', 'target_media_type', 'is_active', 'priority', 'created_at')
    list_filter = ('is_active', 'trigger_type', 'target_media_type', 'instagram_account')
    search_fields = ('name', 'description')
    inlines = [InstagramAutomationMediaInline]


@admin.register(InstagramAutomationAction)
class InstagramAutomationActionAdmin(ModelAdmin):
    list_display = ('idempotency_key', 'action_type', 'recipient_username', 'instagram_account', 'status', 'executed_at')
    list_filter = ('action_type', 'status', 'instagram_account')
    search_fields = ('idempotency_key', 'recipient_username', 'comment_id')
    readonly_fields = ('id', 'idempotency_key', 'executed_at', 'created_at')
