import hashlib
import hmac
import json

from django.conf import settings as django_settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework import status
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from integrations.social.models import ProviderEvent
from integrations.social.services.linkedin_compat import record_provider_event, sync_post, sync_settings, sync_source

from .models import ContentBrief, LinkedInAutomationSettings, LinkedInPost
from .assets import asset_token_payload
from .serializers import ContentBriefSerializer, LinkedInAutomationSettingsSerializer, LinkedInPostSerializer
from .services.images import LinkedInImageGenerator
from .services.scheduler import generate_post, upcoming_slots
from .tasks import publish_post
from .workspaces import resolve_active_workspace


class LinkedInCompatibilityEnvelopeSerializer(serializers.Serializer):
    """Schema placeholder for the legacy compatibility endpoints."""


def get_settings(request):
    workspace = resolve_active_workspace(request)
    settings, _ = LinkedInAutomationSettings.objects.get_or_create(
        workspace=workspace,
        defaults={
            "page_name": "Your business",
            "company_description": "",
            "audience": "",
            "content_pillars": [],
            "calls_to_action": [],
            "schedule_days": [0, 1, 2, 3, 4],
        },
    )
    sync_settings(settings)
    return settings


class WorkspaceScopedAPIView(GenericAPIView):
    # BasicAuthentication is first so unauthenticated requests consistently
    # receive 401 instead of SessionAuthentication's anonymous 403 response.
    authentication_classes = [BasicAuthentication, SessionAuthentication]
    serializer_class = LinkedInCompatibilityEnvelopeSerializer


class DashboardAPIView(WorkspaceScopedAPIView):
    def get(self, request):
        settings = get_settings(request)
        counts = {item["status"]: item["count"] for item in settings.posts.values("status").annotate(count=Count("id"))}
        posts = settings.posts.select_related("brief").all()[:50]
        next_slots = upcoming_slots(settings, limit=5)
        return Response({
            "settings": LinkedInAutomationSettingsSerializer(settings).data,
            "counts": counts,
            "posts": LinkedInPostSerializer(posts, many=True).data,
            "briefs": ContentBriefSerializer(settings.briefs.all()[:20], many=True).data,
            "next_slots": [slot.isoformat() for slot in next_slots],
            "server_time": timezone.now().isoformat(),
        })


class SettingsAPIView(WorkspaceScopedAPIView):
    def get(self, request):
        return Response(LinkedInAutomationSettingsSerializer(get_settings(request)).data)

    def put(self, request):
        settings = get_settings(request)
        if request.data.get("is_active") is True and not settings.briefs.filter(is_active=True).exists():
            return Response({"detail": "Add your first content brief before starting the automation."}, status=400)
        serializer = LinkedInAutomationSettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = serializer.save()
        sync_settings(saved)
        return Response(serializer.data)


class BriefListCreateAPIView(WorkspaceScopedAPIView):
    def get(self, request):
        return Response(ContentBriefSerializer(get_settings(request).briefs.all(), many=True).data)

    def post(self, request):
        serializer = ContentBriefSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        brief = serializer.save(settings=get_settings(request))
        sync_source(brief)
        return Response(ContentBriefSerializer(brief).data, status=status.HTTP_201_CREATED)


