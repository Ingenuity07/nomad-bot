import logging

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings as django_settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from integrations.social.publishing.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderValidationError,
)
from integrations.social.publishing.types import ProviderName, WebhookRequest
from integrations.social.media import (
    MediaValidationError,
    delete_media_asset,
    reorder_variant_images,
    store_uploaded_media,
    update_media_alt_text,
)
from integrations.social.models import (
    AnalyticsSuggestion,
    BrandProfile,
    ConnectionState,
    ContentSource,
    MediaAsset,
    MediaAssetSource,
    PublishJobState,
    SocialNetwork,
    SocialConnection,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialWorkspaceSettings,
    SocialAuditEventType,
    StoryInterview,
    VoiceRuleSuggestion,
    VoiceRuleSuggestionState,
)
from integrations.social.services.audit import record_audit_event
from integrations.social.services.data_management import (
    delete_workspace_content,
    export_workspace_content,
)
from integrations.social.services.operations import operational_health_snapshot
from integrations.social.services.analytics import analytics_dashboard, decide_analytics_suggestion
from integrations.social.services.onboarding import (
    cancel_connection,
    complete_connection,
    complete_step,
    onboarding_for,
    serialize_onboarding,
    set_current_step,
    start_connection,
    start_onboarding,
)
from integrations.social.serializers import (
    ContentSourceSummarySerializer,
    MediaAssetSerializer,
    SocialPostSerializer,
)
from integrations.social.services.composer import (
    ComposerValidationError,
    NETWORK_LABELS,
    connected_networks,
    create_draft,
    generate_variants,
    normalize_controls,
    rewrite_variant,
    save_variant_draft,
    source_for_workspace,
    sources_for_workspace,
    submit_for_review,
    sync_draft_networks,
    update_post,
)
from integrations.social.services.knowledge import (
    approve_story,
    brand_brain_for,
    create_content_source,
    current_story_interview,
    decide_voice_suggestion,
    record_voice_edit,
    save_story_answers,
    update_brand_brain,
)
from integrations.social.services.lifecycle import (
    ProviderWebhookReplayError,
    approve_variant,
    process_provider_webhook,
    publish_variant_now,
)
from integrations.social.services.publishing_routing import (
    provider_readiness,
    system_default_provider,
)
from integrations.social.services.studio import (
    approve_exact_version,
    archive_post,
    batch_approve,
    calendar_items,
    calendar_range,
    disconnect_connection,
    duplicate_post,
    home_summary,
    library_posts,
    reconnect_connection,
    reject_variant,
    request_changes,
    reschedule_variant,
    review_queue,
    serialize_connection,
    serialize_variant_card,
)
from integrations.linkedin.services.images import (
    ImageGenerationConfigurationError,
    ImageGenerationError,
    ImageGenerationQuotaError,
    ImageProviderUnavailableError,
    LinkedInImageGenerator,
)
from integrations.linkedin.workspaces import resolve_active_workspace


logger = logging.getLogger(__name__)


class ContentStudioEnvelopeSerializer(serializers.Serializer):
    """Schema placeholder; endpoint shapes are documented in the API guide."""


class SocialWorkspaceScopedAPIView(GenericAPIView):
    authentication_classes = [BasicAuthentication, SessionAuthentication]
    serializer_class = ContentStudioEnvelopeSerializer

    def workspace(self, request):
        return resolve_active_workspace(request)

    def variant(self, request, variant_id):
        return get_object_or_404(
            SocialPostVariant.objects.select_related("post").filter(
                post__workspace=self.workspace(request),
            ),
            pk=variant_id,
        )

    def post_object(self, request, post_id):
        return get_object_or_404(
            SocialPost.objects.filter(workspace=self.workspace(request)),
            pk=post_id,
        )

    def connection(self, request, connection_id):
        return get_object_or_404(
            SocialConnection.objects.filter(workspace=self.workspace(request)),
            pk=connection_id,
        )

    @staticmethod
    def media_error(error):
        return Response({"media": list(error.messages)}, status=400)


def onboarding_validation_response(error):
    if hasattr(error, "message_dict"):
        return Response(error.message_dict, status=400)
    return Response({"detail": error.messages}, status=400)


def social_validation_response(error):
    if hasattr(error, "message_dict"):
        return Response(error.message_dict, status=400)
    return Response({"detail": error.messages}, status=400)


