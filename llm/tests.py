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
