from django.test import TestCase
from unittest.mock import MagicMock, patch
import requests
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

from llm.tools.base import BaseTool
from llm.tools.registry import ToolRegistry
from llm.tools.executor import ToolExecutor
from llm.tools.context import ToolContext
from llm.tools.result import ToolResult
from llm.tools.implementations.discovery_tools import (
    SearchCompaniesTool,
    SearchWebTool,
    ExtractContactDataTool
)
from llm.providers.duckduckgo import DuckDuckGoSearchProvider
from llm.providers.registry import provider_registry
from llm.providers.base import CompanyDiscoveryProvider, CompanyCandidate
from llm.gemini_api import GeminiAPIProvider

# =====================================================================
# Dummy tools and models for testing platform behaviors
# =====================================================================

class DummyInput(BaseModel):
    value: str = Field(..., min_length=3)

class DummyTool(BaseTool):
    @property
    def name(self) -> str:
        return "dummy_tool"

    @property
    def description(self) -> str:
        return "A dummy tool for testing."

    @property
    def parameters(self) -> Dict[str, Any]:
        return DummyInput.model_json_schema()

    @property
    def input_schema(self) -> type[BaseModel]:
        return DummyInput

    def execute(self, value: str, **kwargs) -> str:
        return f"Echo: {value}"


class DummyWriteTool(BaseTool):
    @property
    def name(self) -> str:
        return "GitHubWriteFileTool"

    @property
    def read_only(self) -> bool:
        return False

    @property
    def description(self) -> str:
        return "Simulated writing tool."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {}

    def execute(self, **kwargs) -> str:
        return "Written"


class DummyCompanyProvider(CompanyDiscoveryProvider):
    def search_companies(self, query: str, geography: Optional[str] = None, limit: int = 20) -> list:
        return [
            CompanyCandidate(
                name="Test Biz",
                website="https://testbiz.co.uk",
                source="test",
                address="123 Road, UK"
            )
        ]


# =====================================================================
# Tool Platform Unit Tests
# =====================================================================

