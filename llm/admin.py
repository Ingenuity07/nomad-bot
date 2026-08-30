from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from unfold.admin import ModelAdmin
from llm.models import LLMPrompt, PromptRun, AgentConfig


class ActiveListFilter(SimpleListFilter):
    title = 'Status'
    parameter_name = 'is_active'

    def lookups(self, request, model_admin):
        return (
            ('active', 'Active (Default)'),
            ('inactive', 'Inactive'),
            ('all', 'All'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'active' or self.value() is None:
            return queryset.filter(is_active=True)
        if self.value() == 'inactive':
            return queryset.filter(is_active=False)
        return queryset


@admin.register(LLMPrompt)
class LLMPromptAdmin(ModelAdmin):
    list_display = ('key', 'version', 'is_active', 'created_at', 'description')
    list_filter = (ActiveListFilter,)
    search_fields = ('key', 'description')
    ordering = ('key', '-version')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('key', 'version')
        return ()


@admin.register(PromptRun)
class PromptRunAdmin(ModelAdmin):
    list_display = (
        'created_at',
        'operation',
        'prompt_key',
        'provider',
        'llm_model',
        'status',
        'input_tokens',
        'output_tokens',
        'total_cost',
        'duration_ms',
    )
    list_filter = ('provider', 'status')
    search_fields = ('correlation_id', 'trace_id', 'operation', 'error_message', 'prompt_key')
    ordering = ('-created_at',)
    show_full_result_count = True

    # Telemetry is read-only — never allow add or edit, but DO allow view
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    # Computed column to avoid name clash with Python builtin 'model'
    @admin.display(description='LLM Model')
    def llm_model(self, obj):
        return obj.model

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
                'rendered_prompt',
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
                'cost_usd',
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
class AgentConfigAdmin(ModelAdmin):
    list_display = ('name', 'model_tier', 'temperature', 'created_at')
    list_filter = ('model_tier',)
    search_fields = ('name', 'system_prompt')
