import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework import status
from django.utils import timezone

from prospecting.models import WorkerRuntimeState
from prospecting.tasks import (
    send_worker_wake_ping,
    wake_all_remote_workers,
    worker_keepalive_task,
    web_worker_keepalive_task,
)
from scripts.worker_health_server import WorkerHealthRequestHandler


class WorkerRuntimeStateModelTests(TestCase):
    databases = {'default'}

    def test_singleton_get_and_set_state(self):
        # 1. Initially disabled
        state = WorkerRuntimeState.get_state()
        self.assertFalse(state.enabled)
        self.assertEqual(state.id, 1)

        # 2. Enable
        state = WorkerRuntimeState.set_enabled(True)
        self.assertTrue(state.enabled)
        self.assertIsNotNone(state.started_at)
        self.assertEqual(WorkerRuntimeState.objects.count(), 1)

        # 3. Disable
        state = WorkerRuntimeState.set_enabled(False)
        self.assertFalse(state.enabled)
        self.assertIsNotNone(state.stopped_at)
        self.assertEqual(WorkerRuntimeState.objects.count(), 1)

    def test_repeated_save_maintains_singleton(self):
        s1 = WorkerRuntimeState.objects.create(id=1, enabled=True)
        s2 = WorkerRuntimeState(id=99, enabled=False)
        s2.save()
        self.assertEqual(WorkerRuntimeState.objects.count(), 1)
        self.assertEqual(WorkerRuntimeState.get_state().id, 1)


class WorkerHealthServerRequestHandlerTests(TestCase):
    @patch.dict("os.environ", {"WORKER_WAKE_TOKEN": "secret-test-token"})
    def test_auth_token_verification(self):
        handler = WorkerHealthRequestHandler.__new__(WorkerHealthRequestHandler)
        
        # Valid header
        handler.headers = {"X-Worker-Wake-Token": "secret-test-token"}
        self.assertTrue(handler._is_authenticated())

        # Valid Bearer authorization header
        handler.headers = {"Authorization": "Bearer secret-test-token"}
        self.assertTrue(handler._is_authenticated())

        # Valid query params
        handler.headers = {}
        self.assertTrue(handler._is_authenticated(query_params={"token": ["secret-test-token"]}))

        # Valid JSON body
        self.assertTrue(handler._is_authenticated(body_json={"token": "secret-test-token"}))

        # Missing / invalid token
        handler.headers = {"X-Worker-Wake-Token": "wrong-token"}
        self.assertFalse(handler._is_authenticated())

        handler.headers = {}
        self.assertFalse(handler._is_authenticated(query_params={"token": ["wrong-token"]}))
        self.assertFalse(handler._is_authenticated(body_json={"token": "wrong-token"}))
        self.assertFalse(handler._is_authenticated())

    @patch.dict("os.environ", {"WORKER_WAKE_TOKEN": ""})
    def test_auth_token_dev_mode_allowed_when_token_empty(self):
        handler = WorkerHealthRequestHandler.__new__(WorkerHealthRequestHandler)
        handler.headers = {}
        self.assertTrue(handler._is_authenticated())


