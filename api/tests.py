from django.test import TestCase
from unittest.mock import patch, MagicMock
import os
import json
import base64

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

from core.tools.implementations.github_tool import (
    GitHubSearchCodeTool,
    GitHubReadFileTool,
    GitHubWriteFileTool,
    GitHubCreatePRTool
)
from core.agents.research_agent import LoopState, _compress_old_tool_results
from memory.models import UserProfile, AgentRun, ToolExecution
from orchestrator.single_agent import SingleAgentOrchestrator

class GitHubToolsTestCase(TestCase):
    
    @patch('core.tools.implementations.github_tool.subprocess.run')
    def test_search_code_success(self, mock_run):
        # Mock successful gh search code
        mock_response = MagicMock()
        mock_response.returncode = 0
        mock_response.stdout = json.dumps([
            {
                "path": "resume.md",
                "repository": {"fullName": "Ingenuity07/resume"},
                "textMatches": [{"fragment": "Software Engineer"}]
            }
        ])
        mock_response.stderr = ""
        mock_run.return_value = mock_response
        
        tool = GitHubSearchCodeTool()
        result = tool.execute(query="Software Engineer", repo="Ingenuity07/resume")
        
        self.assertIn("resume.md", result)
        self.assertIn("Ingenuity07/resume", result)
        self.assertIn("Software Engineer", result)
        
    @patch('core.tools.implementations.github_tool.subprocess.run')
    def test_read_file_success(self, mock_run):
        # Mock successful file contents read
        mock_response = MagicMock()
        mock_response.returncode = 0
        file_content = "Hello World\nLine 2"
        b64_content = base64.b64encode(file_content.encode('utf-8')).decode('ascii')
        mock_response.stdout = json.dumps({
            "encoding": "base64",
            "content": b64_content
        })
        mock_response.stderr = ""
        mock_run.return_value = mock_response
        
        tool = GitHubReadFileTool()
        result = tool.execute(repo="Ingenuity07/resume", path="resume.md")
        
        self.assertIn("1: Hello World", result)
        self.assertIn("2: Line 2", result)
        
    @patch('core.tools.implementations.github_tool.subprocess.run')
    def test_write_file_success(self, mock_run):
        # Mock branch exists and write is successful
        mock_response_exists = MagicMock()
        mock_response_exists.returncode = 0
        mock_response_exists.stdout = json.dumps({"object": {"sha": "12345"}})
        
        mock_response_write = MagicMock()
        mock_response_write.returncode = 0
        mock_response_write.stdout = json.dumps({"content": {"html_url": "http://github.com/file"}})
        
        mock_run.side_effect = [mock_response_exists, mock_response_exists, mock_response_write]
        
        tool = GitHubWriteFileTool()
        result = tool.execute(
            repo="Ingenuity07/resume",
            path="resume.md",
            content="New Resume Content",
            branch="update-resume",
            commit_message="update"
        )
        
        self.assertIn("Success", result)
        self.assertIn("http://github.com/file", result)


