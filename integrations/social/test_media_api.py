import shutil
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.social.models import (
    ConnectionState,
    MediaAsset,
    MediaAssetSource,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialProvider,
)
from prospecting.models import Workspace, WorkspaceMembership


def png_file(name="image.png", width=1200, height=1200, content_type="image/png"):
    body = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"placeholder-crc"
    )
    return SimpleUploadedFile(name, body, content_type=content_type)


def jpeg_file(name="image.jpg", width=1200, height=1200):
    body = (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    )
    return SimpleUploadedFile(name, body, content_type="image/jpeg")


def pdf_file(name="document.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.7\nminimal test document", content_type="application/pdf")


def mp4_file(name="video.mp4", width=1280, height=720, duration_ms=30_000):
    body = bytearray(80 * 1024)
    body[0:4] = struct.pack(">I", 20)
    body[4:8] = b"ftyp"
    body[8:12] = b"mp42"
    body[20:24] = b"mvhd"
    body[36:40] = struct.pack(">I", 1000)
    body[40:44] = struct.pack(">I", duration_ms)
    body[70:74] = b"tkhd"
    body[146:150] = struct.pack(">I", width << 16)
    body[150:154] = struct.pack(">I", height << 16)
    return SimpleUploadedFile(name, bytes(body), content_type="video/mp4")


