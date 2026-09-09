from rest_framework import serializers

from integrations.social.models import ContentSource, MediaAsset, SocialPost, SocialPostVariant
from integrations.social.services.composer import NETWORK_LABELS, variant_validation


class MediaAssetSerializer(serializers.ModelSerializer):
    publish_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id",
            "asset_type",
            "source",
            "original_filename",
            "content_type",
            "byte_size",
            "checksum_sha256",
            "width",
            "height",
            "duration_ms",
            "alt_text",
            "sort_order",
            "publish_url",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_publish_url(self, obj):
        return obj.publish_url


class ContentSourceSummarySerializer(serializers.ModelSerializer):
    text_content = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()

    class Meta:
        model = ContentSource
        fields = ("id", "source_type", "label", "text_content", "source_url", "original_filename", "processing_status", "metadata", "owner_name", "updated_at")
        read_only_fields = fields

    @staticmethod
    def get_text_content(obj):
        return obj.extracted_text if obj.processing_status == "READY" else ""

    @staticmethod
    def get_owner_name(obj):
        if not obj.owner:
            return "Workspace"
        return obj.owner.get_full_name() or obj.owner.get_username()


class SocialPostVariantSerializer(serializers.ModelSerializer):
    network_label = serializers.SerializerMethodField()
    account = serializers.SerializerMethodField()
    media = MediaAssetSerializer(source="media_assets", many=True, read_only=True)
    validation = serializers.SerializerMethodField()

    class Meta:
        model = SocialPostVariant
        fields = (
            "id",
            "network",
            "network_label",
            "account",
            "copy",
            "hashtags",
            "scheduled_for",
            "status",
            "metadata",
            "media",
            "validation",
            "updated_at",
        )
        read_only_fields = fields

    def get_network_label(self, obj):
        return NETWORK_LABELS[obj.network]

    @staticmethod
    def get_account(obj):
        if not obj.connection:
            return None
        return {
            "id": str(obj.connection.id),
            "display_name": obj.connection.display_name,
            "account_type": obj.connection.get_account_type_display(),
            "health": "HEALTHY" if obj.connection.status == "CONNECTED" else "NEEDS_ATTENTION",
        }

    @staticmethod
    def get_validation(obj):
        return variant_validation(obj)


class SocialPostSerializer(serializers.ModelSerializer):
    source = ContentSourceSummarySerializer(read_only=True)
    sources = serializers.SerializerMethodField()
    brand_brain_version = serializers.SerializerMethodField()
    controls = serializers.SerializerMethodField()
    variants = SocialPostVariantSerializer(many=True, read_only=True)

    class Meta:
        model = SocialPost
        fields = (
            "id",
            "idea_title",
            "idea_text",
            "source",
            "sources",
            "brand_brain_version",
            "state",
            "controls",
            "variants",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @staticmethod
    def get_controls(obj):
        return obj.metadata.get("generation_controls", {})

    @staticmethod
    def get_sources(obj):
        references = list(obj.source_references.select_related("source").all())
        sources = [reference.source for reference in references]
        if not sources and obj.source:
            sources = [obj.source]
        return ContentSourceSummarySerializer(sources, many=True).data

    @staticmethod
    def get_brand_brain_version(obj):
        version = obj.brand_profile_version
        return {"id": str(version.id), "version": version.version} if version else None