class AgentLoopTestCase(TestCase):
    
    def test_loop_state_stuck_detection(self):
        loop_state = LoopState()
        # Non-stuck calls
        loop_state.record_tool_call("github_read_file", {"repo": "user/repo", "path": "file1.txt"}, success=True)
        loop_state.record_tool_call("github_read_file", {"repo": "user/repo", "path": "file2.txt"}, success=True)
        self.assertFalse(loop_state.is_stuck())
        
        # Stuck calls (same tool + same discriminator arguments 3 times in a row)
        loop_state.record_tool_call("github_read_file", {"repo": "user/repo", "path": "file1.txt"}, success=True)
        loop_state.record_tool_call("github_read_file", {"repo": "user/repo", "path": "file1.txt"}, success=True)
        loop_state.record_tool_call("github_read_file", {"repo": "user/repo", "path": "file1.txt"}, success=True)
        self.assertTrue(loop_state.is_stuck())
        
    def test_context_compression(self):
        messages = [
            {"role": "user", "content": "Help me"},
            {"role": "assistant", "content": "Sure", "tool_call": {"name": "test_tool", "args": {}}},
            {"role": "tool", "content": "Line1\n" + "\n".join([f"Line {i}" for i in range(100)]) + "\nLineLast"},
            {"role": "assistant", "content": "Next step", "tool_call": {"name": "test_tool2", "args": {}}},
            {"role": "tool", "content": "Recent log output\nLine 2"}
        ]
        
        # Iteration 2 (current_iteration=2): older tool results (iteration < 2-1 = 1) should be compressed
        # Here tool_iteration for first tool is 0, which is < 1. First tool result has > 50 lines. It should compress.
        # Second tool result has tool_iteration = 1, which is not < 1. It should NOT compress.
        _compress_old_tool_results(messages, current_iteration=2)
        
        # First tool output should be compressed
        compressed_content = messages[2]["content"]
        self.assertIn("lines omitted", compressed_content)
        self.assertIn("Line1", compressed_content)
        self.assertIn("LineLast", compressed_content)
        
        # Second tool output (the most recent one) should not be compressed
        self.assertEqual(messages[4]["content"], "Recent log output\nLine 2")

    @patch('core.llm_providers.gemini_api.GeminiAPIProvider.generate')
    def test_orchestrator_logs_tool_execution(self, mock_generate):
        # Create models
        user = UserProfile.objects.create(username="test_user", email="test@test.com")
        orchestrator = SingleAgentOrchestrator()
        
        # Mock LLM provider to return a tool call followed by a final response
        mock_generate.side_effect = [
            {
                "type": "tool_call",
                "tool_name": "read_file",
                "tool_args": {"file_path": "Base-prompt.md"}
            },
            {
                "type": "text",
                "text": json.dumps({"response": "Final Answer"})
            }
        ]
        
        response = orchestrator.handle_request(user_profile=user, conversation_id=None, message_text="Hello")
        
        # Verify ToolExecution record was saved to database
        runs = AgentRun.objects.all()
        self.assertEqual(runs.count(), 1)
        
        tool_execs = ToolExecution.objects.filter(agent_run=runs[0])
        self.assertEqual(tool_execs.count(), 1)
        self.assertEqual(tool_execs[0].tool_name, "read_file")
        self.assertEqual(tool_execs[0].status, "success")

    @patch('core.llm_providers.gemini_api.GeminiAPIProvider.generate')
    def test_job_reasoning_agent_routing(self, mock_generate):
        # Create models
        user = UserProfile.objects.create(username="job_user", email="job@test.com")
        orchestrator = SingleAgentOrchestrator()
        
        # Mock LLM provider to return a final response
        mock_generate.return_value = {
            "type": "text",
            "text": json.dumps({"response": "Tailored Resume Report"})
        }
        
        # Call orchestrator with agent_type="JobReasoningAgent"
        response = orchestrator.handle_request(
            user_profile=user,
            conversation_id=None,
            message_text="Analyze greenhouse.io/job",
            agent_type="JobReasoningAgent"
        )
        
        # Verify AgentRun record was created with type JobReasoningAgent
        runs = AgentRun.objects.filter(agent_type="JobReasoningAgent")
        self.assertEqual(runs.count(), 1)
        self.assertEqual(runs[0].status, "completed")

    @patch('core.agents.research_agent.ResearchAgent.execute')
    def test_job_reasoning_agent_profile_data_injection(self, mock_execute):
        from core.agents.job_reasoning_agent import JobReasoningAgent
        
        agent = JobReasoningAgent(provider=MagicMock())
        user_profile_data = {
            "full_name": "Test Name",
            "email": "test@test.com",
            "phone": "12345",
            "linkedin_url": "link",
            "github_url": "git",
            "portfolio_url": "port"
        }
        
        agent.execute(prompt="Hello", user_profile_data=user_profile_data)
        
        # Verify parent execute was called and prompt has the injected details
        mock_execute.assert_called_once()
        called_prompt = mock_execute.call_args[0][0]
        self.assertIn("Test Name", called_prompt)
        self.assertIn("test@test.com", called_prompt)
        self.assertIn("User Profile Information", called_prompt)

    @patch('memory.tasks.run_agent_task.delay')
    def test_async_chat_endpoint_routing(self, mock_delay):
        # Create models
        user = UserProfile.objects.create(username="async_user", email="async@test.com")
        
        # Mock Celery delay to return a mock task object with a fake id
        mock_task = MagicMock()
        mock_task.id = "mock-celery-task-id"
        mock_delay.return_value = mock_task
        
        # Send post request to /api/chat/ with async_execution=True
        response = self.client.post(
            '/api/chat/',
            {
                "message": "Find python jobs",
                "async_execution": True,
                "agent_type": "JobReasoningAgent"
            },
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["task_id"], "mock-celery-task-id")
        self.assertIn("conversation_id", response.data)
        
        # Verify Celery delay was called with correct parameters
        mock_delay.assert_called_once_with(
            username="default_user",
            conversation_id=response.data["conversation_id"],
            message_text="Find python jobs",
            agent_type="JobReasoningAgent"
        )


class BrowserToolTestCase(TestCase):
    
    @patch('core.tools.implementations.browser_tool.PlaywrightBrowser.get_page')
    def test_browser_tool_navigate_and_content(self, mock_get_page):
        from core.tools.implementations.browser_tool import BrowserTool
        
        # Mock Playwright Page
        mock_page = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response
        mock_page.title.return_value = "Mock Page Title"
        mock_page.url = "http://mocksite.com"
        
        # Mock text content evaluation
        mock_page.locator.return_value.inner_text.return_value = "Welcome to Mock Page"
        mock_page.evaluate.return_value = [
            {"tag": "button", "type": "submit", "name": "apply", "id": "btn-apply", "text": "Apply Now", "label": ""}
        ]
        
        mock_get_page.return_value = mock_page
        
        tool = BrowserTool()
        # Test navigate action
        nav_result = tool.execute(action="navigate", url="http://mocksite.com")
        self.assertIn("Mock Page Title", nav_result)
        self.assertIn("Successfully navigated", nav_result)
        
        # Test get_content action
        content_result = tool.execute(action="get_content")
        self.assertIn("Mock Page Title", content_result)
        self.assertIn("Welcome to Mock Page", content_result)
        self.assertIn("btn-apply", content_result)


