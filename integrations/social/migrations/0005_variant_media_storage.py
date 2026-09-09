import django.db.models.deletion
from django.db import migrations, models


def assign_assets_to_variants(apps, schema_editor):
    Asset = apps.get_model("social_content", "MediaAsset")
    Variant = apps.get_model("social_content", "SocialPostVariant")
    for asset in Asset.objects.all().iterator():
        variant = (
            Variant.objects.filter(post_id=asset.post_id, network="LINKEDIN").first()
            or Variant.objects.filter(post_id=asset.post_id).first()
        )
        if variant is None:
            raise RuntimeError(f"Media asset {asset.pk} has no platform variant.")
        Asset.objects.filter(pk=asset.pk).update(
            variant_id=variant.id,
            source="LEGACY",
            original_filename="",
            content_type=str((asset.metadata or {}).get("content_type") or ""),
        )
    for variant_id in Asset.objects.values_list("variant_id", flat=True).distinct():
        asset_ids = list(
            Asset.objects.filter(variant_id=variant_id)
            .order_by("sort_order", "created_at")
            .values_list("id", flat=True)
        )
        for index, asset_id in enumerate(asset_ids):
            Asset.objects.filter(pk=asset_id).update(sort_order=index)


def restore_post_ownership(apps, schema_editor):
    Asset = apps.get_model("social_content", "MediaAsset")
    for asset in Asset.objects.select_related("variant").all().iterator():
        Asset.objects.filter(pk=asset.pk).update(post_id=asset.variant.post_id)


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0004_publish_job_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="variant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="media_assets",
                to="social_content.socialpostvariant",
            ),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="source",
            field=models.CharField(
                choices=[("UPLOAD", "User upload"), ("AI", "AI generated"), ("LEGACY", "Legacy import")],
                default="UPLOAD",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="original_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="original_storage_key",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="publish_storage_key",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="content_type",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="byte_size",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="checksum_sha256",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="width",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="height",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="duration_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="mediaasset",
            name="storage_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Legacy compatibility only. New assets use owned storage keys.",
                max_length=2000,
            ),
        ),
        migrations.AlterField(
            model_name="mediaasset",
            name="post",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                help_text="Nullable compatibility pointer; variant is the authoritative owner.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="legacy_media_assets",
                to="social_content.socialpost",
            ),
        ),
        migrations.RunPython(assign_assets_to_variants, restore_post_ownership),
        migrations.AlterField(
            model_name="mediaasset",
            name="variant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="media_assets",
                to="social_content.socialpostvariant",
            ),
        ),
        migrations.AddConstraint(
            model_name="mediaasset",
            constraint=models.UniqueConstraint(
                fields=("variant", "sort_order"),
                name="unique_social_variant_media_order",
            ),
        ),
        migrations.AddIndex(
            model_name="mediaasset",
            index=models.Index(
                fields=["variant", "sort_order"],
                name="social_asset_variant_order_idx",
            ),
        ),
    ]