@override_settings(
    WORKER_1_URL="http://worker-1.test.local",
    WORKER_2_URL="http://worker-2.test.local",
    WORKER_WAKE_TOKEN="test-token"
)
class WorkerTasksKeepaliveTests(TestCase):
    databases = {'default'}

    def setUp(self):
        WorkerRuntimeState.objects.all().delete()

    @patch("requests.post")
    def test_send_worker_wake_ping_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_resp

        res = send_worker_wake_ping("http://worker-1.test.local")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["status_code"], 200)
        self.assertEqual(res["url"], "http://worker-1.test.local/wake")

        mock_post.assert_called_once_with(
            "http://worker-1.test.local/wake",
            json={"token": "test-token"},
            headers={
                "Content-Type": "application/json",
                "X-Worker-Wake-Token": "test-token",
                "Authorization": "Bearer test-token"
            },
            timeout=5.0
        )

    @patch("requests.post")
    def test_send_worker_wake_ping_network_failure(self, mock_post):
        import requests
        mock_post.side_effect = requests.RequestException("Connection refused")

        res = send_worker_wake_ping("http://worker-1.test.local")
        self.assertEqual(res["status"], "error")
        self.assertIn("Connection refused", res["error"])

    @patch("requests.post")
    def test_worker_keepalive_skipped_when_disabled(self, mock_post):
        WorkerRuntimeState.set_enabled(False)

        res = worker_keepalive_task(target_worker_url="http://worker-2.test.local")
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "disabled")
        mock_post.assert_not_called()

    @patch("prospecting.tasks.worker_keepalive_task.apply_async")
    @patch("requests.post")
    def test_worker_keepalive_executes_and_reschedules_when_enabled(self, mock_post, mock_apply_async):
        WorkerRuntimeState.set_enabled(True)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_resp

        res = worker_keepalive_task(target_worker_url="http://worker-2.test.local")
        self.assertEqual(res["status"], "pinged")
        self.assertEqual(res["target"], "http://worker-2.test.local")
        self.assertGreaterEqual(res["next_countdown_seconds"], 300)
        self.assertLessEqual(res["next_countdown_seconds"], 540)

        mock_post.assert_called_once()
        mock_apply_async.assert_called_once()

    @patch("requests.post")
    def test_web_worker_keepalive_skipped_when_disabled(self, mock_post):
        WorkerRuntimeState.set_enabled(False)

        res = web_worker_keepalive_task()
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "disabled")
        mock_post.assert_not_called()

    @patch("prospecting.tasks.web_worker_keepalive_task.apply_async")
    @patch("requests.post")
    def test_web_worker_keepalive_executes_and_reschedules_when_enabled(self, mock_post, mock_apply_async):
        WorkerRuntimeState.set_enabled(True)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_resp

        res = web_worker_keepalive_task()
        self.assertEqual(res["status"], "pinged")
        self.assertIn("worker_1", res["wake_results"])
        self.assertIn("worker_2", res["wake_results"])
        self.assertGreaterEqual(res["next_countdown_seconds"], 480)
        self.assertLessEqual(res["next_countdown_seconds"], 540)

        self.assertEqual(mock_post.call_count, 2)
        mock_apply_async.assert_called_once()


@override_settings(
    WORKER_1_URL="http://worker-1.test.local",
    WORKER_2_URL="http://worker-2.test.local",
    WORKER_WAKE_TOKEN="test-token"
)
class WorkerControlAPITests(APITestCase):
    databases = {'default'}

    def setUp(self):
        WorkerRuntimeState.objects.all().delete()

    @patch("prospecting.tasks.worker_keepalive_task.delay")
    @patch("prospecting.tasks.web_worker_keepalive_task.delay")
    @patch("requests.post")
    def test_start_endpoint_success(self, mock_post, mock_web_delay, mock_worker_delay):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"status": "ok", "message": "Worker awakened successfully"}
        mock_post.return_value = mock_resp

        response = self.client.post("/api/v3/prospecting/workers/start/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "started")
        self.assertTrue(response.data["enabled"])
        self.assertEqual(response.data["worker_1_wake"]["status"], "ok")
        self.assertEqual(response.data["worker_2_wake"]["status"], "ok")

        # Verify DB state
        state = WorkerRuntimeState.get_state()
        self.assertTrue(state.enabled)

        # Verify tasks triggered
        mock_web_delay.assert_called_once()
        self.assertEqual(mock_worker_delay.call_count, 2)

    @patch("prospecting.tasks.worker_keepalive_task.delay")
    @patch("prospecting.tasks.web_worker_keepalive_task.delay")
    @patch("requests.post")
    def test_start_endpoint_tolerates_one_worker_down(self, mock_post, mock_web_delay, mock_worker_delay):
        import requests
        # worker-1 succeeds, worker-2 times out
        def side_effect_post(url, **kwargs):
            if "worker-1" in url:
                m = MagicMock()
                m.status_code = 200
                m.headers = {"content-type": "application/json"}
                m.json.return_value = {"status": "ok"}
                return m
            else:
                raise requests.RequestException("Worker 2 sleeping or connection timed out")

        mock_post.side_effect = side_effect_post

        response = self.client.post("/api/v3/prospecting/workers/start/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "started_with_warnings")
        self.assertTrue(response.data["enabled"])
        self.assertEqual(response.data["worker_1_wake"]["status"], "ok")
        self.assertEqual(response.data["worker_2_wake"]["status"], "error")

        # DB state is still enabled
        state = WorkerRuntimeState.get_state()
        self.assertTrue(state.enabled)

    def test_stop_endpoint(self):
        WorkerRuntimeState.set_enabled(True)

        response = self.client.post("/api/v3/prospecting/workers/stop/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "stopped")
        self.assertFalse(response.data["enabled"])

        # Verify DB state
        state = WorkerRuntimeState.get_state()
        self.assertFalse(state.enabled)

    def test_status_endpoint(self):
        WorkerRuntimeState.set_enabled(True)
        response = self.client.get("/api/v3/prospecting/workers/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["enabled"])

        WorkerRuntimeState.set_enabled(False)
        response = self.client.get("/api/v3/prospecting/workers/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["enabled"])