class SchedulerTestCase(TestCase):
    
    def setUp(self):
        self.user = UserProfile.objects.create(username="sched_user", email="sched@test.com")
        
    def test_scheduler_lifecycle(self):
        from memory.scheduler import (
            create_interval_agent_task,
            create_cron_agent_task,
            list_schedules,
            disable_schedule
        )
        
        # Test creating interval task
        task1 = create_interval_agent_task(
            name="job-search-every-10-min",
            username="sched_user",
            prompt="Find Go developer jobs",
            interval_minutes=10
        )
        self.assertEqual(task1.name, "job-search-every-10-min")
        self.assertEqual(task1.interval.every, 10)
        
        # Test creating cron task
        task2 = create_cron_agent_task(
            name="job-search-daily-9am",
            username="sched_user",
            prompt="Find Django developer jobs",
            cron_expression="0 9 * * *"
        )
        self.assertEqual(task2.name, "job-search-daily-9am")
        self.assertEqual(task2.crontab.minute, "0")
        self.assertEqual(task2.crontab.hour, "9")
        
        # Test listing tasks
        schedules = list_schedules("sched_user")
        self.assertEqual(len(schedules), 2)
        names = [s["name"] for s in schedules]
        self.assertIn("job-search-every-10-min", names)
        self.assertIn("job-search-daily-9am", names)
        
        # Test disabling a task
        disabled = disable_schedule("job-search-every-10-min")
        self.assertFalse(disabled.enabled)


class SecurityLockingAuditingVisionTestCase(TestCase):
    
    def setUp(self):
        self.user = UserProfile.objects.create(username="default_user", email="default@test.com")
        
    def test_github_safety_guards(self):
        from core.tools.implementations.github_tool import GitHubWriteFileTool
        tool = GitHubWriteFileTool()
        
        # Test protected branch block
        res_branch = tool.execute(repo="owner/repo", path="resume.md", content="Hi", branch="main", commit_message="Update")
        self.assertIn("blocked", res_branch)
        
        # Test protected file block
        res_file = tool.execute(repo="owner/repo", path=".github/workflows/ci.yml", content="Hi", branch="dev", commit_message="Update")
        self.assertIn("blocked", res_file)
        
    def test_browser_safety_guards(self):
        from core.tools.implementations.browser_tool import BrowserTool
        tool = BrowserTool()
        
        # Test file:// block
        res_file = tool.execute(action="navigate", url="file:///etc/passwd")
        self.assertIn("blocked", res_file)
        
        # Test localhost block
        res_local = tool.execute(action="navigate", url="http://localhost:8000")
        self.assertIn("blocked", res_local)

    @patch('core.llm_providers.gemini_api.GeminiAPIProvider.generate')
    def test_token_and_cost_auditing(self, mock_generate):
        orchestrator = SingleAgentOrchestrator()
        
        # Mock API response to return prompt/completion tokens
        mock_generate.return_value = {
            "type": "text",
            "text": json.dumps({"response": "Done"}),
            "prompt_tokens": 1000,
            "completion_tokens": 500
        }
        
        orchestrator.handle_request(user_profile=self.user, conversation_id=None, message_text="Hello")
        
        # Verify AgentRun has values saved and correct cost calculated
        run = AgentRun.objects.filter(conversation__user_profile=self.user).first()
        self.assertEqual(run.prompt_tokens, 1000)
        self.assertEqual(run.completion_tokens, 500)
        # Cost: (1000 * 0.075 + 500 * 0.30) / 1,000,000 = (75 + 150) / 1,000,000 = 225 / 1,000,000 = 0.000225
        from decimal import Decimal
        self.assertEqual(run.total_cost, Decimal("0.000225"))

    def test_distributed_locking(self):
        from django.core.cache import cache
        from memory.tasks import run_agent_task
        
        lock_key = "lock:run_agent_task:default_user:new"
        cache.set(lock_key, "locked", timeout=60)
        
        # Run task when lock is held -> should skip
        res = run_agent_task(username="default_user", conversation_id=None, message_text="Hello")
        self.assertEqual(res["status"], "Skipped")
        
        # Release lock and check success
        cache.delete(lock_key)
        
    @patch('requests.post')
    def test_analyze_screenshot_tool(self, mock_post):
        from core.tools.implementations.vision_tool import AnalyzeScreenshotTool
        
        # Mock file existence checks and mock image read
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Found visual error in form."}]
                }
            }]
        }
        mock_post.return_value = mock_response
        
        tool = AnalyzeScreenshotTool()
        
        # Create a dummy file to analyze
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake-image-bytes")
            tmp_name = tmp.name
            
        # Temporarily mock get_screenshot_dir to return temp dir
        with patch('core.tools.implementations.vision_tool.get_screenshot_dir', return_value=os.path.dirname(tmp_name)):
            res = tool.execute(screenshot_name=os.path.basename(tmp_name), query="Inspect errors")
            self.assertIn("Found visual error", res)
            
        os.unlink(tmp_name)




