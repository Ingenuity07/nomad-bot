from django.db import migrations


POST_STATES = {
    "DRAFT": "DRAFT",
    "SCHEDULED": "SCHEDULED",
    "READY": "READY",
    "PUBLISHING": "PUBLISHING",
    "SUBMITTED": "SUBMITTED",
    "PUBLISHED": "PUBLISHED",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
}

JOB_STATES = {
    "SCHEDULED": "PENDING",
    "READY": "PENDING",
    "PUBLISHING": "CLAIMED",
    "SUBMITTED": "SUBMITTED",
    "PUBLISHED": "SUCCEEDED",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
}
APPROVED_STATES = {"SCHEDULED", "READY", "PUBLISHING", "SUBMITTED", "PUBLISHED", "FAILED"}


def import_linkedin_content(apps, schema_editor):
    LegacySettings = apps.get_model("linkedin_automation", "LinkedInAutomationSettings")
    LegacyBrief = apps.get_model("linkedin_automation", "ContentBrief")
    LegacyPost = apps.get_model("linkedin_automation", "LinkedInPost")
    SocialSettings = apps.get_model("social_content", "SocialWorkspaceSettings")
    BrandProfile = apps.get_model("social_content", "BrandProfile")
    ContentSource = apps.get_model("social_content", "ContentSource")
    SocialConnection = apps.get_model("social_content", "SocialConnection")
    SocialPost = apps.get_model("social_content", "SocialPost")
    SocialPostVariant = apps.get_model("social_content", "SocialPostVariant")
    SocialPostVersion = apps.get_model("social_content", "SocialPostVersion")
    MediaAsset = apps.get_model("social_content", "MediaAsset")
    PublishJob = apps.get_model("social_content", "PublishJob")

    connection_map = {}
    for legacy in LegacySettings.objects.all().iterator():
        social_settings = SocialSettings.objects.create(
            workspace_id=legacy.workspace_id,
            brand_name=legacy.page_name,
            language=legacy.language,
            timezone=legacy.timezone,
            approval_mode=legacy.approval_mode,
            schedule_days=legacy.schedule_days,
            post_time=legacy.post_time,
            posts_per_week=legacy.posts_per_week,
            queue_horizon_days=legacy.queue_horizon_days,
            is_active=legacy.is_active,
            legacy_linkedin_settings_id=legacy.id,
        )
        SocialSettings.objects.filter(pk=social_settings.pk).update(
            created_at=legacy.created_at,
            updated_at=legacy.updated_at,
        )
        BrandProfile.objects.create(
            settings_id=social_settings.id,
            audience=legacy.audience,
            voice=legacy.brand_voice,
            content_pillars=legacy.content_pillars,
            calls_to_action=legacy.calls_to_action,
            visual_direction=legacy.image_style,
            forbidden_topics=legacy.forbidden_topics,
        )
        provider = legacy.publisher if legacy.publisher in {"MANUAL", "BUFFER", "N8N", "WEBHOOK"} else "MANUAL"
        connection = SocialConnection.objects.create(
            workspace_id=legacy.workspace_id,
            network="LINKEDIN",
            provider=provider,
            display_name=legacy.page_name,
            account_type="ORGANIZATION",
            status="DISCONNECTED" if provider == "MANUAL" else "CONNECTING",
            capabilities={
                "publish_text": True,
                "publish_image": True,
                "legacy_import": True,
            },
            legacy_linkedin_settings_id=legacy.id,
        )
        connection_map[legacy.id] = connection.id

    source_map = {}
    for brief in LegacyBrief.objects.select_related("settings").all().iterator():
        source = ContentSource.objects.create(
            workspace_id=brief.settings.workspace_id,
            source_type="TEXT",
            label=brief.label,
            text_content=brief.context,
            metadata={"is_evergreen": brief.is_evergreen, "legacy_import": True},
            is_reusable=brief.is_evergreen,
            is_active=brief.is_active,
            legacy_linkedin_brief_id=brief.id,
        )
        ContentSource.objects.filter(pk=source.pk).update(
            created_at=brief.created_at,
            updated_at=brief.updated_at,
        )
        source_map[brief.id] = source.id

    for legacy in LegacyPost.objects.select_related("settings").all().iterator():
        state = POST_STATES.get(legacy.status, "DRAFT")
        social_post = SocialPost.objects.create(
            workspace_id=legacy.settings.workspace_id,
            source_id=source_map.get(legacy.brief_id),
            idea_title=legacy.topic,
            idea_text=legacy.hook,
            state=state,
            metadata={
                "generation": legacy.generation_metadata,
                "legacy_import": True,
            },
            legacy_linkedin_post_id=legacy.id,
        )
        SocialPost.objects.filter(pk=social_post.pk).update(
            created_at=legacy.created_at,
            updated_at=legacy.updated_at,
        )
        variant = SocialPostVariant.objects.create(
            post_id=social_post.id,
            connection_id=connection_map.get(legacy.settings_id),
            network="LINKEDIN",
            copy=legacy.body,
            hashtags=legacy.hashtags,
            scheduled_for=legacy.scheduled_for,
            status=state,
            failure_reason=legacy.failure_reason,
        )
        SocialPostVariant.objects.filter(pk=variant.pk).update(
            created_at=legacy.created_at,
            updated_at=legacy.updated_at,
        )

        media_snapshot = []
        if legacy.image_url or legacy.image_data:
            storage_url = legacy.image_url or f"/api/v3/linkedin/posts/{legacy.id}/image/"
            asset = MediaAsset.objects.create(
                workspace_id=legacy.settings.workspace_id,
                post_id=social_post.id,
                asset_type="IMAGE",
                storage_url=storage_url,
                alt_text=legacy.alt_text,
                metadata={
                    "content_type": legacy.image_content_type,
                    "image_prompt": legacy.image_prompt,
                    "legacy_import": True,
                },
                legacy_linkedin_post_id=legacy.id,
            )
            media_snapshot.append({
                "asset_id": str(asset.id),
                "asset_type": "IMAGE",
                "storage_url": storage_url,
                "alt_text": legacy.alt_text,
            })

        approved_at = legacy.approved_at
        if approved_at is None and legacy.status in APPROVED_STATES:
            approved_at = legacy.created_at
        version = SocialPostVersion.objects.create(
            variant_id=variant.id,
            version=1,
            copy=legacy.body,
            hashtags=legacy.hashtags,
            media_snapshot=media_snapshot,
            approved_at=approved_at,
        )
        job_state = JOB_STATES.get(legacy.status)
        if legacy.status == "CANCELLED" and legacy.approved_at is None and not legacy.external_post_id:
            job_state = None
        if job_state:
            PublishJob.objects.create(
                variant_id=variant.id,
                approved_version_id=version.id,
                provider=legacy.settings.publisher,
                idempotency_key=str(legacy.id),
                attempt_count=1 if legacy.status in {"PUBLISHING", "SUBMITTED", "PUBLISHED", "FAILED"} else 0,
                external_id=legacy.external_post_id,
                status=job_state,
                error_details={"message": legacy.failure_reason} if legacy.failure_reason else {},
                submitted_at=legacy.updated_at if legacy.status in {"SUBMITTED", "PUBLISHED"} else None,
                completed_at=legacy.published_at if legacy.status == "PUBLISHED" else None,
            )


def remove_imported_linkedin_content(apps, schema_editor):
    PublishJob = apps.get_model("social_content", "PublishJob")
    SocialPost = apps.get_model("social_content", "SocialPost")
    ContentSource = apps.get_model("social_content", "ContentSource")
    SocialConnection = apps.get_model("social_content", "SocialConnection")
    SocialSettings = apps.get_model("social_content", "SocialWorkspaceSettings")

    PublishJob.objects.filter(variant__post__legacy_linkedin_post_id__isnull=False).delete()
    SocialPost.objects.filter(legacy_linkedin_post_id__isnull=False).delete()
    ContentSource.objects.filter(legacy_linkedin_brief_id__isnull=False).delete()
    SocialConnection.objects.filter(legacy_linkedin_settings_id__isnull=False).delete()
    SocialSettings.objects.filter(legacy_linkedin_settings_id__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0001_initial"),
        ("linkedin_automation", "0003_generic_business_defaults"),
    ]

    operations = [
        migrations.RunPython(import_linkedin_content, remove_imported_linkedin_content),
    ]