def social_post_response(post):
    post = (
        SocialPost.objects.select_related("source", "brand_profile_version")
        .prefetch_related("source_references__source__owner", "variants__connection", "variants__media_assets")
        .get(pk=post.pk)
    )
    return SocialPostSerializer(post).data


def sources_from_request(workspace, data):
    if "source_ids" in data:
        sources = sources_for_workspace(workspace, data.get("source_ids"))
        return sources, "source_ids"
    source_id = data.get("source_id")
    source = source_for_workspace(workspace, source_id)
    if source_id and source is None:
        return None, "source_id"
    return ([] if source is None else [source]), "source_id"


def serialize_brand_brain(profile):
    latest = profile.versions.order_by("-version").first()
    suggestions = profile.voice_rule_suggestions.filter(
        status=VoiceRuleSuggestionState.PENDING,
        evidence_count__gte=3,
    ).order_by("-updated_at")
    return {
        "id": str(profile.id),
        "version": latest.version if latest else 0,
        "version_id": str(latest.id) if latest else "",
        "business_description": profile.business_description,
        "audience": profile.audience,
        "goals": profile.goals,
        "voice": profile.voice,
        "voice_rules": profile.voice_rules,
        "example_posts": profile.example_posts,
        "content_pillars": profile.content_pillars,
        "calls_to_action": profile.calls_to_action,
        "visual_direction": profile.visual_direction,
        "forbidden_topics": profile.forbidden_topics,
        "performance_rules": profile.performance_rules,
        "suggestions": [{"id": str(item.id), "rule": item.suggested_rule, "evidence_count": item.evidence_count} for item in suggestions],
        "updated_at": profile.updated_at.isoformat(),
    }


def serialize_story(interview):
    return {
        "id": str(interview.id),
        "week_of": interview.week_of.isoformat(),
        "questions": interview.questions,
        "answers": interview.answers,
        "status": interview.status,
        "approved_source_id": str(interview.approved_source_id or ""),
        "approved_at": interview.approved_at.isoformat() if interview.approved_at else None,
    }


class BrandBrainAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        return Response(serialize_brand_brain(brand_brain_for(self.workspace(request))))

    def put(self, request):
        if "performance_rules" in request.data:
            return Response(
                {"performance_rules": ["Accept an analytics suggestion to add a performance rule."]},
                status=400,
            )
        profile = brand_brain_for(self.workspace(request))
        try:
            profile = update_brand_brain(profile, request.data, user=request.user)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(serialize_brand_brain(profile))


class BrandVoiceSuggestionAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, suggestion_id):
        suggestion = get_object_or_404(
            VoiceRuleSuggestion.objects.select_related("brand_profile__settings").filter(
                brand_profile__settings__workspace=self.workspace(request),
                status=VoiceRuleSuggestionState.PENDING,
            ),
            pk=suggestion_id,
        )
        action = str(request.data.get("action") or "").upper()
        if action not in {"CONFIRM", "DISMISS"}:
            return Response({"action": ["Choose confirm or dismiss."]}, status=400)
        try:
            decide_voice_suggestion(suggestion, confirm=action == "CONFIRM", user=request.user)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(serialize_brand_brain(suggestion.brand_profile))


class ContentSourcesAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        sources = ContentSource.objects.filter(workspace=self.workspace(request), is_active=True).select_related("owner")
        return Response(ContentSourceSummarySerializer(sources, many=True).data)

    def post(self, request):
        try:
            source = create_content_source(workspace=self.workspace(request), owner=request.user, values=request.data)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(ContentSourceSummarySerializer(source).data, status=201)


class StoryInterviewAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        return Response(serialize_story(current_story_interview(self.workspace(request), owner=request.user)))

    def patch(self, request):
        interview = current_story_interview(self.workspace(request), owner=request.user)
        try:
            interview = save_story_answers(interview, request.data.get("answers"))
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(serialize_story(interview))

    def post(self, request):
        interview = current_story_interview(self.workspace(request), owner=request.user)
        if str(request.data.get("action") or "").upper() != "APPROVE":
            return Response({"action": ["Choose approve to save these answers as a source."]}, status=400)
        try:
            interview = approve_story(interview, user=request.user)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(serialize_story(interview))


class SocialComposerOptionsAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        workspace = self.workspace(request)
        connections = connected_networks(workspace)
        connection_options = []
        for network in (SocialNetwork.LINKEDIN, SocialNetwork.X, SocialNetwork.INSTAGRAM):
            connection = connections.get(network)
            if connection:
                connection_options.append({
                    "network": network,
                    "label": NETWORK_LABELS[network],
                    "display_name": connection.display_name,
                    "account_type": connection.get_account_type_display(),
                    "health": "HEALTHY",
                })
        if not connection_options and django_settings.CONTENT_STUDIO_DRAFT_ONLY_ALLOWED:
            connection_options.append({
                "network": SocialNetwork.LINKEDIN,
                "label": "LinkedIn",
                "display_name": "Draft only",
                "account_type": "Connect before publishing",
                "health": "NEEDS_ATTENTION",
            })
        sources = ContentSource.objects.filter(workspace=workspace, is_active=True, is_reusable=True)
        drafts = (
            SocialPost.objects.filter(
                workspace=workspace,
                state__in=[
                    SocialPostState.DRAFT,
                    SocialPostState.NEEDS_REVIEW,
                    SocialPostState.CONNECTION_REQUIRED,
                ],
            )
            .prefetch_related("variants")
            .order_by("-updated_at")[:50]
        )
        return Response({
            "connections": connection_options,
            "sources": ContentSourceSummarySerializer(sources, many=True).data,
            "drafts": [
                {
                    "id": str(post.id),
                    "idea_title": post.idea_title,
                    "state": post.state,
                    "networks": [variant.network for variant in post.variants.all()],
                    "updated_at": post.updated_at.isoformat(),
                }
                for post in drafts
            ],
            "generation_controls": {
                "tones": ["Professional", "Friendly", "Bold", "Educational"],
                "goals": ["Awareness", "Engagement", "Education", "Leads"],
                "lengths": ["Short", "Medium", "Long"],
            },
        })


class SocialPostListCreateAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request):
        workspace = self.workspace(request)
        sources, source_field = sources_from_request(workspace, request.data)
        if sources is None:
            return Response({source_field: ["Choose sources from this workspace."]}, status=400)
        try:
            post = create_draft(
                workspace=workspace,
                idea_title=request.data.get("idea_title"),
                idea_text=request.data.get("idea_text", ""),
                sources=sources,
                networks=request.data.get("networks"),
                controls=request.data.get("controls"),
            )
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(post), status=201)


class SocialPostDetailAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request, post_id):
        return Response(social_post_response(self.post_object(request, post_id)))

    @transaction.atomic
    def patch(self, request, post_id):
        post = self.post_object(request, post_id)
        sources = None
        source_was_supplied = "source_id" in request.data or "source_ids" in request.data
        if source_was_supplied:
            sources, source_field = sources_from_request(post.workspace, request.data)
            if sources is None:
                return Response({source_field: ["Choose sources from this workspace."]}, status=400)
        try:
            update_post(
                post=post,
                idea_title=request.data.get("idea_title") if "idea_title" in request.data else None,
                idea_text=request.data.get("idea_text") if "idea_text" in request.data else None,
                sources=sources,
                source_was_supplied=source_was_supplied,
                controls=request.data.get("controls") if "controls" in request.data else None,
            )
            if "networks" in request.data:
                sync_draft_networks(post=post, networks=request.data.get("networks"))
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(post))


class SocialPostGenerateAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request):
        workspace = self.workspace(request)
        post_id = request.data.get("post_id")
        if post_id:
            post = self.post_object(request, post_id)
            sources = None
            source_was_supplied = "source_id" in request.data or "source_ids" in request.data
            if source_was_supplied:
                sources, source_field = sources_from_request(workspace, request.data)
                if sources is None:
                    return Response({source_field: ["Choose sources from this workspace."]}, status=400)
            try:
                update_post(
                    post=post,
                    idea_title=request.data.get("idea_title") if "idea_title" in request.data else None,
                    idea_text=request.data.get("idea_text") if "idea_text" in request.data else None,
                    sources=sources,
                    source_was_supplied=source_was_supplied,
                )
            except DjangoValidationError as error:
                return social_validation_response(error)
        else:
            sources, source_field = sources_from_request(workspace, request.data)
            if sources is None:
                return Response({source_field: ["Choose sources from this workspace."]}, status=400)
            try:
                post = create_draft(
                    workspace=workspace,
                    idea_title=request.data.get("idea_title"),
                    idea_text=request.data.get("idea_text", ""),
                    sources=sources,
                    networks=request.data.get("networks"),
                    controls=request.data.get("controls"),
                )
            except DjangoValidationError as error:
                return social_validation_response(error)
        try:
            post = generate_variants(
                post=post,
                networks=request.data.get("networks"),
                controls=request.data.get("controls"),
            )
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(post))


