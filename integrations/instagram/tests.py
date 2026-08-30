import uuid
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from django.db import IntegrityError
from django.contrib.admin.sites import AdminSite

from knowledge_base.models import UserProfile
from prospecting.models import Workspace, get_default_workspace
from integrations.instagram.models import (
    InstagramAccount,
    InstagramOAuthState,
    InstagramWebhookEvent,
    InstagramAutomation,
    InstagramAutomationMedia,
    InstagramAutomationAction,
)
from integrations.instagram.security import (
    encrypt_token,
    decrypt_token,
    generate_oauth_state,
    mask_token,
)
from integrations.instagram.admin import InstagramAccountAdmin


class InstagramSecurityTestCase(TestCase):
    """
    Tests for token encryption, decryption, OAuth state generation, and token masking.
    """

    def test_encrypt_and_decrypt_token(self):
        raw_token = "EAAGtest_instagram_token_123456789_xyz"
        encrypted = encrypt_token(raw_token)

        self.assertNotEqual(raw_token, encrypted)
        self.assertNotIn(raw_token, encrypted)

        decrypted = decrypt_token(encrypted)
        self.assertEqual(decrypted, raw_token)

    def test_encrypt_empty_token_returns_empty(self):
        self.assertEqual(encrypt_token(""), "")
        self.assertEqual(decrypt_token(""), "")

    def test_decrypt_invalid_token_raises_error(self):
        with self.assertRaises(ValueError):
            decrypt_token("corrupted_or_invalid_fernet_token")

    def test_generate_oauth_state(self):
        state1 = generate_oauth_state()
        state2 = generate_oauth_state()

        self.assertIsInstance(state1, str)
        self.assertGreater(len(state1), 20)
        self.assertNotEqual(state1, state2)

    def test_mask_token(self):
        raw_token = "EAAG_secret_access_token_1234_abcd"
        masked = mask_token(raw_token)

        self.assertTrue(masked.startswith("EAAG"))
        self.assertTrue(masked.endswith("abcd"))
        self.assertIn("••••••••", masked)
        self.assertNotIn("secret_access_token", masked)

    def test_mask_token_empty_or_short(self):
        self.assertEqual(mask_token(None), "—")
        self.assertEqual(mask_token(""), "—")
        self.assertEqual(mask_token("short"), "••••••••")


class InstagramAccountModelTestCase(TestCase):
    """
    Tests for InstagramAccount model creation, token encryption, constraints, and relationships.
    """

    def setUp(self):
        self.workspace = get_default_workspace()
        self.user_profile = UserProfile.objects.create(
            username='ig_test_user',
            email='ig_test_user@example.com',
            full_name='IG Test User'
        )

    def test_create_account_and_encrypted_token_methods(self):
        raw_token = "IG_LONG_LIVED_TOKEN_999_SECRET"
        account = InstagramAccount.objects.create(
            workspace=self.workspace,
            created_by=self.user_profile,
            instagram_user_id="17841400123456789",
            username="nomad_coffee_roasters",
            name="Nomad Coffee Roasters",
            status="CONNECTED"
        )
        account.set_access_token(raw_token)
        account.save()

        # Reload from database
        reloaded = InstagramAccount.objects.get(id=account.id)
        self.assertNotEqual(reloaded.encrypted_access_token, raw_token)
        self.assertEqual(reloaded.get_access_token(), raw_token)
        self.assertEqual(str(reloaded), "@nomad_coffee_roasters (CONNECTED)")

    def test_unique_instagram_user_id_constraint(self):
        InstagramAccount.objects.create(
            workspace=self.workspace,
            instagram_user_id="duplicate_ig_id_100",
            username="brand_one"
        )

        with self.assertRaises(IntegrityError):
            InstagramAccount.objects.create(
                workspace=self.workspace,
                instagram_user_id="duplicate_ig_id_100",
                username="brand_two"
            )

    def test_token_expiry_checks(self):
        account = InstagramAccount.objects.create(
            workspace=self.workspace,
            instagram_user_id="ig_expiry_test_101",
            username="brand_expiry"
        )

        # No expiration set
        self.assertFalse(account.is_token_expired)

        # Future expiration
        account.token_expires_at = timezone.now() + timedelta(days=60)
        self.assertFalse(account.is_token_expired)

        # Past expiration
        account.token_expires_at = timezone.now() - timedelta(minutes=5)
        self.assertTrue(account.is_token_expired)

    def test_workspace_relationship_and_cascade(self):
        temp_workspace = Workspace.objects.create(name="Temp Workspace")
        InstagramAccount.objects.create(
            workspace=temp_workspace,
            instagram_user_id="temp_ig_user_102",
            username="temp_brand"
        )

        self.assertEqual(InstagramAccount.objects.filter(workspace=temp_workspace).count(), 1)
        temp_workspace.delete()
        self.assertEqual(InstagramAccount.objects.filter(instagram_user_id="temp_ig_user_102").count(), 0)


