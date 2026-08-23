from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import (
    Workspace, ProspectingCampaign, ICPProfile, ProblemSignal,
    DiscoveryRun, LeadCompany, CompanySource, LeadContact, WebsiteAnalysis, CampaignLeadInsight,
    Evidence, CompanySignal, Qualification, Person, ContactPoint,
    BuyingGroupMember, ResearchRun, ProviderExecution, CampaignEvent,
    TargetList, ListMembership, CampaignEnrollment, SalesGuidance,
    EmailSequence, EmailMessage, EmailBounce, EmailUnsubscribe,
    InboundReply, LeadFeedback, CRMIntegrationRecord,
    ProspectingRequest, ProspectingSpecificationVersion, Discovery, DiscoveryLead,
)


@admin.register(Workspace)
class WorkspaceAdmin(ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)


@admin.register(ProspectingCampaign)
class ProspectingCampaignAdmin(ModelAdmin):
    list_display = ('name', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('name',)


@admin.register(ICPProfile)
class ICPProfileAdmin(ModelAdmin):
    list_display = ('campaign', 'created_at')
    search_fields = ('campaign__name',)


@admin.register(ProblemSignal)
class ProblemSignalAdmin(ModelAdmin):
    list_display = ('name', 'signal_type', 'category', 'active', 'created_at')
    list_filter = ('signal_type', 'active')
    search_fields = ('name', 'category')


@admin.register(DiscoveryRun)
class DiscoveryRunAdmin(ModelAdmin):
    list_display = ('id', 'keyword', 'location', 'status', 'total_leads_found', 'started_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('keyword', 'location')
    readonly_fields = ('id', 'started_at', 'completed_at')


@admin.register(LeadCompany)
class LeadCompanyAdmin(ModelAdmin):
    list_display = ('name', 'website', 'phone', 'category', 'rating', 'created_at')
    list_filter = ('category',)
    search_fields = ('name', 'website', 'phone')


@admin.register(CompanySource)
class CompanySourceAdmin(ModelAdmin):
    list_display = ('company', 'provider', 'source_type', 'first_seen_at')
    list_filter = ('provider', 'source_type')
    search_fields = ('company__name', 'provider')


@admin.register(LeadContact)
class LeadContactAdmin(ModelAdmin):
    list_display = ('company', 'email', 'phone', 'role', 'source', 'created_at')
    search_fields = ('email', 'phone', 'role', 'company__name')


@admin.register(WebsiteAnalysis)
class WebsiteAnalysisAdmin(ModelAdmin):
    list_display = ('company', 'lead_score', 'has_delivery', 'has_scheduling', 'needs_routing', 'created_at')
    list_filter = ('has_delivery', 'has_scheduling', 'needs_routing')
    search_fields = ('company__name',)


@admin.register(CampaignLeadInsight)
class CampaignLeadInsightAdmin(ModelAdmin):
    list_display = ('company', 'campaign', 'fit_score', 'fit_level', 'confidence', 'analyzed_at')
    list_filter = ('fit_level', 'campaign')
    search_fields = ('company__name', 'campaign__name', 'fit_reason')


@admin.register(Evidence)
class EvidenceAdmin(ModelAdmin):
    list_display = ('company', 'source_type', 'source_url', 'confidence', 'captured_at')
    list_filter = ('source_type',)
    search_fields = ('company__name', 'source_url')


@admin.register(CompanySignal)
class CompanySignalAdmin(ModelAdmin):
    list_display = ('company', 'signal', 'status', 'confidence', 'first_detected_at')
    list_filter = ('status',)
    search_fields = ('company__name',)


@admin.register(Qualification)
class QualificationAdmin(ModelAdmin):
    list_display = ('company', 'campaign', 'overall_score', 'fit_class', 'buying_window_class', 'created_at')
    list_filter = ('fit_class', 'buying_window_class')
    search_fields = ('company__name', 'campaign__name')


@admin.register(Person)
class PersonAdmin(ModelAdmin):
    list_display = ('name', 'company', 'title', 'linkedin_url', 'created_at')
    search_fields = ('name', 'title', 'company__name')


@admin.register(ContactPoint)
class ContactPointAdmin(ModelAdmin):
    list_display = ('person', 'type', 'value', 'verification_status')
    list_filter = ('type', 'verification_status')
    search_fields = ('value', 'person__name')


@admin.register(BuyingGroupMember)
class BuyingGroupMemberAdmin(ModelAdmin):
    list_display = ('person', 'company', 'campaign', 'role_type', 'relevance_score')
    list_filter = ('role_type',)
    search_fields = ('person__name', 'company__name')


@admin.register(ResearchRun)
class ResearchRunAdmin(ModelAdmin):
    list_display = ('company', 'status', 'workflow_version', 'started_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('company__name',)


@admin.register(ProviderExecution)
class ProviderExecutionAdmin(ModelAdmin):
    list_display = ('provider', 'operation', 'status', 'latency_ms', 'cost', 'created_at')
    list_filter = ('provider', 'status')
    search_fields = ('provider', 'operation')


@admin.register(CampaignEvent)
class CampaignEventAdmin(ModelAdmin):
    list_display = ('campaign', 'event_type', 'company', 'occurred_at')
    list_filter = ('event_type',)
    search_fields = ('campaign__name', 'event_type')


@admin.register(TargetList)
class TargetListAdmin(ModelAdmin):
    list_display = ('name', 'created_by', 'is_smart', 'created_at')
    list_filter = ('is_smart',)
    search_fields = ('name',)


@admin.register(ListMembership)
class ListMembershipAdmin(ModelAdmin):
    list_display = ('target_list', 'company', 'added_at')
    search_fields = ('company__name', 'target_list__name')


@admin.register(CampaignEnrollment)
class CampaignEnrollmentAdmin(ModelAdmin):
    list_display = ('company', 'campaign', 'status', 'enrolled_at')
    list_filter = ('status',)
    search_fields = ('company__name', 'campaign__name')


@admin.register(SalesGuidance)
class SalesGuidanceAdmin(ModelAdmin):
    list_display = ('company', 'campaign', 'recommended_angle', 'recommended_next_step', 'created_at')
    search_fields = ('company__name', 'campaign__name')


@admin.register(EmailSequence)
class EmailSequenceAdmin(ModelAdmin):
    list_display = ('name', 'campaign', 'created_at')
    search_fields = ('name', 'campaign__name')


@admin.register(EmailMessage)
class EmailMessageAdmin(ModelAdmin):
    list_display = ('subject', 'recipient_email', 'status', 'is_approved', 'sent_at', 'created_at')
    list_filter = ('status', 'is_approved')
    search_fields = ('subject', 'recipient_email')


@admin.register(EmailBounce)
class EmailBounceAdmin(ModelAdmin):
    list_display = ('email', 'bounce_type', 'created_at')
    list_filter = ('bounce_type',)
    search_fields = ('email',)


@admin.register(EmailUnsubscribe)
class EmailUnsubscribeAdmin(ModelAdmin):
    list_display = ('email', 'created_at')
    search_fields = ('email',)


@admin.register(InboundReply)
class InboundReplyAdmin(ModelAdmin):
    list_display = ('email_message', 'classification', 'confidence', 'requires_review', 'received_at')
    list_filter = ('classification', 'requires_review')
    search_fields = ('email_message__recipient_email',)


@admin.register(LeadFeedback)
class LeadFeedbackAdmin(ModelAdmin):
    list_display = ('company', 'feedback_type', 'created_at')
    list_filter = ('feedback_type',)
    search_fields = ('company__name',)


@admin.register(CRMIntegrationRecord)
class CRMIntegrationRecordAdmin(ModelAdmin):
    list_display = ('company', 'external_crm', 'external_id', 'synced_at')
    list_filter = ('external_crm',)
    search_fields = ('company__name', 'external_id')


@admin.register(ProspectingRequest)
class ProspectingRequestAdmin(ModelAdmin):
    list_display = ('id', 'status', 'raw_objective', 'created_at')
    list_filter = ('status',)
    search_fields = ('raw_objective',)
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(ProspectingSpecificationVersion)
class ProspectingSpecificationVersionAdmin(ModelAdmin):
    list_display = ('request', 'version', 'status', 'parser_model', 'created_at', 'confirmed_at')
    list_filter = ('status',)
    search_fields = ('request__raw_objective',)
    readonly_fields = ('id', 'created_at', 'confirmed_at')


@admin.register(Discovery)
class DiscoveryAdmin(ModelAdmin):
    list_display = ('id', 'user_profile', 'prospecting_request', 'specification_version', 'created_at')
    search_fields = ('user_profile__username',)
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(DiscoveryLead)
class DiscoveryLeadAdmin(ModelAdmin):
    list_display = ('discovery_run', 'company', 'created_at')
    search_fields = ('company__name',)