class SocialVariantDetailAPIView(SocialWorkspaceScopedAPIView):
    def patch(self, request, variant_id):
        variant = self.variant(request, variant_id)
        original_copy = variant.copy
        hashtags = request.data.get("hashtags") if "hashtags" in request.data else None
        if hashtags is not None and not isinstance(hashtags, list):
            return Response({"hashtags": ["Provide hashtags as a list."]}, status=400)
        scheduled_for = None
        if "scheduled_for" in request.data:
            scheduled_for = parse_datetime(str(request.data.get("scheduled_for") or ""))
            if scheduled_for is None:
                return Response({"scheduled_for": ["Choose a valid date and time."]}, status=400)
        try:
            variant = save_variant_draft(
                variant=variant,
                copy=request.data.get("copy") if "copy" in request.data else None,
                hashtags=hashtags,
                scheduled_for=scheduled_for,
            )
            if "copy" in request.data:
                record_voice_edit(workspace=variant.post.workspace, before=original_copy, after=variant.copy)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(variant.post))


class SocialVariantRewriteAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        try:
            variant = rewrite_variant(variant=variant, action=request.data.get("action"))
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(variant.post))


class SocialPostSubmitReviewAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, post_id):
        post = self.post_object(request, post_id)
        try:
            submit_for_review(post)
        except ComposerValidationError as error:
            return Response(error.payload, status=400)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(post))


class ContentStudioHomeAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        return Response(home_summary(self.workspace(request)))


class SocialApprovalsAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        return Response(review_queue(self.workspace(request)))


class SocialAnalyticsAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        return Response(analytics_dashboard(self.workspace(request)))


class SocialAnalyticsSuggestionAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, suggestion_id):
        workspace = self.workspace(request)
        suggestion = get_object_or_404(
            AnalyticsSuggestion.objects.filter(workspace=workspace),
            pk=suggestion_id,
        )
        action = str(request.data.get("action") or "").upper()
        if action not in {"ACCEPT", "DISMISS"}:
            return Response({"action": ["Choose accept or dismiss."]}, status=400)
        try:
            decide_analytics_suggestion(
                suggestion,
                accept=action == "ACCEPT",
                user=request.user,
            )
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(analytics_dashboard(workspace))


class SocialApprovalActionAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        action = str(request.data.get("action") or "").upper()
        try:
            if action == "APPROVE":
                approve_exact_version(variant, request.data.get("version_id"), user=request.user)
            elif action == "REQUEST_CHANGES":
                request_changes(variant, note=request.data.get("note"))
            elif action == "REJECT":
                reject_variant(variant, note=request.data.get("note"))
            else:
                return Response({"action": ["Choose approve, request changes, or reject."]}, status=400)
        except DjangoValidationError as error:
            return social_validation_response(error)
        record_audit_event(
            workspace=variant.post.workspace,
            event_type={
                "APPROVE": SocialAuditEventType.APPROVAL_GRANTED,
                "REQUEST_CHANGES": SocialAuditEventType.CHANGES_REQUESTED,
                "REJECT": SocialAuditEventType.APPROVAL_REJECTED,
            }[action],
            actor=request.user,
            target=variant,
            details={"version_id": str(request.data.get("version_id") or ""), "network": variant.network},
        )
        return Response(review_queue(variant.post.workspace))


class SocialPublishNowAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        try:
            job = publish_variant_now(variant)
        except DjangoValidationError as error:
            return social_validation_response(error)
        except Exception:
            logger.exception("Unexpected immediate publishing failure for social variant %s.", variant.id)
            return Response(
                {"code": "publish_failed", "detail": "The post could not be submitted for publishing."},
                status=502,
            )

        variant = (
            SocialPostVariant.objects.select_related("post__source", "connection")
            .prefetch_related("media_assets")
            .get(pk=variant.id)
        )
        payload = {
            "variant": serialize_variant_card(variant),
            "publish_job": {
                "id": str(job.id),
                "status": job.status,
                "external_id": job.external_id,
                "failure_message": job.failure_message,
            },
        }
        if job.status == PublishJobState.FAILED:
            payload["detail"] = job.failure_message or "The publishing provider rejected the post."
            return Response(payload, status=502)
        if job.status == PublishJobState.CONNECTION_REQUIRED:
            payload["detail"] = job.failure_message or "Reconnect the social account before publishing."
            return Response(payload, status=409)
        if job.status == PublishJobState.SCHEDULED:
            payload["detail"] = job.failure_message or "The provider is temporarily unavailable; publishing will be retried."
            return Response(payload, status=503)
        response_status = 200 if job.status == PublishJobState.PUBLISHED else 202
        return Response(payload, status=response_status)


class SocialBatchApprovalAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request):
        workspace = self.workspace(request)
        try:
            approved = batch_approve(workspace, request.data.get("approvals"), user=request.user)
        except DjangoValidationError as error:
            return social_validation_response(error)
        for variant in approved:
            record_audit_event(
                workspace=workspace,
                event_type=SocialAuditEventType.APPROVAL_GRANTED,
                actor=request.user,
                target=variant,
                details={"batch": True, "network": variant.network},
            )
        return Response(review_queue(workspace))


class SocialCalendarAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        workspace = self.workspace(request)
        view = str(request.query_params.get("view") or "WEEK").upper()
        if view not in {"WEEK", "MONTH"}:
            return Response({"view": ["Choose week or month."]}, status=400)
        settings = SocialWorkspaceSettings.objects.filter(workspace=workspace).first()
        timezone_name = settings.timezone if settings else "UTC"
        try:
            calendar_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            calendar_timezone = ZoneInfo("UTC")
            timezone_name = "UTC"
        anchor_date = parse_date(str(request.query_params.get("date") or "")) or timezone.now().astimezone(calendar_timezone).date()
        anchor = datetime.combine(anchor_date, time.min, tzinfo=calendar_timezone)
        start, end = calendar_range(anchor, view)
        return Response({
            "view": view,
            "timezone": timezone_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "items": calendar_items(workspace, start=start, end=end),
        })


class SocialRescheduleAPIView(SocialWorkspaceScopedAPIView):
    def patch(self, request, variant_id):
        variant = self.variant(request, variant_id)
        previous_schedule = variant.scheduled_for
        scheduled_for = parse_datetime(str(request.data.get("scheduled_for") or ""))
        if scheduled_for is None:
            return Response({"scheduled_for": ["Choose a valid date and time."]}, status=400)
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(scheduled_for)
        try:
            variant = reschedule_variant(variant, scheduled_for)
        except DjangoValidationError as error:
            return social_validation_response(error)
        record_audit_event(
            workspace=variant.post.workspace,
            event_type=SocialAuditEventType.SCHEDULE_CHANGED,
            actor=request.user,
            target=variant,
            details={"from": previous_schedule.isoformat(), "to": scheduled_for.isoformat(), "network": variant.network},
        )
        return Response(serialize_variant_card(
            SocialPostVariant.objects.select_related("post__source", "connection").prefetch_related("media_assets").get(pk=variant.pk)
        ))


class SocialLibraryAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        workspace = self.workspace(request)
        status_filter = str(request.query_params.get("status") or "").upper()
        platform = str(request.query_params.get("platform") or "").upper()
        date_from = parse_date(str(request.query_params.get("date_from") or ""))
        date_to = parse_date(str(request.query_params.get("date_to") or ""))
        posts = library_posts(
            workspace,
            status=status_filter,
            query=str(request.query_params.get("q") or "").strip(),
            platform=platform,
            date_from=date_from,
            date_to=date_to,
            include_archived=str(request.query_params.get("archived") or "").lower() == "true",
        )
        return Response(SocialPostSerializer(posts, many=True).data)


class SocialLibraryActionAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, post_id):
        post = self.post_object(request, post_id)
        action = str(request.data.get("action") or "").upper()
        try:
            if action == "DUPLICATE":
                result = duplicate_post(post)
            elif action == "REUSE_IDEA":
                result = duplicate_post(post, reuse_idea=True)
            elif action == "ARCHIVE":
                result = archive_post(post)
            else:
                return Response({"action": ["Choose duplicate, reuse idea, or archive."]}, status=400)
        except DjangoValidationError as error:
            return social_validation_response(error)
        return Response(social_post_response(result))


class SocialConnectionsAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        connections = SocialConnection.objects.filter(workspace=self.workspace(request)).order_by("network", "display_name")
        return Response([serialize_connection(connection) for connection in connections])


class SocialConnectionActionAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, connection_id):
        connection = self.connection(request, connection_id)
        action = str(request.data.get("action") or "").upper()
        if action == "DISCONNECT":
            try:
                connection = disconnect_connection(connection)
            except DjangoValidationError as error:
                return social_validation_response(error)
            record_audit_event(
                workspace=connection.workspace,
                event_type=SocialAuditEventType.CONNECTION_DISCONNECTED,
                actor=request.user,
                target=connection,
                details={"network": connection.network},
            )
            return Response(serialize_connection(connection))
        if action == "RECONNECT":
            try:
                result = reconnect_connection(connection)
            except DjangoValidationError as error:
                return social_validation_response(error)
            except Exception:
                return Response({"detail": "The social account connection step could not start. Try again."}, status=502)
            record_audit_event(
                workspace=connection.workspace,
                event_type=SocialAuditEventType.CONNECTION_STARTED,
                actor=request.user,
                target=connection,
                details={"network": connection.network, "reconnect": True},
            )
            return Response({"authorization_url": result.url, "expires_at": result.expires_at.isoformat() if result.expires_at else None})
        return Response({"action": ["Choose reconnect or disconnect."]}, status=400)


class ContentStudioOnboardingAPIView(SocialWorkspaceScopedAPIView):
    def get(self, request):
        return Response(serialize_onboarding(onboarding_for(self.workspace(request))))

    def post(self, request):
        onboarding = start_onboarding(self.workspace(request))
        return Response(serialize_onboarding(onboarding))

    def patch(self, request):
        try:
            step = int(request.data.get("current_step"))
            onboarding = set_current_step(self.workspace(request), step)
        except (TypeError, ValueError):
            return Response({"current_step": ["Choose a step from 1 to 4."]}, status=400)
        except DjangoValidationError as error:
            return onboarding_validation_response(error)
        return Response(serialize_onboarding(onboarding))


class ContentStudioOnboardingStepAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, step):
        try:
            onboarding = complete_step(self.workspace(request), step, request.data)
        except DjangoValidationError as error:
            return onboarding_validation_response(error)
        return Response(serialize_onboarding(onboarding))


class ContentStudioConnectionStartAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request):
        workspace = self.workspace(request)
        try:
            onboarding, authorization_url = start_connection(workspace, redirect_uri="")
        except DjangoValidationError as error:
            return onboarding_validation_response(error)
        record_audit_event(
            workspace=workspace,
            event_type=SocialAuditEventType.CONNECTION_STARTED,
            actor=request.user,
            details={"network": SocialNetwork.LINKEDIN},
        )
        return Response({
            "authorization_url": authorization_url,
            "expires_at": onboarding.connection_expires_at.isoformat(),
        })


class ContentStudioConnectionCompleteAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request):
        workspace = self.workspace(request)
        if request.data.get("cancelled") is True:
            onboarding = cancel_connection(workspace)
            return Response(serialize_onboarding(onboarding))
        if request.data.get("error"):
            onboarding = cancel_connection(workspace)
            onboarding.connection_error = "LinkedIn could not be connected. Check access and choose Reconnect."
            onboarding.save(update_fields=["connection_error", "updated_at"])
            return Response(serialize_onboarding(onboarding), status=400)
        try:
            onboarding = complete_connection(
                workspace,
                state=str(request.data.get("state") or ""),
                authorization_code=str(request.data.get("code") or ""),
            )
        except DjangoValidationError as error:
            return onboarding_validation_response(error)
        connection = SocialConnection.objects.filter(
            workspace=workspace,
            network=SocialNetwork.LINKEDIN,
            status=ConnectionState.CONNECTED,
        ).order_by("-connected_at").first()
        record_audit_event(
            workspace=workspace,
            event_type=SocialAuditEventType.CONNECTION_COMPLETED,
            actor=request.user,
            target=connection,
            details={"network": SocialNetwork.LINKEDIN},
        )
        return Response(serialize_onboarding(onboarding))


class ContentStudioConnectionCancelAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request):
        workspace = self.workspace(request)
        onboarding = cancel_connection(workspace)
        record_audit_event(
            workspace=workspace,
            event_type=SocialAuditEventType.CONNECTION_DISCONNECTED,
            actor=request.user,
            details={"network": SocialNetwork.LINKEDIN, "during_setup": True},
        )
        return Response(serialize_onboarding(onboarding))