class ToolPlatformTestCase(TestCase):
    databases = {'default', 'telemetry'}
    def setUp(self):
        self.registry = ToolRegistry()
        self.executor = ToolExecutor(self.registry)

    def test_registry_registration_and_collision(self):
        tool = DummyTool()
        self.registry.register(tool)
        self.assertEqual(self.registry.get_tool("dummy_tool"), tool)

        # Expect ValueError on duplicate registration
        with self.assertRaises(ValueError):
            self.registry.register(tool)

    def test_executor_input_validation(self):
        self.registry.register(DummyTool())

        # 1. Valid Input
        res = self.executor.execute("dummy_tool", {"value": "hello"})
        self.assertTrue(res.success)
        self.assertEqual(res.data, "Echo: hello")

        # 2. Invalid Input (fails validation constraint of min_length=3)
        res_fail = self.executor.execute("dummy_tool", {"value": "hi"})
        self.assertFalse(res_fail.success)
        self.assertEqual(res_fail.error.code, "VALIDATION_FAILED")

    @patch("llm.providers.duckduckgo.requests.post")
    def test_duckduckgo_parses_html_results(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = '''
            <div class="result">
              <h2><a class="result__a" href="https://example.com">Example Pest Control</a></h2>
              <div class="result__snippet">Local pest control company</div>
            </div>
        '''
        results = DuckDuckGoSearchProvider().search_web("pest control Manchester", limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.com")
        self.assertEqual(results[0]["snippet"], "Local pest control company")

    def test_read_only_policy_blocks_write_tools(self):
        self.registry.register(DummyWriteTool())
        
        # Enable read-only in context
        context = ToolContext(metadata={"read_only": True})
        res = self.executor.execute("GitHubWriteFileTool", {}, context=context)
        
        self.assertFalse(res.success)
        self.assertEqual(res.error.code, "POLICY_BLOCKED")
        self.assertIn("blocked", res.error.message)

    def test_approval_gate_policy(self):
        self.registry.register(DummyTool())

        # 1. Approval Required but not approved -> Expect Block
        context_block = ToolContext(metadata={"require_approval": True, "is_approved": False})
        res_block = self.executor.execute("dummy_tool", {"value": "hello"}, context=context_block)
        self.assertFalse(res_block.success)
        self.assertEqual(res_block.error.code, "POLICY_BLOCKED")

        # 2. Approval Required and approved -> Expect Success
        context_allow = ToolContext(metadata={"require_approval": True, "is_approved": True})
        res_allow = self.executor.execute("dummy_tool", {"value": "hello"}, context=context_allow)
        self.assertTrue(res_allow.success)

    def test_audit_sanitization(self):
        sanitized = self.executor._sanitize_data({
            "api_key": "secret-12345",
            "normal_field": "hello",
            "nested": {
                "password": "my-password",
                "safe": 100
            }
        })
        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["normal_field"], "hello")
        self.assertEqual(sanitized["nested"]["password"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["safe"], 100)

    def test_exception_mapping_logic(self):
        class BrokenTool(BaseTool):
            @property
            def name(self) -> str:
                return "broken_tool"
            @property
            def description(self) -> str:
                return "Broken"
            @property
            def parameters(self) -> Dict[str, Any]:
                return {}
            def execute(self, **kwargs) -> Any:
                raise TimeoutError("Request timed out after 10s")

        self.registry.register(BrokenTool())
        res = self.executor.execute("broken_tool", {})
        self.assertFalse(res.success)
        self.assertEqual(res.error.code, "TIMEOUT")
        self.assertTrue(res.error.retryable)

    def test_search_companies_tool_with_mock_provider(self):
        provider_registry.register("dummy_prov", DummyCompanyProvider())
        tool = SearchCompaniesTool()
        
        res = tool.execute(query="pest", provider="dummy_prov")
        self.assertTrue(res.success)
        self.assertEqual(len(res.data["companies"]), 1)
        self.assertEqual(res.data["companies"][0]["name"], "Test Biz")

    def test_extract_contact_data_tool(self):
        tool = ExtractContactDataTool()
        text = "Hello! Please email us at contact@testbiz.co.uk or call 0161 9998888. LinkedIn: https://linkedin.com/company/testbiz"
        res = tool.execute(text=text)
        self.assertTrue(res.success)
        self.assertIn("contact@testbiz.co.uk", res.data["emails"])
        self.assertIn("0161 9998888", res.data["phones"])
        self.assertIn("https://linkedin.com/company/testbiz", res.data["linkedin_urls"])

    def test_swagger_endpoints(self):
        # Swagger UI endpoint
        response = self.client.get('/api/schema/swagger-ui/')
        self.assertEqual(response.status_code, 200)
        self.assertIn("swagger-ui", response.content.decode().lower())

        # OpenAPI schema endpoint
        response = self.client.get('/api/schema/')
        self.assertEqual(response.status_code, 200)


class GeminiReliabilityTestCase(TestCase):
    databases = {'default', 'telemetry'}
    @patch("llm.gemini_api.requests.post")
    def test_gemini_error_preserves_http_status_and_message(self, mock_post):
        response = MagicMock()
        response.status_code = 503
        response.headers = {"Retry-After": "4"}
        response.json.return_value = {
            "error": {"message": "The model is temporarily overloaded"}
        }
        response.raise_for_status.side_effect = requests.HTTPError(
            "503 Server Error", response=response
        )
        mock_post.return_value = response

        result = GeminiAPIProvider(api_key="test-key").generate("hello")

        self.assertEqual(result["type"], "error")
        self.assertEqual(result["status_code"], 503)
        self.assertEqual(result["retry_after"], "4")
        self.assertIn("temporarily overloaded", result["text"])

    @patch("time.sleep")
    def test_router_retries_503_three_times(self, mock_sleep):
        from llm.router import IntelligentRouter

        adapter = MagicMock()
        adapter.generate.return_value = {
            "type": "error",
            "text": "Service unavailable",
            "status_code": 503
        }
        router = IntelligentRouter.__new__(IntelligentRouter)

        result = router._generate_with_logging("gemini-flash", adapter, "hello", "", None)

        self.assertEqual(result["status_code"], 503)
        self.assertEqual(adapter.generate.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)


class PromptRegistryTestCase(TestCase):
    databases = {'default', 'telemetry'}
    def test_prompt_creation_and_active_uniqueness(self):
        from llm.models import LLMPrompt
        from django.core.exceptions import ValidationError

        # Create version 1
        p1 = LLMPrompt.objects.create(key="test.prompt", version=1, template="Hello {{ name }}!", is_active=True)
        self.assertTrue(p1.is_active)

        # Create version 2 (also active)
        p2 = LLMPrompt.objects.create(key="test.prompt", version=2, template="Hi {{ name }}!", is_active=True)
        self.assertTrue(p2.is_active)

        # Refresh p1 and verify it is now inactive
        p1.refresh_from_db()
        self.assertFalse(p1.is_active)

        # Check unique constraint on key + version
        with self.assertRaises(Exception):
            LLMPrompt.objects.create(key="test.prompt", version=2, template="Duplicate version")

    def test_jinja2_compilation_and_rendering(self):
        from llm.models import LLMPrompt
        from llm.prompts import PromptRegistry
        from django.core.exceptions import ValidationError

        # Malformed template syntax -> should raise ValidationError on clean/save
        with self.assertRaises(ValidationError):
            LLMPrompt.objects.create(key="bad.template", template="Hello {{ name")

        # Correct template rendering
        p = LLMPrompt.objects.create(key="render.test", template="Hello {{ user.first_name }}!")
        rendered = PromptRegistry.render("render.test", {"user": {"first_name": "Alice"}})
        self.assertEqual(rendered["rendered_prompt"], "Hello Alice!")
        self.assertEqual(rendered["prompt_version"], p.version)

    def test_historical_used_prompt_immutability(self):
        from llm.models import LLMPrompt, PromptRun

        p = LLMPrompt.objects.create(key="immutable.test", version=1, template="Initial template", is_active=True)

        # Associate with a PromptRun
        PromptRun.objects.using('telemetry').create(
            purpose="test",
            prompt_text="Initial template",
            response_text="ok",
            model_name="mock",
            prompt_key=p.key,
            prompt_version=p.version
        )

        # Edit the template text and save
        v1_id = p.id
        p.template = "Changed template"
        p.save()

        # Original prompt record with version 1 remains unchanged in DB with is_active=False
        p_v1 = LLMPrompt.objects.get(id=v1_id)
        self.assertEqual(p_v1.template, "Initial template")
        self.assertFalse(p_v1.is_active)
        self.assertEqual(p_v1.version, 1)

        # New prompt record version 2 is inserted with the updated template and is_active=True
        p_v2 = LLMPrompt.objects.get(key="immutable.test", is_active=True)
        self.assertEqual(p_v2.template, "Changed template")
        self.assertEqual(p_v2.version, 2)


class LLMObservabilityTestCase(TestCase):
    databases = {'default', 'telemetry'}
    def test_request_context_propagation_and_merging(self):
        from llm.context import LLMRequestContext

        with LLMRequestContext(correlation_id="root_id", operation="root_op", metadata={"k1": "v1"}):
            self.assertEqual(LLMRequestContext.get_value("correlation_id"), "root_id")
            self.assertEqual(LLMRequestContext.get_value("operation"), "root_op")
            self.assertEqual(LLMRequestContext.get_value("metadata")["k1"], "v1")

            # Nested context overrides field and merges metadata
            with LLMRequestContext(operation="child_op", metadata={"k2": "v2"}):
                self.assertEqual(LLMRequestContext.get_value("correlation_id"), "root_id")
                self.assertEqual(LLMRequestContext.get_value("operation"), "child_op")
                self.assertEqual(LLMRequestContext.get_value("metadata")["k1"], "v1")
                self.assertEqual(LLMRequestContext.get_value("metadata")["k2"], "v2")

            # Restores parent context
            self.assertEqual(LLMRequestContext.get_value("operation"), "root_op")

        self.assertIsNone(LLMRequestContext.get_current())

    @patch("llm.tracing.get_tracer")
    def test_router_instrumentation_saves_observability_fields(self, mock_get_tracer):
        from llm.router import IntelligentRouter
        from llm.models import PromptRun, LLMPrompt
        from llm.context import LLMRequestContext
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

        # Mock Tracer and Span context
        trace_id_int = 0x1234567890abcdef1234567890abcdef
        span_id_int = 0x1234567890abcdef
        span_ctx = SpanContext(trace_id_int, span_id_int, is_remote=False, trace_flags=TraceFlags(1))
        mock_span = NonRecordingSpan(span_ctx)
        
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        mock_get_tracer.return_value = mock_tracer

        # Prepare adapter mock
        adapter = MagicMock()
        adapter.model_name = "gemini-2.5-flash"
        adapter.generate.return_value = {
            "type": "text",
            "text": "Success response",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15
        }

        # Setup prompt registry template
        prompt_obj = LLMPrompt.objects.create(key="observe.prompt", version=1, template="Greet {{ name }}", is_active=True)

        router = IntelligentRouter()
        router.adapters = {"test": adapter}

        # Run with context
        with LLMRequestContext(correlation_id="corr_99", operation="test_op", metadata={"test_meta": "yes"}):
            res = router._generate_with_logging(
                provider_key="test",
                adapter=adapter,
                prompt="Greet Alice",
                system_prompt="sys",
                tools=[],
                prompt_key="observe.prompt",
                prompt_version=1,
                template_variables={"name": "Alice"},
                prompt_obj=prompt_obj
            )

        self.assertEqual(res["type"], "text")
        
        # Verify PromptRun DB record fields
        run_record = PromptRun.objects.using('telemetry').filter(correlation_id="corr_99").first()
        self.assertIsNotNone(run_record)
        self.assertEqual(run_record.operation, "test_op")
        self.assertEqual(run_record.prompt_key, "observe.prompt")
        self.assertEqual(run_record.prompt_version, 1)
        self.assertEqual(run_record.template_variables, {"name": "Alice"})
        self.assertEqual(run_record.provider, "test")
        self.assertEqual(run_record.model, "gemini-2.5-flash")
        self.assertEqual(run_record.trace_id, format(trace_id_int, '032x'))
        self.assertEqual(run_record.span_id, format(span_id_int, '016x'))
        self.assertEqual(run_record.metadata, {"test_meta": "yes"})
        self.assertEqual(run_record.status, "success")
        self.assertGreater(run_record.total_cost, 0.0)


class TelemetryDatabaseSeparationTestCase(TestCase):
    databases = {'default', 'telemetry'}
    def test_database_routing_separation(self):
        from llm.models import LLMPrompt, PromptRun

        # 1. LLMPrompt uses default database
        p = LLMPrompt.objects.create(key="db.separation.test", template="Hello separation test!")
        self.assertEqual(p._state.db, "default")

        # Verify it can be retrieved from default database
        p_retrieved = LLMPrompt.objects.using("default").filter(id=p.id).first()
        self.assertIsNotNone(p_retrieved)

        # 2. PromptRun uses telemetry database
        pr = PromptRun.objects.create(
            purpose="test",
            prompt_text="Test prompt",
            response_text="Test response",
            model_name="test-model"
        )
        self.assertEqual(pr._state.db, "telemetry")

        # Verify it is written to telemetry DB
        pr_retrieved = PromptRun.objects.using("telemetry").filter(id=pr.id).first()
        self.assertIsNotNone(pr_retrieved)

        # Verify it cannot be queried on default DB (table does not exist)
        from django.db import OperationalError
        try:
            PromptRun.objects.using("default").filter(id=pr.id).first()
            self.fail("Querying PromptRun on default DB should fail because the table does not exist.")
        except OperationalError:
            # Expected! The table is not migrated to default DB.
            pass

    def test_prompt_registry_reads_from_default(self):
        from llm.models import LLMPrompt
        from llm.prompts import PromptRegistry

        p = LLMPrompt.objects.create(key="registry.test.db", template="Test registry templating {{ var }}", is_active=True)
        self.assertEqual(p._state.db, "default")

        rendered = PromptRegistry.render("registry.test.db", {"var": "ok"})
        self.assertEqual(rendered["rendered_prompt"], "Test registry templating ok")
        self.assertEqual(rendered["prompt_version"], p.version)

    def test_telemetry_failure_does_not_break_execution(self):
        from llm.router import IntelligentRouter
        from unittest.mock import patch
        from django.db import OperationalError

        # Make sure target prompt exists on default
        from llm.models import LLMPrompt
        LLMPrompt.objects.create(key="fail.test.prompt", version=1, template="Hi {{ name }}", is_active=True)

        router = IntelligentRouter()
        
        with patch("llm.models.PromptRun.objects.using") as mock_using:
            mock_using.side_effect = OperationalError("Simulated database disk failure")

            # Running generate with observation context. Telemetry save will raise
            # OperationalError, but router execution should complete successfully.
            res = router.generate(
                prompt="test telemetry failure",
                prompt_key="fail.test.prompt",
                prompt_version=1,
                template_variables={"name": "Bob"}
            )
            # The router completed generation despite telemetry persistence failing
            self.assertIsNotNone(res)


class LLMPromptVersioningTestCase(TestCase):
    databases = {'default', 'telemetry'}
    def test_copy_and_insert_versioning_on_template_edit(self):
        from llm.models import LLMPrompt
        from llm.prompts import PromptRegistry

        # 1. Create version 1 prompt
        v1 = LLMPrompt.objects.create(
            key="test.versioning.key",
            version=1,
            template="Hello v1 {{ name }}",
            description="Initial version",
            is_active=True
        )
        self.assertEqual(v1.version, 1)

        # Render prompt via registry -> resolves v1
        rendered1 = PromptRegistry.render("test.versioning.key", {"name": "Alice"})
        self.assertEqual(rendered1["rendered_prompt"], "Hello v1 Alice")
        self.assertEqual(rendered1["prompt_version"], 1)

        # 2. Simulate editing the prompt in Django Admin
        # Fetch the prompt, change template/description, and call save()
        prompt_to_edit = LLMPrompt.objects.get(pk=v1.id)
        prompt_to_edit.template = "Hello v2 {{ name }}"
        prompt_to_edit.description = "Updated version"
        prompt_to_edit.save()

        # 3. Assert original v1 record is still preserved in DB with is_active=False
        v1_reloaded = LLMPrompt.objects.get(pk=v1.id)
        self.assertEqual(v1_reloaded.template, "Hello v1 {{ name }}")
        self.assertFalse(v1_reloaded.is_active)
        self.assertEqual(v1_reloaded.version, 1)

        # 4. Assert new v2 record exists in DB with is_active=True
        v2 = LLMPrompt.objects.get(key="test.versioning.key", is_active=True)
        self.assertNotEqual(v2.id, v1.id)
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.template, "Hello v2 {{ name }}")
        self.assertEqual(v2.description, "Updated version")

        # 5. PromptRegistry automatically resolves to the new active v2 version
        rendered2 = PromptRegistry.render("test.versioning.key", {"name": "Alice"})
        self.assertEqual(rendered2["rendered_prompt"], "Hello v2 Alice")
        self.assertEqual(rendered2["prompt_version"], 2)


class TestSchemaModel(BaseModel):
    summary: str = Field(...)
    score: int = Field(...)


class LLMGenericModelRoutingTestCase(TestCase):
    databases = {'default', 'telemetry'}

    def test_pool_selection_and_priority(self):
        from llm.enums import LLMComplexity
        from llm.registry import get_pool_models

        simple_models = [m.model_name for m in get_pool_models(LLMComplexity.SIMPLE)]
        self.assertEqual(simple_models[0], "gemini-3.1-flash-lite")

        standard_models = [m.model_name for m in get_pool_models(LLMComplexity.STANDARD)]
        self.assertEqual(standard_models[0], "gemini-3.5-flash")

        complex_models = [m.model_name for m in get_pool_models(LLMComplexity.COMPLEX)]
        self.assertEqual(complex_models[0], "gemini-3.7-flash")

    def test_structured_output_schema_validation_and_fallback(self):
        from llm.router import IntelligentRouter
        from llm.contracts import LLMRequest
        from llm.enums import LLMOperation, LLMComplexity

        router = IntelligentRouter()
        router.health_monitor.reset("google")

        # Mock adapters: first model returns bad json schema, second returns valid schema
        bad_adapter = MagicMock()
        bad_adapter.model_name = "gemini-3.7-flash"
        bad_adapter.generate.return_value = {"type": "text", "text": "{\"invalid\": \"json\"}"}

        good_adapter = MagicMock()
        good_adapter.model_name = "gemini-3.6-flash"
        good_adapter.generate.return_value = {"type": "text", "text": "{\"summary\": \"Valid\", \"score\": 95}"}

        def mock_get_adapter(cfg):
            if cfg.model_name == "gemini-3.7-flash":
                return bad_adapter
            return good_adapter

        with patch.object(router, "_get_adapter_for_model", side_effect=mock_get_adapter):
            req = LLMRequest(
                operation=LLMOperation.STRUCTURED_OUTPUT,
                complexity=LLMComplexity.COMPLEX,
                prompt="Generate summary",
                schema=TestSchemaModel
            )
            res = router.execute(req)
            self.assertTrue(res.is_success())
            self.assertIsInstance(res.output, TestSchemaModel)
            self.assertEqual(res.output.summary, "Valid")
            self.assertEqual(res.output.score, 95)
            self.assertEqual(res.attempts, 2)

    def test_non_retryable_error_halts_fallback(self):
        from llm.router import IntelligentRouter
        from llm.contracts import LLMRequest
        from llm.enums import LLMOperation, LLMComplexity, LLMErrorCategory

        router = IntelligentRouter()
        auth_adapter = MagicMock()
        auth_adapter.model_name = "gemini-3.7-flash"
        auth_adapter.generate.return_value = {"type": "error", "status_code": 401, "text": "Unauthorized API Key"}

        with patch.object(router, "_get_adapter_for_model", return_value=auth_adapter):
            req = LLMRequest(
                operation=LLMOperation.GENERATE,
                complexity=LLMComplexity.COMPLEX,
                prompt="Hello"
            )
            res = router.execute(req)
            self.assertFalse(res.is_success())
            self.assertEqual(res.error_category, LLMErrorCategory.AUTHENTICATION_ERROR)
            # Must halt on 401 without wasting retries across all pool models
            self.assertEqual(res.attempts, 1)





