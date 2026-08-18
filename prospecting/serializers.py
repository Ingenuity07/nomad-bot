from rest_framework import serializers
from django.db.models import Q
from prospecting.models import (
    ProblemSignal, LeadCompany, Evidence, CompanySignal, 
    Person, ContactPoint, BuyingGroupMember, TargetList, 
    DiscoveryRun, ProspectingCampaign, CampaignEnrollment, SalesGuidance, EmailSequence, EmailMessage,
    EmailBounce, EmailUnsubscribe, InboundReply, LeadFeedback, CRMIntegrationRecord
)


class DiscoveryRunSerializer(serializers.ModelSerializer):
    lead_count = serializers.SerializerMethodField()
    new_lead_count = serializers.SerializerMethodField()
    duplicate_lead_count = serializers.SerializerMethodField()
    campaign = serializers.SerializerMethodField()
    prospecting_request = serializers.SerializerMethodField()
    specification_version = serializers.SerializerMethodField()

    class Meta:
        model = DiscoveryRun
        fields = [
            'id', 'keyword', 'location', 'status', 'total_leads_found',
            'lead_count', 'new_lead_count', 'duplicate_lead_count',
            'campaign', 'prospecting_request', 'specification_version',
            'started_at', 'completed_at'
        ]
        read_only_fields = fields

    def _lead_queryset(self, obj):
        return LeadCompany.objects.filter(
            Q(discovery_run=obj) | Q(discovery_leads__discovery_run=obj)
        ).distinct()

    def get_lead_count(self, obj):
        return self._lead_queryset(obj).count()

    def get_new_lead_count(self, obj):
        return obj.companies.count()

    def get_duplicate_lead_count(self, obj):
        return self._lead_queryset(obj).exclude(discovery_run=obj).count()

    def get_campaign(self, obj):
        if not obj.campaign_id:
            return None
        return {
            'id': str(obj.campaign_id),
            'name': obj.campaign.name,
            'status': obj.campaign.status,
        }

    def get_prospecting_request(self, obj):
        if not obj.prospecting_request_id:
            return None
        request = obj.prospecting_request
        return {
            'id': str(request.id),
            'status': request.status,
            'objective': request.raw_objective,
            'target': request.raw_target,
            'qualification': request.raw_qualification,
        }

    def get_specification_version(self, obj):
        if not obj.specification_version_id:
            return None
        specification = obj.specification_version
        return {
            'id': str(specification.id),
            'version': specification.version,
            'status': specification.status,
        }


class ProspectingCampaignSerializer(serializers.ModelSerializer):
    lead_count = serializers.SerializerMethodField()
    discovery_run_count = serializers.SerializerMethodField()

    class Meta:
        model = ProspectingCampaign
        fields = [
            'id', 'name', 'description', 'product_description',
            'problem_statement', 'geography', 'status', 'lead_count',
            'discovery_run_count', 'prospecting_request', 'created_at', 'updated_at'
        ]
        read_only_fields = fields

    def get_lead_count(self, obj):
        return LeadCompany.objects.filter(
            Q(campaign=obj) |
            Q(discovery_run__campaign=obj) |
            Q(discovery_leads__discovery_run__campaign=obj)
        ).distinct().count()

    def get_discovery_run_count(self, obj):
        return obj.discovery_runs.count()

class ProblemSignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProblemSignal
        fields = [
            'id', 'name', 'category', 'description', 
            'signal_type', 'detection_method', 'weight', 
            'active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class LeadCompanySerializer(serializers.ModelSerializer):
    lead_score = serializers.SerializerMethodField()
    fit_class = serializers.SerializerMethodField()
    buying_window_class = serializers.SerializerMethodField()
    discovery_run_id = serializers.UUIDField(source='discovery_run.id', read_only=True, allow_null=True)
    discovery_run_keyword = serializers.CharField(source='discovery_run.keyword', read_only=True, allow_null=True)
    discovery_run_location = serializers.CharField(source='discovery_run.location', read_only=True, allow_null=True)

    class Meta:
        model = LeadCompany
        fields = [
            'id', 'name', 'website', 'phone', 'address', 'category', 'rating',
            'lead_score', 'fit_class', 'buying_window_class', 'created_at',
            'discovery_run_id', 'discovery_run_keyword', 'discovery_run_location'
          ]

    def get_lead_score(self, obj):
        # Fetch from latest qualification or website analysis
        qual = obj.qualifications.order_by('-analysis_version').first()
        if qual:
            return float(qual.overall_score)
        analysis = getattr(obj, 'analysis', None)
        if analysis:
            return float(analysis.lead_score)
        return 0.0

    def get_fit_class(self, obj):
        qual = obj.qualifications.order_by('-analysis_version').first()
        if qual:
            return qual.fit_class
        return "UNKNOWN"

    def get_buying_window_class(self, obj):
        qual = obj.qualifications.order_by('-analysis_version').first()
        if qual:
            return qual.buying_window_class
        return "UNKNOWN"


class EvidenceSerializer(serializers.ModelSerializer):
    signal_name = serializers.CharField(source='signal.name', read_only=True)
    class Meta:
        model = Evidence
        fields = [
            'id', 'source_type', 'source_url', 'source_title', 
            'evidence_text', 'confidence', 'signal_name', 'captured_at'
        ]


class CompanySignalSerializer(serializers.ModelSerializer):
    signal_name = serializers.CharField(source='signal.name', read_only=True)
    category = serializers.CharField(source='signal.category', read_only=True)
    class Meta:
        model = CompanySignal
        fields = [
            'id', 'signal_name', 'category', 'value', 'confidence', 
            'status', 'first_detected_at', 'last_detected_at'
        ]


class ContactPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactPoint
        fields = ['id', 'type', 'value', 'source', 'verification_status', 'confidence']


class PersonSerializer(serializers.ModelSerializer):
    contact_points = ContactPointSerializer(many=True, read_only=True)
    class Meta:
        model = Person
        fields = ['id', 'name', 'first_name', 'last_name', 'title', 'linkedin_url', 'contact_points']


class BuyingGroupMemberSerializer(serializers.ModelSerializer):
    person = PersonSerializer(read_only=True)
    class Meta:
        model = BuyingGroupMember
        fields = ['id', 'role_type', 'relevance_score', 'reason', 'person']


class TargetListSerializer(serializers.ModelSerializer):
    class Meta:
        model = TargetList
        fields = ['id', 'name', 'is_smart', 'criteria', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_at']


class CampaignEnrollmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampaignEnrollment
        fields = ['id', 'campaign', 'company', 'status', 'enrolled_at', 'updated_at']
        read_only_fields = ['id', 'enrolled_at', 'updated_at']


class SalesGuidanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesGuidance
        fields = [
            'id', 'company', 'campaign', 'person', 'talking_points', 
            'recommended_angle', 'recommended_next_step', 'message_draft', 
            'risks', 'unknowns', 'metadata', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class EmailSequenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailSequence
        fields = ['id', 'campaign', 'name', 'steps', 'created_at']
        read_only_fields = ['id', 'created_at']


class EmailMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailMessage
        fields = [
            'id', 'sequence', 'company', 'recipient_email', 'subject', 
            'body', 'is_approved', 'status', 'sent_at', 'created_at'
        ]
        read_only_fields = ['id', 'sent_at', 'created_at']


class InboundReplySerializer(serializers.ModelSerializer):
    class Meta:
        model = InboundReply
        fields = [
            'id', 'email_message', 'reply_text', 'classification', 
            'confidence', 'requires_review', 'received_at'
        ]
        read_only_fields = ['id', 'classification', 'confidence', 'requires_review', 'received_at']


class LeadFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadFeedback
        fields = ['id', 'company', 'feedback_type', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']


class CRMIntegrationRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = CRMIntegrationRecord
        fields = ['id', 'company', 'person', 'external_crm', 'external_id', 'synced_at']
        read_only_fields = ['id', 'synced_at']


from prospecting.models import ProspectingRequest, ProspectingSpecificationVersion, Discovery

class ProspectingRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProspectingRequest
        fields = ['id', 'user_profile', 'raw_objective', 'raw_target', 'raw_qualification', 'clarification_history', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user_profile', 'clarification_history', 'status', 'created_at', 'updated_at']


class ProspectingSpecificationVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProspectingSpecificationVersion
        fields = ['id', 'request', 'version', 'schema_version', 'specification_json', 'status', 'parser_model', 'parser_provider', 'prompt_version', 'created_at', 'confirmed_at', 'confirmed_by']
        read_only_fields = ['id', 'request', 'version', 'schema_version', 'status', 'parser_model', 'parser_provider', 'prompt_version', 'created_at', 'confirmed_at', 'confirmed_by']


class DiscoverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Discovery
        fields = ['id', 'user_profile', 'prospecting_request', 'specification_version', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user_profile', 'prospecting_request', 'specification_version', 'created_at', 'updated_at']

