import hashlib
import hmac
import json
import base64
from datetime import datetime, time
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from prospecting.models import Workspace

from .models import ContentBrief, LinkedInAutomationSettings, LinkedInPost
from .services.content import GeneratedPostContent, LinkedInContentGenerator
from .services.images import LinkedInImageGenerator
from .services.publishers import BufferPublisher
from .services.scheduler import generate_post, upcoming_slots
from .tasks import publish_post, sync_submitted_posts


class LinkedInSchedulerTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Route Floww")
        self.settings = LinkedInAutomationSettings.objects.create(
            workspace=self.workspace,
            page_name="Route Floww",
            timezone="Asia/Kolkata",
            schedule_days=[0, 2, 4],
            post_time=time(10, 0),
            posts_per_week=3,
        )
        self.brief = ContentBrief.objects.create(
            settings=self.settings,
            label="Planning insight",
            context="Dispatch teams need faster route changes.",
        )

    def test_upcoming_slots_follow_local_schedule(self):
        now = datetime(2026, 9, 7, 3, 0, tzinfo=ZoneInfo("UTC"))
        slots = upcoming_slots(self.settings, now=now, limit=3)
        self.assertEqual(len(slots), 3)
        self.assertEqual(slots[0].astimezone(ZoneInfo("Asia/Kolkata")).hour, 10)
        self.assertEqual([slot.astimezone(ZoneInfo("Asia/Kolkata")).weekday() for slot in slots], [0, 2, 4])

    def test_generation_defaults_to_approval_queue(self):
        generator = Mock()
        generator.generate.return_value = GeneratedPostContent(
            topic="Route changes",
            hook="Plans change.",
            body="Plans change. Your operation should keep moving.",
            hashtags=["#Logistics"],
            image_prompt="Green route illustration",
            alt_text="A route changing direction",
        )
        image_generator = Mock()
        image_generator.generate.return_value = ("", {"status": "not_configured"}, b"")
        post = generate_post(self.settings, self.brief, generator=generator, image_generator=image_generator)
        self.assertEqual(post.status, LinkedInPost.DRAFT)
        self.assertEqual(post.hashtags, ["#Logistics"])


class LinkedInContentGeneratorTests(TestCase):
    def test_invalid_llm_response_uses_safe_fallback(self):
        settings = Mock(
            page_name="Route Floww",
            company_description="Routing software",
            audience="Dispatchers",
            brand_voice="Practical",
            language="English",
            content_pillars=["Route planning"],
            calls_to_action=[],
            forbidden_topics=[],
            image_style="Editorial",
        )
        brief = Mock(label="Dispatch", context="Make route changes easier.")
        router = Mock()
        router.generate.return_value = {"type": "text", "text": "not json"}
        result = LinkedInContentGenerator(router=router).generate(settings, brief)
        self.assertIn("Route Floww", result.body)
        self.assertTrue(result.hashtags)


class LinkedInImageGeneratorTests(TestCase):
    @override_settings(
        LINKEDIN_GENERATE_IMAGES=True,
        LINKEDIN_IMAGE_PROVIDER="gemini",
        GEMINI_API_KEY="gemini-key",
        GEMINI_IMAGE_MODEL="gemini-3.1-flash-image",
        GEMINI_IMAGE_SIZE="1K",
    )
    @patch("integrations.linkedin.services.images.requests.post")
    def test_gemini_generates_four_by_five_feed_image(self, request_post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(b"image-bytes").decode("ascii"),
            }}]}}],
        }
        request_post.return_value = response

        url, metadata, image_data = LinkedInImageGenerator().generate("post-id", "A bridge representing trust")

        payload = request_post.call_args.kwargs["json"]
        image_format = payload["generationConfig"]["responseFormat"]["image"]
        self.assertEqual(image_format["aspectRatio"], "ASPECT_RATIO_FOUR_BY_FIVE")
        self.assertEqual(image_format["imageSize"], "IMAGE_SIZE_ONE_K")
        self.assertIn("single clear focal concept", payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(image_data, b"image-bytes")
        self.assertEqual(metadata["provider"], "gemini")
        self.assertIn("post-id", url)

    @override_settings(
        LINKEDIN_GENERATE_IMAGES=True,
        LINKEDIN_IMAGE_PROVIDER="gemini",
        GEMINI_API_KEY="gemini-key",
        GEMINI_IMAGE_MODEL="gemini-3.1-flash-image",
    )
    @patch("integrations.linkedin.services.images.requests.post")
    def test_gemini_quota_error_is_actionable(self, request_post):
        response = Mock(status_code=429)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        response.json.return_value = {"error": {"message": "Quota exceeded"}}
        request_post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "Enable billing or increase"):
            LinkedInImageGenerator().generate("post-id", "A delivery route")


class LinkedInAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_dashboard_bootstraps_configuration(self):
        response = self.client.get(reverse("linkedin-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["settings"]["page_name"], "Your business")

    def test_configuration_can_be_updated(self):
        response = self.client.put(
            reverse("linkedin-settings"),
            {"timezone": "Asia/Kolkata", "schedule_days": [1, 3], "posts_per_week": 2},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["schedule_days"], [1, 3])

    def test_approve_post(self):
        self.client.get(reverse("linkedin-dashboard"))
        settings = LinkedInAutomationSettings.objects.get(workspace__name="Default Workspace")
        post = LinkedInPost.objects.create(
            settings=settings,
            topic="Test",
            body="Test body",
            scheduled_for=timezone.now(),
        )
        response = self.client.post(reverse("linkedin-approve", args=[post.id]), format="json")
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, LinkedInPost.SCHEDULED)

    def test_generated_image_has_stable_endpoint(self):
        self.client.get(reverse("linkedin-dashboard"))
        settings = LinkedInAutomationSettings.objects.get(workspace__name="Default Workspace")
        post = LinkedInPost.objects.create(
            settings=settings,
            topic="Image test",
            body="Test body",
            scheduled_for=timezone.now(),
            image_data=b"png-bytes",
        )
        response = self.client.get(reverse("linkedin-post-image", args=[post.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"png-bytes")
        self.assertEqual(response["Content-Type"], "image/png")

    @override_settings(N8N_LINKEDIN_WEBHOOK_SECRET="callback-secret")
    def test_signed_publisher_callback_confirms_publication(self):
        self.client.get(reverse("linkedin-dashboard"))
        settings = LinkedInAutomationSettings.objects.get(workspace__name="Default Workspace")
        post = LinkedInPost.objects.create(
            settings=settings,
            topic="Callback test",
            body="Test body",
            scheduled_for=timezone.now(),
            status=LinkedInPost.SUBMITTED,
        )
        body = json.dumps({
            "idempotency_key": str(post.id),
            "external_post_id": "linkedin-post-id",
            "status": "published",
        }).encode("utf-8")
        signature = hmac.new(b"callback-secret", body, hashlib.sha256).hexdigest()

        response = self.client.post(
            reverse("linkedin-publisher-callback"),
            data=body,
            content_type="application/json",
            HTTP_X_NOMAD_SIGNATURE=signature,
        )

        post.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.status, LinkedInPost.PUBLISHED)
        self.assertEqual(post.external_post_id, "linkedin-post-id")

    @patch("integrations.linkedin.views.LinkedInImageGenerator.generate")
    def test_draft_image_can_be_regenerated(self, generate_image):
        self.client.get(reverse("linkedin-dashboard"))
        settings = LinkedInAutomationSettings.objects.get(workspace__name="Default Workspace")
        post = LinkedInPost.objects.create(
            settings=settings,
            topic="Image refresh",
            body="Test body",
            image_prompt="One strong visual metaphor",
            scheduled_for=timezone.now(),
        )
        generate_image.return_value = (
            f"https://example.com/{post.id}.png",
            {"status": "generated", "provider": "gemini", "content_type": "image/png"},
            b"new-image",
        )

        response = self.client.post(reverse("linkedin-regenerate-image", args=[post.id]), format="json")

        post.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(bytes(post.image_data), b"new-image")
        self.assertEqual(post.generation_metadata["image"]["provider"], "gemini")


class LinkedInPublisherTests(TestCase):
    def setUp(self):
        workspace = Workspace.objects.create(name="Publisher Workspace")
        self.settings = LinkedInAutomationSettings.objects.create(
            workspace=workspace,
            publisher=LinkedInAutomationSettings.BUFFER,
        )
        self.post = LinkedInPost.objects.create(
            settings=self.settings,
            topic="Publisher test",
            body="A useful route planning idea.",
            hashtags=["#Logistics"],
            image_url="https://cdn.example.com/post.png",
            scheduled_for=timezone.now(),
            status=LinkedInPost.PUBLISHING,
        )

    @override_settings(BUFFER_API_KEY="buffer-key", BUFFER_CHANNEL_ID="linkedin-channel")
    @patch("integrations.linkedin.services.publishers.requests.post")
    def test_buffer_submission_uses_linkedin_channel_and_image(self, request_post):
        response = Mock()
        response.json.return_value = {"data": {"createPost": {"post": {"id": "buffer-post-1"}}}}
        response.raise_for_status.return_value = None
        request_post.return_value = response

        result = BufferPublisher().publish(self.post)

        self.assertEqual(result.state, "SUBMITTED")
        sent_input = request_post.call_args.kwargs["json"]["variables"]["input"]
        self.assertEqual(sent_input["channelId"], "linkedin-channel")
        self.assertEqual(sent_input["mode"], "shareNow")
        self.assertEqual(sent_input["assets"][0]["image"]["url"], self.post.image_url)

    @override_settings(BUFFER_API_KEY="buffer-key", BUFFER_CHANNEL_ID="linkedin-channel")
    @patch("integrations.linkedin.services.publishers.requests.post")
    def test_submission_is_not_marked_published_until_buffer_confirms(self, request_post):
        response = Mock()
        response.json.return_value = {"data": {"createPost": {"post": {"id": "buffer-post-2"}}}}
        response.raise_for_status.return_value = None
        request_post.return_value = response

        publish_post(self.post)

        self.assertEqual(self.post.status, LinkedInPost.SUBMITTED)
        self.assertIsNone(self.post.published_at)

    @override_settings(BUFFER_API_KEY="buffer-key", BUFFER_CHANNEL_ID="linkedin-channel")
    @patch("integrations.linkedin.services.publishers.requests.post")
    def test_buffer_sync_marks_sent_post_published(self, request_post):
        self.post.status = LinkedInPost.SUBMITTED
        self.post.external_post_id = "buffer-post-3"
        self.post.save()
        response = Mock()
        response.json.return_value = {"data": {"post": {"id": "buffer-post-3", "status": "sent"}}}
        response.raise_for_status.return_value = None
        request_post.return_value = response

        result = sync_submitted_posts()

        self.post.refresh_from_db()
        self.assertEqual(result["published"], 1)
        self.assertEqual(self.post.status, LinkedInPost.PUBLISHED)
        self.assertIsNotNone(self.post.published_at)