@override_settings(
    CONTENT_AUTOMATION_DEV_BOOTSTRAP=False,
    SOCIAL_PUBLISHER_DEFAULT="UPLOAD_POST",
    PUBLIC_BACKEND_URL="https://app.example.test",
)
class SocialMediaApiTests(TestCase):
    def setUp(self):
        self.storage_root = Path(tempfile.mkdtemp(prefix="nomad-social-media-test-"))
        self.storage_override = override_settings(STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            "social_private": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {
                    "location": str(self.storage_root / "private"),
                },
            },
            "social_publish": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {
                    "location": str(self.storage_root / "publish"),
                    "base_url": "/owned-media/",
                },
            },
        })
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(shutil.rmtree, self.storage_root, True)

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="media-user")
        self.other_user = user_model.objects.create_user(username="other-media-user")
        self.workspace = Workspace.objects.create(name="Media workspace")
        self.other_workspace = Workspace.objects.create(name="Other media workspace")
        WorkspaceMembership.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=WorkspaceMembership.OWNER,
            is_active=True,
        )
        WorkspaceMembership.objects.create(
            workspace=self.other_workspace,
            user=self.other_user,
            role=WorkspaceMembership.OWNER,
            is_active=True,
        )
        self.variant = self.create_variant(self.workspace, SocialNetwork.LINKEDIN)
        self.other_variant = self.create_variant(self.other_workspace, SocialNetwork.LINKEDIN)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def create_variant(workspace, network):
        post = SocialPost.objects.create(
            workspace=workspace,
            idea_title=f"{network} media post",
            state=SocialPostState.DRAFT,
        )
        return SocialPostVariant.objects.create(
            post=post,
            network=network,
            copy="Media post",
            scheduled_for=timezone.now(),
            status=SocialPostState.DRAFT,
        )

    def list_url(self, variant=None):
        return reverse("social-media-list", args=[(variant or self.variant).id])

    def upload(self, file, variant=None, **data):
        return self.client.post(
            self.list_url(variant),
            {"file": file, **data},
            format="multipart",
        )

    def test_upload_keeps_private_original_and_stable_publish_derivative(self):
        response = self.upload(png_file(), alt_text="Accessible description")

        self.assertEqual(response.status_code, 201)
        asset = MediaAsset.objects.get(pk=response.data["id"])
        self.assertEqual(asset.variant, self.variant)
        self.assertEqual(asset.asset_type, "IMAGE")
        self.assertEqual(asset.alt_text, "Accessible description")
        self.assertTrue((self.storage_root / "private" / asset.original_storage_key).exists())
        self.assertTrue((self.storage_root / "publish" / asset.publish_storage_key).exists())
        self.assertNotEqual(asset.original_storage_key, asset.publish_storage_key)
        self.assertTrue(response.data["publish_url"].startswith("/owned-media/"))
        self.assertNotIn("original_storage_key", response.data)
        self.assertFalse(any(field.name == "binary" for field in MediaAsset._meta.fields))

    def test_multiple_images_can_be_reordered_and_deleted(self):
        first = self.upload(png_file("first.png")).data
        second = self.upload(png_file("second.png", width=1000)).data

        reordered = self.client.post(
            reverse("social-media-reorder", args=[self.variant.id]),
            {"asset_ids": [second["id"], first["id"]]},
            format="json",
        )
        deleted = self.client.delete(
            reverse("social-media-detail", args=[self.variant.id, second["id"]]),
        )

        self.assertEqual(reordered.status_code, 200)
        self.assertEqual([item["id"] for item in reordered.data], [second["id"], first["id"]])
        self.assertEqual(deleted.status_code, 204)
        remaining = self.variant.media_assets.get()
        self.assertEqual(remaining.sort_order, 0)

    def test_alt_text_edit_revokes_approval_and_creates_a_version(self):
        asset_id = self.upload(png_file()).data["id"]
        self.variant.refresh_from_db()
        self.variant.status = SocialPostState.APPROVED
        self.variant.save(update_fields=["status"])
        before = self.variant.versions.count()

        response = self.client.patch(
            reverse("social-media-detail", args=[self.variant.id, asset_id]),
            {"alt_text": "Updated description"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.status, SocialPostState.NEEDS_REVIEW)
        self.assertEqual(self.variant.versions.count(), before + 1)
        report = self.variant.versions.latest("version").quality_check
        alt_text = next(item for item in report["deterministic"] if item["key"] == "missing_alt_text")
        self.assertEqual(alt_text["status"], "PASS")

    def test_invalid_mime_and_arbitrary_server_url_are_rejected(self):
        mismatched = self.upload(png_file(content_type="image/jpeg"))
        remote_url = self.client.post(
            self.list_url(),
            {"storage_url": "http://169.254.169.254/latest/meta-data"},
            format="json",
        )

        self.assertEqual(mismatched.status_code, 400)
        self.assertIn("declared content type", str(mismatched.data))
        self.assertEqual(remote_url.status_code, 400)
        self.assertIn("not accepted", str(remote_url.data))
        self.assertEqual(MediaAsset.objects.count(), 0)

    def test_invalid_dimensions_size_and_duration_are_rejected(self):
        zero_dimension = self.upload(png_file(width=0, height=1200))
        oversized_body = png_file().read() + (b"x" * (8 * 1024 * 1024))
        oversized = self.upload(SimpleUploadedFile(
            "oversized.png",
            oversized_body,
            content_type="image/png",
        ))
        x_variant = self.create_variant(self.workspace, SocialNetwork.X)
        too_long = self.upload(mp4_file(duration_ms=(20 * 60 * 1000) + 1), x_variant)

        self.assertEqual(zero_dimension.status_code, 400)
        self.assertIn("dimensions", str(zero_dimension.data))
        self.assertEqual(oversized.status_code, 400)
        self.assertIn("no larger", str(oversized.data))
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("too long", str(too_long.data))

    def test_images_cannot_be_mixed_with_video_or_document(self):
        self.assertEqual(self.upload(png_file()).status_code, 201)

        video = self.upload(mp4_file())
        document = self.upload(pdf_file())

        self.assertEqual(video.status_code, 400)
        self.assertEqual(document.status_code, 400)
        self.assertIn("cannot be mixed", str(video.data))
        self.assertIn("cannot be mixed", str(document.data))

    def test_one_video_or_one_document_is_supported_by_the_domain(self):
        video_variant = self.create_variant(self.workspace, SocialNetwork.X)
        document_variant = self.create_variant(self.workspace, SocialNetwork.LINKEDIN)

        video = self.upload(mp4_file(), video_variant)
        document = self.upload(pdf_file(), document_variant)

        self.assertEqual(video.status_code, 201, video.data)
        self.assertEqual(video.data["asset_type"], "VIDEO")
        self.assertEqual(video.data["duration_ms"], 30_000)
        self.assertEqual(document.status_code, 201, document.data)
        self.assertEqual(document.data["asset_type"], "DOCUMENT")

    def test_platform_specific_image_count_and_type_limits(self):
        x_variant = self.create_variant(self.workspace, SocialNetwork.X)
        for index in range(4):
            self.assertEqual(self.upload(png_file(f"x-{index}.png"), x_variant).status_code, 201)
        over_limit = self.upload(png_file("x-over.png"), x_variant)
        instagram_variant = self.create_variant(self.workspace, SocialNetwork.INSTAGRAM)
        invalid_instagram_type = self.upload(png_file("instagram.png"), instagram_variant)
        valid_instagram_type = self.upload(jpeg_file("instagram.jpg"), instagram_variant)

        self.assertEqual(over_limit.status_code, 400)
        self.assertIn("at most 4", str(over_limit.data))
        self.assertEqual(invalid_instagram_type.status_code, 400, invalid_instagram_type.data)
        self.assertEqual(valid_instagram_type.status_code, 201)

    def test_provider_capabilities_are_checked_again_before_approval(self):
        document_variant = self.create_variant(self.workspace, SocialNetwork.LINKEDIN)
        document_upload = self.upload(pdf_file(), document_variant)
        self.assertEqual(document_upload.status_code, 201, document_upload.data)

        unsupported = self.client.post(
            reverse("social-variant-approve", args=[document_variant.id]),
            {},
            format="json",
        )
        self.assertEqual(unsupported.status_code, 400)
        self.assertIn("does not support documents", str(unsupported.data))

        self.variant.connection = SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.UPLOAD_POST,
            provider_profile_id="media-profile",
            provider_account_id="media-account",
            display_name="Media Page",
            status=ConnectionState.CONNECTED,
        )
        self.variant.save(update_fields=["connection"])
        self.assertEqual(self.upload(png_file()).status_code, 201)
        approved = self.client.post(
            reverse("social-variant-approve", args=[self.variant.id]),
            {},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.data["status"], "APPROVED")

    def test_media_queries_and_mutations_are_tenant_isolated(self):
        other_asset = MediaAsset.objects.create(
            workspace=self.other_workspace,
            variant=self.other_variant,
            asset_type="IMAGE",
            content_type="image/png",
            byte_size=100,
            width=10,
            height=10,
        )

        listing = self.client.get(self.list_url(self.other_variant))
        deletion = self.client.delete(
            reverse("social-media-detail", args=[self.other_variant.id, other_asset.id]),
        )
        forbidden_workspace = self.client.get(
            self.list_url(),
            HTTP_X_WORKSPACE_ID=str(self.other_workspace.id),
        )

        self.assertEqual(listing.status_code, 404)
        self.assertEqual(deletion.status_code, 404)
        self.assertEqual(forbidden_workspace.status_code, 403)
        self.assertTrue(MediaAsset.objects.filter(pk=other_asset.pk).exists())

        self.client.force_authenticate(user=None)
        unauthenticated = self.client.get(self.list_url())
        self.assertEqual(unauthenticated.status_code, 401)

    def test_ai_regeneration_replaces_an_owned_image(self):
        old_id = self.upload(png_file()).data["id"]
        generated = png_file("generated.png", width=1080, height=1350).read()
        with patch(
            "integrations.social.views.LinkedInImageGenerator.generate",
            return_value=("", {"content_type": "image/png"}, generated),
        ):
            response = self.client.post(
                reverse("social-media-regenerate", args=[self.variant.id]),
                {"prompt": "A calm editorial visual", "asset_id": old_id, "alt_text": "Generated visual"},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["source"], MediaAssetSource.AI)
        self.assertEqual(response.data["alt_text"], "Generated visual")
        self.assertFalse(MediaAsset.objects.filter(pk=old_id).exists())
        self.assertEqual(self.variant.media_assets.count(), 1)