class BriefDetailAPIView(WorkspaceScopedAPIView):
    def patch(self, request, pk):
        brief = get_object_or_404(get_settings(request).briefs, pk=pk)
        serializer = ContentBriefSerializer(brief, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = serializer.save()
        sync_source(saved)
        return Response(serializer.data)


class PostListAPIView(WorkspaceScopedAPIView):
    def get(self, request):
        queryset = get_settings(request).posts.select_related("brief")
        requested_status = request.query_params.get("status", "").strip().upper()
        if requested_status:
            queryset = queryset.filter(status=requested_status)
        return Response(LinkedInPostSerializer(queryset[:100], many=True).data)


class GeneratePostsAPIView(WorkspaceScopedAPIView):
    def post(self, request):
        settings = get_settings(request)
        try:
            count = max(1, min(int(request.data.get("count", 1)), 7))
        except (TypeError, ValueError):
            return Response({"count": ["Enter a number from 1 to 7."]}, status=status.HTTP_400_BAD_REQUEST)

        brief = None
        brief_id = request.data.get("brief_id")
        if brief_id:
            brief = settings.briefs.filter(pk=brief_id, is_active=True).first()
            if not brief:
                return Response({"brief_id": ["Active content brief not found."]}, status=status.HTTP_400_BAD_REQUEST)
        context = str(request.data.get("context", "")).strip()
        if context:
            brief = ContentBrief.objects.create(
                settings=settings,
                label=str(request.data.get("label", "Fresh context"))[:255],
                context=context,
                is_evergreen=bool(request.data.get("is_evergreen", False)),
            )

        requested_time = request.data.get("scheduled_for")
        first_slot = parse_datetime(requested_time) if requested_time else None
        if requested_time and first_slot is None:
            return Response({"scheduled_for": ["Use a valid ISO-8601 date and time."]}, status=400)
        if first_slot is not None and timezone.is_naive(first_slot):
            first_slot = timezone.make_aware(first_slot)
        try:
            slots = [first_slot] if first_slot else upcoming_slots(settings, limit=count)
            if len(slots) < count:
                return Response({"detail": "Not enough free schedule slots. Increase the queue horizon or choose more days."}, status=400)
            posts = [generate_post(settings, brief=brief, scheduled_for=slot) for slot in slots[:count]]
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(LinkedInPostSerializer(posts, many=True).data, status=status.HTTP_201_CREATED)


class PostDetailAPIView(WorkspaceScopedAPIView):
    def patch(self, request, pk):
        post = get_object_or_404(get_settings(request).posts.select_related("brief"), pk=pk)
        if post.status in {LinkedInPost.PUBLISHING, LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED, LinkedInPost.CANCELLED}:
            return Response({"detail": "This post can no longer be edited because it has left the editable queue."}, status=409)
        allowed = {"topic", "hook", "body", "hashtags", "image_prompt", "image_url", "alt_text", "scheduled_for"}
        payload = {key: value for key, value in request.data.items() if key in allowed}
        serializer = LinkedInPostSerializer(post, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        approval_sensitive = {"body", "hashtags", "image_url", "alt_text", "scheduled_for"}
        revokes_approval = any(
            key in serializer.validated_data
            and serializer.validated_data[key] != getattr(post, key)
            for key in approval_sensitive
        )
        saved = serializer.save(
            status=LinkedInPost.DRAFT if revokes_approval else post.status,
            approved_at=None if revokes_approval else post.approved_at,
        )
        sync_post(saved)
        return Response(serializer.data)


class PostImageAPIView(WorkspaceScopedAPIView):
    def get(self, request, pk):
        signed_asset_request = False
        if request.user.is_authenticated:
            workspace = resolve_active_workspace(request)
            post = LinkedInPost.objects.only("image_data", "image_content_type").filter(
                pk=pk,
                settings__workspace=workspace,
            ).first()
        elif request.query_params.get("asset_token"):
            signed_asset_request = True
            try:
                payload = asset_token_payload(request.query_params["asset_token"])
            except signing.BadSignature:
                post = None
            else:
                post = None
                if str(pk) == payload.get("post_id"):
                    post = LinkedInPost.objects.only("image_data", "image_content_type").filter(
                        pk=pk,
                        settings_id=payload.get("settings_id"),
                    ).first()
        else:
            workspace = resolve_active_workspace(request)
            post = LinkedInPost.objects.only("image_data", "image_content_type").filter(
                pk=pk,
                settings__workspace=workspace,
            ).first()
        if not post or not post.image_data:
            raise Http404
        response = HttpResponse(bytes(post.image_data), content_type=post.image_content_type)
        if signed_asset_request:
            response["Cache-Control"] = (
                f"public, max-age={django_settings.CONTENT_AUTOMATION_ASSET_TOKEN_MAX_AGE_SECONDS}, immutable"
            )
        else:
            response["Cache-Control"] = "private, max-age=300"
        response["Content-Disposition"] = f'inline; filename="linkedin-{post.id}.png"'
        return response


class RegeneratePostImageAPIView(WorkspaceScopedAPIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings(request).posts.select_related("brief"), pk=pk)
        if post.status in {LinkedInPost.PUBLISHING, LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED, LinkedInPost.CANCELLED}:
            return Response({"detail": "This image cannot be changed after the post leaves the editable queue."}, status=409)
        try:
            image_url, metadata, image_data = LinkedInImageGenerator().generate(post.id, post.image_prompt)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=502)
        if not image_data:
            return Response({"detail": metadata.get("detail") or "Image generation is not configured."}, status=400)
        post.image_url = image_url
        post.image_data = image_data
        post.image_content_type = metadata.get("content_type", "image/png")
        post.generation_metadata = {**post.generation_metadata, "image": metadata}
        post.status = LinkedInPost.DRAFT
        post.approved_at = None
        post.save(update_fields=[
            "image_url", "image_data", "image_content_type", "generation_metadata",
            "status", "approved_at", "updated_at",
        ])
        sync_post(post)
        return Response(LinkedInPostSerializer(post).data)


class ApprovePostAPIView(WorkspaceScopedAPIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings(request).posts.select_related("brief"), pk=pk)
        if post.status not in {LinkedInPost.DRAFT, LinkedInPost.FAILED}:
            return Response({"detail": "Only draft or failed posts can be approved."}, status=409)
        post.status = LinkedInPost.SCHEDULED
        post.approved_at = timezone.now()
        post.failure_reason = ""
        post.save(update_fields=["status", "approved_at", "failure_reason", "updated_at"])
        sync_post(post)
        return Response(LinkedInPostSerializer(post).data)


class CancelPostAPIView(WorkspaceScopedAPIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings(request).posts.select_related("brief"), pk=pk)
        if post.status in {LinkedInPost.PUBLISHING, LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED}:
            return Response({"detail": "This post has already been sent to the publisher and cannot be cancelled here."}, status=409)
        post.status = LinkedInPost.CANCELLED
        post.save(update_fields=["status", "updated_at"])
        sync_post(post)
        return Response(LinkedInPostSerializer(post).data)


class PublishNowAPIView(WorkspaceScopedAPIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings(request).posts.select_related("settings", "brief"), pk=pk)
        if post.status == LinkedInPost.DRAFT and post.settings.approval_mode == post.settings.APPROVAL_REQUIRED:
            return Response({"detail": "Approve this post before publishing it."}, status=409)
        if post.status not in {LinkedInPost.DRAFT, LinkedInPost.SCHEDULED, LinkedInPost.READY, LinkedInPost.FAILED}:
            return Response({"detail": "This post is already publishing, published, or cancelled."}, status=409)
        if post.settings.publisher == post.settings.MANUAL:
            post.status = LinkedInPost.READY
            post.failure_reason = ""
            post.save(update_fields=["status", "failure_reason", "updated_at"])
            sync_post(post)
            return Response(LinkedInPostSerializer(post).data)
        post.status = LinkedInPost.PUBLISHING
        post.save(update_fields=["status", "updated_at"])
        publish_post(post)
        response_status = 200 if post.status in {LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED} else 502
        return Response(LinkedInPostSerializer(post).data, status=response_status)


class PublisherCallbackAPIView(GenericAPIView):
    """Accept an authenticated terminal update from an n8n/provider workflow."""

    authentication_classes = []
    permission_classes = []
    serializer_class = LinkedInCompatibilityEnvelopeSerializer

    def post(self, request):
        secrets = [
            value for value in (
                django_settings.N8N_LINKEDIN_WEBHOOK_SECRET,
                django_settings.LINKEDIN_PUBLISH_WEBHOOK_SECRET,
            ) if value
        ]
        if not secrets:
            return Response({"detail": "Publisher callback secret is not configured."}, status=503)
        supplied_timestamp = request.headers.get("X-Nomad-Timestamp", "")
        if django_settings.LINKEDIN_LEGACY_CALLBACK_REQUIRE_TIMESTAMP and not supplied_timestamp:
            return Response({"detail": "Callback timestamp is required."}, status=401)
        if supplied_timestamp:
            try:
                timestamp = int(supplied_timestamp)
            except (TypeError, ValueError):
                return Response({"detail": "Invalid callback timestamp."}, status=401)
            if abs(int(timezone.now().timestamp()) - timestamp) > django_settings.LINKEDIN_LEGACY_CALLBACK_MAX_AGE_SECONDS:
                return Response({"detail": "Callback timestamp has expired."}, status=401)
        supplied = request.headers.get("X-Nomad-Signature", "")
        signed_body = (
            supplied_timestamp.encode("utf-8") + b"." + request.body
            if supplied_timestamp
            else request.body
        )
        valid_signature = any(
            hmac.compare_digest(
                supplied,
                hmac.new(secret.encode("utf-8"), signed_body, hashlib.sha256).hexdigest(),
            )
            for secret in secrets
        )
        if not supplied or not valid_signature:
            return Response({"detail": "Invalid callback signature."}, status=401)
        try:
            payload = json.loads(request.body)
        except (TypeError, ValueError):
            return Response({"detail": "Invalid JSON payload."}, status=400)
        event_id = str(
            request.headers.get("X-Nomad-Delivery-ID")
            or payload.get("event_id")
            or hashlib.sha256(request.body).hexdigest()
        )[:500]
        if ProviderEvent.objects.filter(external_event_id=event_id).exists():
            return Response({"detail": "Callback has already been processed."}, status=409)
        post_id = payload.get("idempotency_key") or payload.get("post_id")
        try:
            with transaction.atomic():
                post = get_object_or_404(
                    LinkedInPost.objects.select_for_update(),
                    pk=post_id,
                )
                remote_status = str(payload.get("status", "")).lower()
                if remote_status in {"published", "sent", "success"}:
                    post.status = LinkedInPost.PUBLISHED
                    post.published_at = timezone.now()
                    post.failure_reason = ""
                elif remote_status in {"failed", "error"}:
                    post.status = LinkedInPost.FAILED
                    post.failure_reason = "The publishing workflow reported that this post failed."
                else:
                    post.status = LinkedInPost.SUBMITTED
                post.external_post_id = str(payload.get("external_post_id") or post.external_post_id)[:500]
                safe_callback = {
                    "event_id": event_id,
                    "status": remote_status,
                    "has_external_post_id": bool(post.external_post_id),
                }
                post.generation_metadata = {
                    **post.generation_metadata,
                    "publisher_callback": safe_callback,
                }
                post.save()
                record_provider_event(
                    post,
                    "publisher.callback",
                    safe_callback,
                    external_event_id=event_id,
                )
        except IntegrityError:
            return Response({"detail": "Callback has already been processed."}, status=409)
        return Response({"ok": True, "status": post.status})
