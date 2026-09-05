from rest_framework import serializers

from .models import ContentBrief, LinkedInAutomationSettings, LinkedInPost


class LinkedInAutomationSettingsSerializer(serializers.ModelSerializer):
    provider_ready = serializers.SerializerMethodField()
    image_provider_ready = serializers.SerializerMethodField()

    class Meta:
        model = LinkedInAutomationSettings
        fields = [
            "id", "page_name", "company_description", "audience", "brand_voice",
            "content_pillars", "calls_to_action", "forbidden_topics", "image_style",
            "language", "timezone", "schedule_days", "post_time", "posts_per_week",
            "queue_horizon_days", "approval_mode", "publisher", "is_active",
            "provider_ready", "image_provider_ready", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "provider_ready", "image_provider_ready", "created_at", "updated_at"]

    def validate_schedule_days(self, value):
        if not isinstance(value, list) or any(not isinstance(day, int) or day < 0 or day > 6 for day in value):
            raise serializers.ValidationError("Use weekday numbers from 0 (Monday) to 6 (Sunday).")
        return sorted(set(value))

    def validate_posts_per_week(self, value):
        if value < 1 or value > 7:
            raise serializers.ValidationError("Posts per week must be between 1 and 7.")
        return value

    def get_provider_ready(self, obj):
        from .services.publishers import publisher_status

        return publisher_status(obj)

    def get_image_provider_ready(self, obj):
        from .services.images import image_provider_status

        return image_provider_status()


class ContentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContentBrief
        fields = ["id", "label", "context", "is_evergreen", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class LinkedInPostSerializer(serializers.ModelSerializer):
    brief_label = serializers.CharField(source="brief.label", read_only=True, default="")
    character_count = serializers.SerializerMethodField()

    class Meta:
        model = LinkedInPost
        fields = [
            "id", "brief", "brief_label", "topic", "hook", "body", "hashtags",
            "image_prompt", "image_url", "alt_text", "status", "scheduled_for",
            "approved_at", "published_at", "external_post_id", "failure_reason",
            "generation_metadata", "character_count", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "approved_at", "published_at", "external_post_id", "failure_reason",
            "generation_metadata", "character_count", "created_at", "updated_at",
        ]

    def get_character_count(self, obj):
        tags = " ".join(obj.hashtags)
        return len(f"{obj.body}\n\n{tags}".strip())