class InstagramOAuthStateModelTestCase(TestCase):
    """
    Tests for InstagramOAuthState model, factory helper, expiration, and single-use semantics.
    """

    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = UserProfile.objects.create(
            username='oauth_test_user',
            email='oauth_test@example.com'
        )

    def test_create_state_factory(self):
        state_obj = InstagramOAuthState.create_state(
            workspace=self.workspace,
            user_profile=self.user,
            redirect_uri="http://localhost:8000/callback",
            ttl_minutes=15,
            metadata={"source": "settings_page"}
        )

        self.assertIsNotNone(state_obj.state)
        self.assertFalse(state_obj.is_used)
        self.assertTrue(state_obj.is_valid)
        self.assertEqual(state_obj.metadata["source"], "settings_page")

    def test_state_single_use_transition(self):
        state_obj = InstagramOAuthState.create_state(
            workspace=self.workspace,
            user_profile=self.user
        )
        self.assertTrue(state_obj.is_valid)

        state_obj.mark_as_used()
        self.assertTrue(state_obj.is_used)
        self.assertFalse(state_obj.is_valid)

    def test_expired_state_is_invalid(self):
        state_obj = InstagramOAuthState.objects.create(
            state="expired_state_token_123",
            workspace=self.workspace,
            user_profile=self.user,
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertFalse(state_obj.is_valid)


class InstagramWebhookEventModelTestCase(TestCase):
    """
    Tests for InstagramWebhookEvent model, deduplication constraint, and status handling.
    """

    def setUp(self):
        self.workspace = get_default_workspace()
        self.account = InstagramAccount.objects.create(
            workspace=self.workspace,
            instagram_user_id="ig_webhook_account_103",
            username="webhook_brand"
        )

    def test_create_webhook_event_and_defaults(self):
        event = InstagramWebhookEvent.objects.create(
            instagram_account=self.account,
            event_id="meta_evt_9988776655",
            event_type="comments",
            sender_id="sender_ig_id_44",
            sender_username="interested_customer",
            comment_id="comment_ig_id_112233",
            raw_payload={"entry": [{"id": "123", "changes": [{"field": "comments"}]}]}
        )

        self.assertEqual(event.status, "RECEIVED")
        self.assertEqual(event.retry_count, 0)
        self.assertIn("WebhookEvent comments [RECEIVED]", str(event))

    def test_event_id_unique_deduplication(self):
        InstagramWebhookEvent.objects.create(
            instagram_account=self.account,
            event_id="dedup_event_id_001",
            event_type="comments"
        )

        with self.assertRaises(IntegrityError):
            InstagramWebhookEvent.objects.create(
                instagram_account=self.account,
                event_id="dedup_event_id_001",
                event_type="comments"
            )


class InstagramAutomationModelTestCase(TestCase):
    """
    Tests for InstagramAutomation and InstagramAutomationMedia models.
    """

    def setUp(self):
        self.workspace = get_default_workspace()
        self.account = InstagramAccount.objects.create(
            workspace=self.workspace,
            instagram_user_id="ig_auto_account_104",
            username="automation_brand"
        )

    def test_create_keyword_automation(self):
        automation = InstagramAutomation.objects.create(
            workspace=self.workspace,
            instagram_account=self.account,
            name="Lead Gen Keyword Comment Reply",
            trigger_type="KEYWORD_COMMENT",
            target_media_type="SELECTED_POSTS",
            keywords=["price", "quote", "send details"],
            match_type="CONTAINS",
            public_reply_enabled=True,
            public_reply_templates=["Sent you a DM! Check your inbox 🚀", "DM on its way! 📩"],
            private_reply_enabled=True,
            private_reply_message="Hi! Thanks for reaching out. Here are our pricing details: https://example.com/pricing",
            priority=10
        )

        self.assertTrue(automation.is_active)
        self.assertEqual(len(automation.keywords), 3)
        self.assertIn("Lead Gen Keyword Comment Reply", str(automation))

    def test_automation_media_junction_and_uniqueness(self):
        automation = InstagramAutomation.objects.create(
            workspace=self.workspace,
            instagram_account=self.account,
            name="Reel Promo Automation"
        )

        media = InstagramAutomationMedia.objects.create(
            automation=automation,
            instagram_media_id="ig_media_post_7788",
            permalink="https://instagram.com/p/C_123456/"
        )
        self.assertIn("ig_media_post_7788", str(media))

        # Test unique together constraint
        with self.assertRaises(IntegrityError):
            InstagramAutomationMedia.objects.create(
                automation=automation,
                instagram_media_id="ig_media_post_7788"
            )


class InstagramAutomationActionModelTestCase(TestCase):
    """
    Tests for InstagramAutomationAction idempotency constraint and execution tracking.
    """

    def setUp(self):
        self.workspace = get_default_workspace()
        self.account = InstagramAccount.objects.create(
            workspace=self.workspace,
            instagram_user_id="ig_action_account_105",
            username="action_brand"
        )

    def test_idempotency_key_uniqueness(self):
        idempotency_key = "comment_999888:PRIVATE_REPLY"

        action1 = InstagramAutomationAction.objects.create(
            instagram_account=self.account,
            action_type="PRIVATE_REPLY",
            recipient_id="recipient_user_55",
            recipient_username="john_doe",
            comment_id="comment_999888",
            idempotency_key=idempotency_key,
            status="EXECUTED"
        )
        self.assertEqual(action1.status, "EXECUTED")

        # Duplicate action with same idempotency key must fail
        with self.assertRaises(IntegrityError):
            InstagramAutomationAction.objects.create(
                instagram_account=self.account,
                action_type="PRIVATE_REPLY",
                recipient_id="recipient_user_55",
                recipient_username="john_doe",
                comment_id="comment_999888",
                idempotency_key=idempotency_key,
                status="PENDING"
            )


class InstagramAdminSecurityTestCase(TestCase):
    """
    Tests ensuring Django Admin representation of InstagramAccount masks encrypted secrets.
    """

    def setUp(self):
        self.workspace = get_default_workspace()
        self.account = InstagramAccount.objects.create(
            workspace=self.workspace,
            instagram_user_id="ig_admin_test_106",
            username="admin_test_brand"
        )
        self.account.set_access_token("SUPER_SECRET_IG_OAUTH_TOKEN_VALUE_XYZ")
        self.account.save()

    def test_admin_masked_token_method(self):
        admin_instance = InstagramAccountAdmin(InstagramAccount, AdminSite())
        masked_val = admin_instance.masked_access_token(self.account)

        self.assertNotIn("SUPER_SECRET_IG_OAUTH_TOKEN_VALUE_XYZ", masked_val)
        self.assertIn("••••••••", masked_val)
