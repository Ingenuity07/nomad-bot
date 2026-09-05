import hashlib
import hmac
import json

from django.conf import settings as django_settings
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from prospecting.models import Workspace

from .models import ContentBrief, LinkedInAutomationSettings, LinkedInPost
from .serializers import ContentBriefSerializer, LinkedInAutomationSettingsSerializer, LinkedInPostSerializer
from .services.images import LinkedInImageGenerator
from .services.scheduler import generate_post, upcoming_slots
from .tasks import publish_post


def get_settings():
    workspace = Workspace.objects.filter(name="Default Workspace").order_by("created_at").first()
    if workspace is None:
        workspace = Workspace.objects.create(name="Default Workspace", timezone="UTC")
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
    return settings


class DashboardAPIView(APIView):
    def get(self, request):
        settings = get_settings()
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


class SettingsAPIView(APIView):
    def get(self, request):
        return Response(LinkedInAutomationSettingsSerializer(get_settings()).data)

    def put(self, request):
        settings = get_settings()
        if request.data.get("is_active") is True and not settings.briefs.filter(is_active=True).exists():
            return Response({"detail": "Add your first content brief before starting the automation."}, status=400)
        serializer = LinkedInAutomationSettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class BriefListCreateAPIView(APIView):
    def get(self, request):
        return Response(ContentBriefSerializer(get_settings().briefs.all(), many=True).data)

    def post(self, request):
        serializer = ContentBriefSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        brief = serializer.save(settings=get_settings())
        return Response(ContentBriefSerializer(brief).data, status=status.HTTP_201_CREATED)


class BriefDetailAPIView(APIView):
    def patch(self, request, pk):
        brief = get_settings().briefs.get(pk=pk)
        serializer = ContentBriefSerializer(brief, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PostListAPIView(APIView):
    def get(self, request):
        queryset = get_settings().posts.select_related("brief")
        requested_status = request.query_params.get("status", "").strip().upper()
        if requested_status:
            queryset = queryset.filter(status=requested_status)
        return Response(LinkedInPostSerializer(queryset[:100], many=True).data)


class GeneratePostsAPIView(APIView):
    def post(self, request):
        settings = get_settings()
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


class PostDetailAPIView(APIView):
    def patch(self, request, pk):
        post = get_object_or_404(get_settings().posts.select_related("brief"), pk=pk)
        if post.status in {LinkedInPost.PUBLISHING, LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED, LinkedInPost.CANCELLED}:
            return Response({"detail": "This post can no longer be edited because it has left the editable queue."}, status=409)
        allowed = {"topic", "hook", "body", "hashtags", "image_prompt", "image_url", "alt_text", "scheduled_for"}
        payload = {key: value for key, value in request.data.items() if key in allowed}
        serializer = LinkedInPostSerializer(post, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PostImageAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, pk):
        post = LinkedInPost.objects.only("image_data", "image_content_type").filter(pk=pk).first()
        if not post or not post.image_data:
            raise Http404
        response = HttpResponse(bytes(post.image_data), content_type=post.image_content_type)
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        response["Content-Disposition"] = f'inline; filename="linkedin-{post.id}.png"'
        return response


class RegeneratePostImageAPIView(APIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings().posts.select_related("brief"), pk=pk)
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
        post.save(update_fields=["image_url", "image_data", "image_content_type", "generation_metadata", "updated_at"])
        return Response(LinkedInPostSerializer(post).data)


class ApprovePostAPIView(APIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings().posts.select_related("brief"), pk=pk)
        if post.status not in {LinkedInPost.DRAFT, LinkedInPost.FAILED}:
            return Response({"detail": "Only draft or failed posts can be approved."}, status=409)
        post.status = LinkedInPost.SCHEDULED
        post.approved_at = timezone.now()
        post.failure_reason = ""
        post.save(update_fields=["status", "approved_at", "failure_reason", "updated_at"])
        return Response(LinkedInPostSerializer(post).data)


class CancelPostAPIView(APIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings().posts.select_related("brief"), pk=pk)
        if post.status in {LinkedInPost.PUBLISHING, LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED}:
            return Response({"detail": "This post has already been sent to the publisher and cannot be cancelled here."}, status=409)
        post.status = LinkedInPost.CANCELLED
        post.save(update_fields=["status", "updated_at"])
        return Response(LinkedInPostSerializer(post).data)


class PublishNowAPIView(APIView):
    def post(self, request, pk):
        post = get_object_or_404(get_settings().posts.select_related("settings", "brief"), pk=pk)
        if post.status == LinkedInPost.DRAFT and post.settings.approval_mode == post.settings.APPROVAL_REQUIRED:
            return Response({"detail": "Approve this post before publishing it."}, status=409)
        if post.status not in {LinkedInPost.DRAFT, LinkedInPost.SCHEDULED, LinkedInPost.READY, LinkedInPost.FAILED}:
            return Response({"detail": "This post is already publishing, published, or cancelled."}, status=409)
        if post.settings.publisher == post.settings.MANUAL:
            post.status = LinkedInPost.READY
            post.failure_reason = ""
            post.save(update_fields=["status", "failure_reason", "updated_at"])
            return Response(LinkedInPostSerializer(post).data)
        post.status = LinkedInPost.PUBLISHING
        post.save(update_fields=["status", "updated_at"])
        publish_post(post)
        response_status = 200 if post.status in {LinkedInPost.SUBMITTED, LinkedInPost.PUBLISHED} else 502
        return Response(LinkedInPostSerializer(post).data, status=response_status)


class PublisherCallbackAPIView(APIView):
    """Accept an authenticated terminal update from an n8n/provider workflow."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        secrets = [
            value for value in (
                django_settings.N8N_LINKEDIN_WEBHOOK_SECRET,
                django_settings.LINKEDIN_PUBLISH_WEBHOOK_SECRET,
            ) if value
        ]
        if not secrets:
            return Response({"detail": "Publisher callback secret is not configured."}, status=503)
        supplied = request.headers.get("X-Nomad-Signature", "")
        valid_signature = any(
            hmac.compare_digest(
                supplied,
                hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest(),
            )
            for secret in secrets
        )
        if not supplied or not valid_signature:
            return Response({"detail": "Invalid callback signature."}, status=401)
        try:
            payload = json.loads(request.body)
        except (TypeError, ValueError):
            return Response({"detail": "Invalid JSON payload."}, status=400)
        post_id = payload.get("idempotency_key") or payload.get("post_id")
        post = get_object_or_404(LinkedInPost, pk=post_id)
        remote_status = str(payload.get("status", "")).lower()
        if remote_status in {"published", "sent", "success"}:
            post.status = LinkedInPost.PUBLISHED
            post.published_at = timezone.now()
            post.failure_reason = ""
        elif remote_status in {"failed", "error"}:
            post.status = LinkedInPost.FAILED
            post.failure_reason = str(payload.get("error") or "Publisher reported a failed LinkedIn post.")
        else:
            post.status = LinkedInPost.SUBMITTED
        post.external_post_id = str(payload.get("external_post_id") or post.external_post_id)
        post.generation_metadata = {**post.generation_metadata, "publisher_callback": payload}
        post.save()
        return Response({"ok": True, "status": post.status})
