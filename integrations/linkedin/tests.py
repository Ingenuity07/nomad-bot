from datetime import datetime, time
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from prospecting.models import Workspace

from .models import ContentBrief, LinkedInAutomationSettings, LinkedInPost
from .services.content import GeneratedPostContent, LinkedInContentGenerator
from .services.scheduler import generate_post, upcoming_slots


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


class LinkedInAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_dashboard_bootstraps_configuration(self):
        response = self.client.get(reverse("linkedin-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["settings"]["page_name"], "Route Floww")

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