class SocialMediaAssetListCreateAPIView(SocialWorkspaceScopedAPIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, variant_id):
        variant = self.variant(request, variant_id)
        return Response(MediaAssetSerializer(variant.media_assets.all(), many=True).data)

    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        if any(key in request.data for key in ("storage_url", "url", "original_storage_key", "publish_storage_key")):
            return Response({"media": ["Upload a file; server-side media URLs are not accepted."]}, status=400)
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return Response({"file": ["Select a media file to upload."]}, status=400)
        try:
            asset = store_uploaded_media(
                variant,
                uploaded_file,
                alt_text=request.data.get("alt_text", ""),
            )
        except MediaValidationError as error:
            return self.media_error(error)
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        return Response(MediaAssetSerializer(asset).data, status=201)


class SocialMediaAssetDetailAPIView(SocialWorkspaceScopedAPIView):
    def asset(self, request, variant_id, pk):
        variant = self.variant(request, variant_id)
        return get_object_or_404(
            MediaAsset.objects.filter(workspace=variant.post.workspace, variant=variant),
            pk=pk,
        )

    def patch(self, request, variant_id, pk):
        asset = self.asset(request, variant_id, pk)
        if set(request.data) - {"alt_text"}:
            return Response({"detail": "Only alt_text can be changed on a stored media asset."}, status=400)
        try:
            asset = update_media_alt_text(asset, request.data.get("alt_text", ""))
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        return Response(MediaAssetSerializer(asset).data)

    def delete(self, request, variant_id, pk):
        asset = self.asset(request, variant_id, pk)
        try:
            delete_media_asset(asset)
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        return Response(status=204)


class SocialMediaReorderAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        asset_ids = request.data.get("asset_ids")
        if not isinstance(asset_ids, list):
            return Response({"asset_ids": ["Provide an ordered list of image asset IDs."]}, status=400)
        try:
            assets = reorder_variant_images(variant, asset_ids)
        except MediaValidationError as error:
            return self.media_error(error)
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        return Response(MediaAssetSerializer(assets, many=True).data)


class SocialMediaRegenerateAPIView(SocialWorkspaceScopedAPIView):
    parser_classes = [JSONParser]

    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        prompt = str(
            request.data.get("prompt")
            or variant.metadata.get("image_prompt")
            or variant.post.metadata.get("image_prompt")
            or ""
        ).strip()
        if not prompt:
            return Response({"prompt": ["Describe the image to generate."]}, status=400)
        try:
            _, metadata, image_data = LinkedInImageGenerator().generate(variant.id, prompt)
        except ImageGenerationQuotaError:
            logger.exception("Image generation quota exhausted for social variant %s.", variant.id)
            return Response(
                {"code": "image_generation_quota", "detail": "Image generation quota is unavailable."},
                status=429,
            )
        except ImageGenerationConfigurationError:
            logger.exception("Image generation configuration rejected for social variant %s.", variant.id)
            return Response(
                {"code": "image_generation_not_configured", "detail": "Image generation is not configured correctly."},
                status=503,
            )
        except ImageProviderUnavailableError:
            logger.exception("Image provider unavailable for social variant %s.", variant.id)
            return Response(
                {"code": "image_provider_unavailable", "detail": "The image provider is temporarily unavailable."},
                status=502,
            )
        except ImageGenerationError:
            logger.exception("Image generation failed for social variant %s.", variant.id)
            return Response(
                {"code": "image_generation_failed", "detail": "The image could not be generated."},
                status=502,
            )
        except Exception:
            logger.exception("Unexpected image generation failure for social variant %s.", variant.id)
            return Response(
                {"code": "image_generation_failed", "detail": "The image could not be generated."},
                status=502,
            )
        if not image_data:
            return Response(
                {"code": "image_generation_not_configured", "detail": "Image generation is not configured."},
                status=503,
            )
        content_type = str(metadata.get("content_type") or "image/png")
        uploaded_file = SimpleUploadedFile(
            f"generated-{variant.id}.png",
            image_data,
            content_type=content_type,
        )
        replace_asset = None
        replace_id = request.data.get("asset_id")
        if replace_id:
            replace_asset = variant.media_assets.filter(pk=replace_id, asset_type="IMAGE").first()
            if replace_asset is None:
                return Response({"asset_id": ["Image asset not found in this variant."]}, status=404)
        try:
            asset = store_uploaded_media(
                variant,
                uploaded_file,
                alt_text=request.data.get("alt_text", ""),
                source=MediaAssetSource.AI,
                replace_asset=replace_asset,
            )
        except MediaValidationError as error:
            return self.media_error(error)
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        return Response(MediaAssetSerializer(asset).data, status=201)


