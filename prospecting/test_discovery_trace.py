import json
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from llm.tools.context import ToolContext
from llm.tools.executor import ToolExecutor
from llm.tools.result import ToolResult
from prospecting.discovery.tracing import DiscoveryTraceRecorder, load_discovery_trace
from prospecting.models import DiscoveryRun
from prospecting.views import get_default_user


class _TraceableSearchTool:
    name = "search_web"
    input_schema = None
    provider = "test-search"
    read_only = True
    requires_approval = False

    def execute(self, query, limit=20, **kwargs):
        return ToolResult(
            success=True,
            data={
                "results": [
                    {
                        "title": "Example Courier",
                        "url": "https://example.com",
                        "snippet": "Courier services in Leeds",
                    }
                ]
            },
            provider="test-search",
            tool_name=self.name,
        )


class _Registry:
    def __init__(self, tool):
        self.tool = tool

    def get_tool(self, tool_name):
        if tool_name != self.tool.name:
            raise ValueError(tool_name)
        return self.tool


class DiscoveryTraceTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(
            DISCOVERY_TRACE_DIR=Path(self.temp_dir.name),
            DISCOVERY_TRACE_ENABLED=True,
            DISCOVERY_TRACE_STRING_LIMIT=200,
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.temp_dir.cleanup()

    def test_recorder_creates_json_and_self_contained_html(self):
        recorder = DiscoveryTraceRecorder("trace-run-1")
        recorder.initialize({
            "keyword": "Courier operators",
            "location": "Leeds",
            "api_key": "must-not-leak",
        })
        recorder.event(
            "llm_input_interpretation",
            "Resolved search plan",
            actor="llm:test",
            input_data={"prompt": "Find courier businesses"},
            output_data={"search_queries": ["courier service"]},
            metadata={"parsed": True},
        )
        recorder.event(
            "completion",
            "Discovery completed",
            actor="workflow",
            output_data={"leads_found": 1},
        )

        trace = json.loads(recorder.json_path.read_text(encoding="utf-8"))
        viewer = recorder.html_path.read_text(encoding="utf-8")

        self.assertEqual(trace["status"], "completed")
        self.assertEqual(trace["input"]["api_key"], "[REDACTED]")
        self.assertEqual(len(trace["events"]), 3)
        self.assertIn("Discovery execution trace", viewer)
        self.assertIn('id="trace-data"', viewer)
        self.assertNotIn("must-not-leak", viewer)

    def test_tool_executor_records_search_request_and_results(self):
        recorder = DiscoveryTraceRecorder("trace-run-2")
        recorder.initialize({"keyword": "courier", "location": "Leeds"})
        executor = ToolExecutor(_Registry(_TraceableSearchTool()))

        result = executor.execute(
            "search_web",
            {"query": '"courier" "Leeds"', "limit": 20},
            context=ToolContext(run_id="trace-run-2", source="test"),
        )

        self.assertTrue(result.success)
        trace = load_discovery_trace("trace-run-2")
        tool_event = trace["events"][-1]
        self.assertEqual(tool_event["stage"], "tool_search")
        self.assertEqual(tool_event["input"]["query"], '"courier" "Leeds"')
        self.assertEqual(tool_event["metadata"]["result_count"], 1)
        self.assertEqual(trace["quality"]["metrics"]["raw_results"], 1)

    def test_trace_api_serves_viewer_and_raw_data_for_visible_run(self):
        run = DiscoveryRun.objects.create(
            user_profile=get_default_user(),
            keyword="Courier operators",
            location="Leeds",
            status="pending",
        )
        DiscoveryTraceRecorder(str(run.id)).initialize({
            "keyword": run.keyword,
            "location": run.location,
        })
        url = reverse("discovery-run-trace", kwargs={"pk": run.id})

        viewer_response = self.client.get(url)
        raw_response = self.client.get(url, {"raw": "true"})

        self.assertEqual(viewer_response.status_code, 200)
        self.assertEqual(viewer_response["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(
            b"Discovery execution trace",
            b"".join(viewer_response.streaming_content),
        )
        self.assertEqual(raw_response.status_code, 200)
        self.assertEqual(raw_response.json()["run_id"], str(run.id))

