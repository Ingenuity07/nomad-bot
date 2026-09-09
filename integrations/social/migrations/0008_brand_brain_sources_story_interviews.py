import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_compatibility_snapshots(apps, schema_editor):
    BrandProfile = apps.get_model("social_content", "BrandProfile")
    BrandProfileVersion = apps.get_model("social_content", "BrandProfileVersion")
    ContentSource = apps.get_model("social_content", "ContentSource")
    SocialPost = apps.get_model("social_content", "SocialPost")
    SocialPostSource = apps.get_model("social_content", "SocialPostSource")

    for source in ContentSource.objects.all():
        metadata = source.metadata if isinstance(source.metadata, dict) else {}
        source.extracted_text = source.text_content or metadata.get("transcript") or metadata.get("summary") or ""
        source.processing_status = "READY" if source.extracted_text else "PENDING"
        source.save(update_fields=["extracted_text", "processing_status"])
    for profile in BrandProfile.objects.select_related("settings"):
        snapshot = {
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
        }
        version = BrandProfileVersion.objects.create(brand_profile=profile, version=1, snapshot=snapshot)
        SocialPost.objects.filter(workspace_id=profile.settings.workspace_id).update(brand_profile_version=version)
    for post in SocialPost.objects.exclude(source_id=None):
        SocialPostSource.objects.get_or_create(post=post, source_id=post.source_id, defaults={"sort_order": 0, "used_in_generation": True})


def remove_compatibility_snapshots(apps, schema_editor):
    # Schema reversal removes all newly introduced records and fields.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0007_socialpostvariant_metadata"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="brandprofile", name="business_description", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="brandprofile", name="goals", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="brandprofile", name="voice_rules", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="brandprofile", name="example_posts", field=models.JSONField(blank=True, default=list)),
        migrations.CreateModel(
            name="BrandProfileVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version", models.PositiveIntegerField()),
                ("snapshot", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("brand_profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="social_content.brandprofile")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_brand_profile_versions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-version"]},
        ),
        migrations.AddConstraint(model_name="brandprofileversion", constraint=models.UniqueConstraint(fields=("brand_profile", "version"), name="unique_brand_profile_version")),
        migrations.CreateModel(
            name="VoiceRuleSuggestion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("signal_key", models.CharField(max_length=100)),
                ("suggested_rule", models.CharField(max_length=500)),
                ("evidence_count", models.PositiveSmallIntegerField(default=1)),
                ("evidence", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("CONFIRMED", "Confirmed"), ("DISMISSED", "Dismissed")], default="PENDING", max_length=20)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand_profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="voice_rule_suggestions", to="social_content.brandprofile")),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_voice_rule_suggestions", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name="voicerulesuggestion", constraint=models.UniqueConstraint(fields=("brand_profile", "signal_key"), name="unique_brand_voice_signal")),
        migrations.AddField(model_name="contentsource", name="owner", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="owned_social_content_sources", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="contentsource", name="extracted_text", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="contentsource", name="original_filename", field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="contentsource", name="processing_status", field=models.CharField(choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("READY", "Ready"), ("FAILED", "Failed")], db_index=True, default="PENDING", max_length=20)),
        migrations.AddField(model_name="contentsource", name="processing_error", field=models.TextField(blank=True, default="")),
        migrations.AlterField(model_name="contentsource", name="source_type", field=models.CharField(choices=[("TEXT", "Text"), ("URL", "URL"), ("PDF", "PDF"), ("DOCUMENT", "Document"), ("TRANSCRIPT", "Transcript"), ("VOICE_NOTE", "Voice note")], default="TEXT", max_length=20)),
        migrations.AddField(model_name="socialpost", name="brand_profile_version", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="generated_posts", to="social_content.brandprofileversion")),
        migrations.CreateModel(
            name="SocialPostSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("used_in_generation", models.BooleanField(default=False)),
                ("post", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="source_references", to="social_content.socialpost")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="post_references", to="social_content.contentsource")),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
        migrations.AddConstraint(model_name="socialpostsource", constraint=models.UniqueConstraint(fields=("post", "source"), name="unique_social_post_source")),
        migrations.AddConstraint(model_name="socialpostsource", constraint=models.UniqueConstraint(fields=("post", "sort_order"), name="unique_social_post_source_order")),
        migrations.CreateModel(
            name="StoryInterview",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("week_of", models.DateField(db_index=True)),
                ("questions", models.JSONField(default=list)),
                ("answers", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("IN_PROGRESS", "In progress"), ("APPROVED", "Approved")], default="IN_PROGRESS", max_length=20)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_source", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="story_interview", to="social_content.contentsource")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="story_interviews", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="story_interviews", to="prospecting.workspace")),
            ],
        ),
        migrations.AddConstraint(model_name="storyinterview", constraint=models.UniqueConstraint(fields=("workspace", "week_of"), name="unique_story_interview_week")),
        migrations.RunPython(create_compatibility_snapshots, remove_compatibility_snapshots),
    ]