class SocialVariantApproveAPIView(SocialWorkspaceScopedAPIView):
    def post(self, request, variant_id):
        variant = self.variant(request, variant_id)
        try:
            version = approve_variant(variant, approved_by=request.user)
        except MediaValidationError as error:
            return self.media_error(error)
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        record_audit_event(
            workspace=variant.post.workspace,
            event_type=SocialAuditEventType.APPROVAL_GRANTED,
            actor=request.user,
            target=variant,
            details={"version_id": str(version.id), "network": variant.network},
        )
        return Response({
            "variant_id": str(variant.id),
            "approved_version_id": str(version.id),
            "version": version.version,
            "status": "APPROVED",
        })


class PublishingProviderHealthAPIView(GenericAPIView):
    """Staff-only, secret-free status for internal operations."""

    authentication_classes = [BasicAuthentication, SessionAuthentication]
    permission_classes = [IsAdminUser]
    serializer_class = ContentStudioEnvelopeSerializer

    def get(self, request):
        providers = []
        for readiness in provider_readiness():
            providers.append({
                "provider": readiness.provider.value,
                "configured": readiness.configured,
                "healthy": readiness.healthy,
                "capabilities": {
                    "networks": sorted(network.value for network in readiness.capabilities.networks),
                    "media_types": sorted(media.value for media in readiness.capabilities.media_types),
                    "connection_completion": readiness.capabilities.supports_connection_completion,
                    "account_listing": readiness.capabilities.supports_account_listing,
                    "cancellation": readiness.capabilities.supports_cancellation,
                    "status_polling": readiness.capabilities.supports_status_polling,
                    "webhooks": readiness.capabilities.supports_webhooks,
                    "metrics": sorted(metric.value for metric in readiness.capabilities.metric_names),
                },
            })
        response = Response({
            "default_provider": system_default_provider().value,
            "providers": providers,
            "operations": operational_health_snapshot(),
        })
        response["Cache-Control"] = "no-store"
        return response


class PublishingProviderWebhookAPIView(GenericAPIView):
    """Secret-verified provider callback; never stores the raw request body."""

    authentication_classes = []
    permission_classes = []
    serializer_class = ContentStudioEnvelopeSerializer

    def post(self, request, provider):
        try:
            provider_name = ProviderName(str(provider).upper())
        except ValueError:
            return Response({"detail": "Unknown publishing callback."}, status=404)
        webhook_request = WebhookRequest(
            headers={key: value for key, value in request.headers.items()},
            body=request.body,
        )
        try:
            events = process_provider_webhook(
                provider=provider_name,
                request=webhook_request,
            )
        except ProviderWebhookReplayError:
            return Response({"detail": "Webhook event already processed."}, status=409)
        except ProviderAuthenticationError:
            return Response({"detail": "Invalid publishing callback signature or scope."}, status=401)
        except ProviderValidationError:
            return Response({"detail": "Invalid publishing callback."}, status=400)
        except ProviderConfigurationError:
            return Response({"detail": "Publishing callback is not configured."}, status=503)
        return Response({"accepted": len(events)}, status=202)


class ContentStudioDataExportAPIView(SocialWorkspaceScopedAPIView):
    """Workspace-admin export with customer content and no vendor credentials."""

    def get(self, request):
        payload = export_workspace_content(
            workspace=self.workspace(request),
            actor=request.user,
        )
        response = Response(payload)
        response["Content-Disposition"] = 'attachment; filename="content-studio-export.json"'
        response["Cache-Control"] = "no-store"
        return response


class ContentStudioDataDeletionAPIView(SocialWorkspaceScopedAPIView):
    """Owner-only deletion of Content Studio data, with an explicit confirmation."""

    def delete(self, request):
        if request.data.get("confirmation") != "DELETE CONTENT STUDIO":
            return Response(
                {"confirmation": ["Type DELETE CONTENT STUDIO to confirm deletion."]},
                status=400,
            )
        try:
            counts = delete_workspace_content(
                workspace=self.workspace(request),
                actor=request.user,
            )
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=409)
        return Response({"deleted": counts})
