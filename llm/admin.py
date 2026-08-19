from django.contrib import admin
from django.utils.safestring import mark_safe
from llm.models import LLMPrompt, PromptRun, AgentConfig

@admin.register(LLMPrompt)
class LLMPromptAdmin(admin.ModelAdmin):
    list_display = ('key', 'version', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('key', 'description')
    ordering = ('key', '-version')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            # Once saved, key and version are immutable to preserve historical records
            return ('key', 'version')
        return ()


@admin.register(PromptRun)
class PromptRunAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'correlation_id',
        'operation',
        'provider',
        'model',
        'status',
        'tokens_used',
        'cost_usd',
        'latency_ms'
    )
    list_filter = ('provider', 'model', 'status', 'created_at')
    search_fields = ('correlation_id', 'trace_id', 'operation', 'error_message', 'prompt_key')
    ordering = ('-created_at',)

    # Make everything read-only to prevent editing history logs
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def get_queryset(self, request):
        return super().get_queryset(request).using('telemetry')

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'correlation_id', 'trace_id', 'span_id', 'operation', 'purpose')
        }),
        ('Prompt Context', {
            'fields': (
                'prompt_key',
                'prompt_version',
                'template_variables',
                'prompt_text',
                'rendered_prompt'
            )
        }),
        ('Model Output', {
            'fields': ('response_text',)
        }),
        ('Execution Metrics', {
            'fields': ('provider', 'model', 'latency_ms', 'duration_ms', 'status', 'retry_count')
        }),
        ('Error Diagnostics', {
            'fields': ('error_type', 'error_code', 'error_message', 'provider_status_code')
        }),
        ('Token Accounting & Costs', {
            'fields': (
                'input_tokens',
                'output_tokens',
                'tokens_used',
                'input_cost',
                'output_cost',
                'total_cost',
                'cost_usd'
            )
        }),
        ('Metadata', {
            'fields': ('metadata',)
        }),
        ('Timestamps', {
            'fields': ('started_at', 'completed_at', 'created_at')
        }),
    )


@admin.register(AgentConfig)
class AgentConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'model_tier', 'temperature', 'created_at')
    list_filter = ('model_tier', 'created_at')
    search_fields = ('name', 'system_prompt')

