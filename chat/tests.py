from django.test import TestCase, TransactionTestCase
from unittest.mock import patch, MagicMock
import os
import json
import base64

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

from llm.tools.implementations.github_tool import (
    GitHubSearchCodeTool,
    GitHubReadFileTool,
    GitHubWriteFileTool,
    GitHubCreatePRTool
)
from chat.agents.research_agent import LoopState, _compress_old_tool_results
from chat.models import AgentRun, ToolExecution
from knowledge_base.models import UserProfile
from chat.orchestrator.single_agent import SingleAgentOrchestrator

class GitHubToolsTestCase(TestCase):
    
    @patch('llm.tools.implementations.github_tool.subprocess.run')
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
        
    @patch('llm.tools.implementations.github_tool.subprocess.run')
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
        
    @patch('llm.tools.implementations.github_tool.subprocess.run')
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


class AgentLoopTestCase(TransactionTestCase):
    
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

    @patch('llm.gemini_api.GeminiAPIProvider.generate')
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

    @patch('llm.gemini_api.GeminiAPIProvider.generate')
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

    @patch('chat.agents.research_agent.ResearchAgent.execute')
    def test_job_reasoning_agent_profile_data_injection(self, mock_execute):
        from chat.agents.job_reasoning_agent import JobReasoningAgent
        
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

    @patch('chat.tasks.run_agent_task.delay')
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
    
    @patch('llm.tools.implementations.browser_tool.PlaywrightBrowser.get_page')
    def test_browser_tool_navigate_and_content(self, mock_get_page):
        from llm.tools.implementations.browser_tool import BrowserTool
        
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
        from chat.scheduler import (
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


class SecurityLockingAuditingVisionTestCase(TransactionTestCase):
    
    def setUp(self):
        self.user = UserProfile.objects.create(username="default_user", email="default@test.com")
        
    def test_github_safety_guards(self):
        from llm.tools.implementations.github_tool import GitHubWriteFileTool
        tool = GitHubWriteFileTool()
        
        # Test protected branch block
        res_branch = tool.execute(repo="owner/repo", path="resume.md", content="Hi", branch="main", commit_message="Update")
        self.assertIn("blocked", res_branch)
        
        # Test protected file block
        res_file = tool.execute(repo="owner/repo", path=".github/workflows/ci.yml", content="Hi", branch="dev", commit_message="Update")
        self.assertIn("blocked", res_file)
        
    def test_browser_safety_guards(self):
        from llm.tools.implementations.browser_tool import BrowserTool
        tool = BrowserTool()
        
        # Test file:// block
        res_file = tool.execute(action="navigate", url="file:///etc/passwd")
        self.assertIn("blocked", res_file)
        
        # Test localhost block
        res_local = tool.execute(action="navigate", url="http://localhost:8000")
        self.assertIn("blocked", res_local)

    @patch('llm.gemini_api.GeminiAPIProvider.generate')
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
        from chat.tasks import run_agent_task
        
        lock_key = "lock:run_agent_task:default_user:new"
        cache.set(lock_key, "locked", timeout=60)
        
        # Run task when lock is held -> should skip
        res = run_agent_task(username="default_user", conversation_id=None, message_text="Hello")
        self.assertEqual(res["status"], "Skipped")
        
        # Release lock and check success
        cache.delete(lock_key)
        
    @patch('requests.post')
    def test_analyze_screenshot_tool(self, mock_post):
        from llm.tools.implementations.vision_tool import AnalyzeScreenshotTool
        
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
        with patch('llm.tools.implementations.vision_tool.get_screenshot_dir', return_value=os.path.dirname(tmp_name)):
            res = tool.execute(screenshot_name=os.path.basename(tmp_name), query="Inspect errors")
            self.assertIn("Found visual error", res)
            
        os.unlink(tmp_name)


class DjangoCheckpointSaverTestCase(TestCase):
    
    def test_checkpoint_saver_lifecycle(self):
        from chat.agents.checkpoint_saver import DjangoCheckpointSaver
        
        saver = DjangoCheckpointSaver()
        
        config = {"configurable": {"thread_id": "thread-123"}}
        checkpoint = {
            "v": 1,
            "id": "checkpoint-1",
            "ts": "2026-07-07T12:00:00Z",
            "channel_values": {"messages": ["hello"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": []
        }
        metadata = {
            "source": "loop",
            "step": 1,
            "writes": None,
            "parents": {}
        }
        
        # Test put
        res_config = saver.put(config, checkpoint, metadata, {})
        self.assertEqual(res_config["configurable"]["thread_id"], "thread-123")
        self.assertEqual(res_config["configurable"]["checkpoint_id"], "checkpoint-1")
        
        # Test get_tuple
        tup = saver.get_tuple(res_config)
        self.assertIsNotNone(tup)
        self.assertEqual(tup.checkpoint["id"], "checkpoint-1")
        self.assertEqual(tup.checkpoint["channel_values"]["messages"], ["hello"])
        self.assertEqual(tup.metadata["step"], 1)
        
        # Test put_writes
        writes = [("output", "Done writing file.")]
        saver.put_writes(res_config, writes, "task-1")
        
        # Test get_tuple with writes
        tup2 = saver.get_tuple(res_config)
        self.assertEqual(len(tup2.pending_writes), 1)
        self.assertEqual(tup2.pending_writes[0][0], "task-1")
        self.assertEqual(tup2.pending_writes[0][1], "output")
        self.assertEqual(tup2.pending_writes[0][2], "Done writing file.")
        
        # Test list
        tups = list(saver.list(config))
        self.assertEqual(len(tups), 1)
        self.assertEqual(tups[0].config["configurable"]["checkpoint_id"], "checkpoint-1")


class V2AgentGraphTestCase(TransactionTestCase):
    
    def setUp(self):
        self.user = UserProfile.objects.create(username="graph_user", email="graph@test.com")

    @patch('chat.agents.planner.PlannerAgent.generate_plan')
    @patch('chat.agents.research_agent.ResearchAgent.execute')
    @patch('chat.agents.job_reasoning_agent.JobReasoningAgent.execute')
    def test_v2_planner_executor_graph_flow(self, mock_job_execute, mock_research_execute, mock_plan):
        from chat.agents.v2_graph import get_v2_agent_graph
        from chat.agents.checkpoint_saver import DjangoCheckpointSaver
        
        # Mock planner to return steps
        mock_plan.return_value = ["search_jobs", "tailor_resume", "fill_application", "submit_application"]
        
        # Mock agent execution outputs
        mock_research_execute.return_value = "Found Python job post on Greenhouse"
        mock_job_execute.return_value = "Tailored resume / filled application form"
        
        # Instantiate saver and compile graph
        saver = DjangoCheckpointSaver()
        graph = get_v2_agent_graph(checkpoint_saver=saver)
        
        config = {"configurable": {"thread_id": "thread-456"}}
        
        # Initial run state
        initial_state = {
            "messages": [{"role": "user", "content": "Help me apply for a Python role"}],
            "plan": [],
            "step_index": 0,
            "scraped_data": "",
            "customized_resume_path": "",
            "screenshot_name": "",
            "human_approved": False,
            "status": "Searching",
            "user_profile_data": {},
            "error": None
        }
        
        # Run graph. It should execute planner, search_jobs, tailor_resume, fill_application,
        # and then halt at approval_wait because human_approved is False.
        final_state = graph.invoke(initial_state, config)
        
        self.assertEqual(final_state["status"], "Waiting Approval")
        self.assertEqual(final_state["step_index"], 3)  # Completed search_jobs (0), tailor_resume (1), fill_application (2)
        self.assertEqual(len(final_state["plan"]), 4)
        self.assertFalse(final_state["human_approved"])
        
        # Get checkpoint tuple from DB to ensure state was persisted
        tup = saver.get_tuple(config)
        self.assertIsNotNone(tup)
        self.assertEqual(tup.checkpoint["channel_values"]["status"], "Waiting Approval")
        
        # Now mock the human approval step: update state to human_approved=True and resume
        updated_state = dict(final_state)
        updated_state["human_approved"] = True
        
        # Run graph again from the checkpoint (using the same config/thread)
        resumed_state = graph.invoke(updated_state, config)
        
        # Asserts it executes the submit_application node and completes.
        self.assertEqual(resumed_state["status"], "Complete")
        self.assertEqual(resumed_state["step_index"], 4)


class ApproveAPITestCase(TransactionTestCase):
    def setUp(self):
        from chat.models import Conversation
        self.user = UserProfile.objects.create(username="default_user", email="default@example.com")
        self.conversation = Conversation.objects.create(user_profile=self.user)
        # Create a mock checkpoint in the database for this thread
        from chat.agents.checkpoint_saver import DjangoCheckpointSaver
        saver = DjangoCheckpointSaver()
        config = {"configurable": {"thread_id": str(self.conversation.id)}}
        checkpoint = {
            "v": 1,
            "id": "1ef6345c-ba6c-67aa-8504-25656b07c68a",
            "ts": "2026-07-11T03:00:00Z",
            "channel_values": {"status": "Waiting Approval", "human_approved": False, "messages": []},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": []
        }
        metadata = {"source": "input", "step": 1, "writes": None, "parents": []}
        saver.put(config, checkpoint, metadata, {})

    @patch('chat.tasks.run_agent_task.delay')
    def test_approve_api_success_resume(self, mock_celery_task):
        mock_celery_task.return_value.id = "mock-celery-id-123"
        
        response = self.client.post('/api/chat/approve/', {
            "conversation_id": str(self.conversation.id),
            "approved": True
        }, content_type="application/json")
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Resumed")
        self.assertEqual(response.json()["task_id"], "mock-celery-id-123")
        
        # Verify that state was updated in the DB checkpointer
        from chat.agents.checkpoint_saver import DjangoCheckpointSaver
        saver = DjangoCheckpointSaver()
        config = {"configurable": {"thread_id": str(self.conversation.id)}}
        tup = saver.get_tuple(config)
        self.assertTrue(tup.checkpoint["channel_values"]["human_approved"])
        mock_celery_task.assert_called_once_with(
            username=self.user.username,
            conversation_id=str(self.conversation.id),
            message_text="",
            agent_type="ResearchAgent"
        )

    def test_approve_api_not_found(self):
        import uuid
        fake_id = uuid.uuid4()
        response = self.client.post('/api/chat/approve/', {
            "conversation_id": str(fake_id),
            "approved": True
        }, content_type="application/json")
        self.assertEqual(response.status_code, 404)


class AgentMemoryTestCase(TestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(username="default_user", email="default@example.com")

    @patch('llm.gemini_api.GeminiAPIProvider.generate')
    def test_memory_injection_and_extraction(self, mock_generate):
        from chat.models import AgentMemory
        # Setup memory in DB
        AgentMemory.objects.create(
            user_profile=self.user,
            category="preference",
            key="blocked_companies",
            value=["Facebook", "Apple"]
        )

        from chat.agents.v2_graph import get_v2_agent_graph
        graph = get_v2_agent_graph()
        
        initial_state = {
            "messages": [{"role": "user", "content": "Search for jobs"}],
            "plan": [],
            "step_index": 0,
            "scraped_data": "",
            "customized_resume_path": "",
            "screenshot_name": "",
            "human_approved": False,
            "status": "Searching",
            "user_profile_data": {"username": "default_user"},
            "agent_memories": [],
            "error": None
        }
        
        # Test Injection
        from chat.agents.v2_graph import memory_injection_node
        res = memory_injection_node(initial_state)
        self.assertEqual(len(res["agent_memories"]), 1)
        self.assertIn("preference.blocked_companies: ['Facebook', 'Apple']", res["agent_memories"][0])

        # Test Extraction
        mock_generate.return_value = {
            "text": '{"memories": [{"category": "preference", "key": "tech_stack", "value": ["Django", "React"]}]}'
        }
        
        from chat.agents.v2_graph import memory_extraction_node
        extraction_state = {
            "messages": [
                {"role": "user", "content": "I love Django and React"},
                {"role": "assistant", "content": "Got it."}
            ],
            "user_profile_data": {"username": "default_user"}
        }
        
        memory_extraction_node(extraction_state)
        
        # Verify it got saved in DB
        mem = AgentMemory.objects.get(user_profile=self.user, category="preference", key="tech_stack")
        self.assertEqual(mem.value, ["Django", "React"])


class WebSocketStreamingTestCase(TransactionTestCase):
    async def test_websocket_connect_and_stream(self):
        import uuid
        import json
        from channels.testing import WebsocketCommunicator
        from config.asgi import application
        from channels.layers import get_channel_layer
        conversation_id = str(uuid.uuid4())
        
        # Connect to websocket router
        communicator = WebsocketCommunicator(application, f"ws/chat/{conversation_id}/")
        connected, subprotocol = await communicator.connect()
        self.assertTrue(connected)
        
        # Post a message directly to the channel group
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"chat_{conversation_id}",
            {
                "type": "chat_message",
                "event_type": "test_event",
                "data": {"foo": "bar"}
            }
        )
        
        # Receive the message via WebsocketCommunicator
        response = await communicator.receive_from()
        data = json.loads(response)
        
        self.assertEqual(data["event_type"], "test_event")
        self.assertEqual(data["data"]["foo"], "bar")
        
        # Disconnect
        await communicator.disconnect()

    async def test_stream_agent_update_sends_websocket_payload(self):
        import uuid
        import json
        from channels.testing import WebsocketCommunicator
        from config.asgi import application
        conversation_id = str(uuid.uuid4())
        communicator = WebsocketCommunicator(application, f"ws/chat/{conversation_id}/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        
        # Trigger helper function (which runs synchronously but utilizes async_to_sync)
        from chat.agents.v2_graph import stream_agent_update
        stream_agent_update(conversation_id, "planner_start", {"message": "Beginning run"})
        
        # Receive event
        response = await communicator.receive_from()
        data = json.loads(response)
        self.assertEqual(data["event_type"], "planner_start")
        self.assertEqual(data["data"]["message"], "Beginning run")
        
        await communicator.disconnect()


class ReflectionAndCritiqueTestCase(TransactionTestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(username="critic_user", email="critic@example.com")
        from chat.models import Conversation
        self.conversation = Conversation.objects.create(user_profile=self.user)

    @patch('llm.gemini_api.GeminiAPIProvider.generate')
    def test_reflection_critic_retry_loop(self, mock_generate):
        # Setup mock sequence:
        # 1. Planner returns plan: ["scrape_job"]
        # 2. Executor 1st attempt at scrape_job: returns text response
        # 3. Critic 1st evaluation: returns success: false, critique warning
        # 4. Executor 2nd attempt at scrape_job: returns successful text response
        # 5. Critic 2nd evaluation: returns success: true
        # 6. Memory extraction: returns empty memories JSON
        mock_generate.side_effect = [
            # 1. Planner
            {
                "type": "text",
                "text": '{"plan": ["scrape_job"]}'
            },
            # 2. Executor (first attempt)
            {
                "type": "text",
                "text": '{"response": "Here is the first attempt at scraping."}'
            },
            # 3. Critic (first evaluation -> failure)
            {
                "type": "text",
                "text": '{"success": false, "critique": "Scraped content is empty. Please scrape again."}'
            },
            # 4. Executor (second attempt after critique)
            {
                "type": "text",
                "text": '{"response": "Scraped content: Python developer, remote, full-time."}'
            },
            # 5. Critic (second evaluation -> success)
            {
                "type": "text",
                "text": '{"success": true, "critique": "Looks good and contains details."}'
            },
            # 6. Memory Extraction
            {
                "type": "text",
                "text": '{"memories": []}'
            }
        ]

        from chat.orchestrator.single_agent import SingleAgentOrchestrator
        orchestrator = SingleAgentOrchestrator()
        
        res = orchestrator.handle_request(
            user_profile=self.user,
            conversation_id=self.conversation.id,
            message_text="Please scrape the job details at example.com"
        )
        
        self.assertIn("Scraped content: Python developer, remote, full-time.", res["response"])
        
        # Verify that we saved checking logs of tool executions or messages
        # Check that the critique feedback message was appended to the conversation in DB
        from chat.models import Message
        msgs = Message.objects.filter(conversation=self.conversation)
        
        # Verify the database states of checkpoints or runs
        # There should be an AgentRun record that finished
        from chat.models import AgentRun
        run = AgentRun.objects.filter(conversation=self.conversation).first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "completed")


class ConversationAPIEndpointsTestCase(TestCase):
    def setUp(self):
        from chat.models import Conversation, Message
        from knowledge_base.models import UserProfile
        self.user = UserProfile.objects.create(username="default_user", email="default@example.com")
        self.conversation = Conversation.objects.create(user_profile=self.user, title="Test Job Search Chat")
        self.msg1 = Message.objects.create(conversation=self.conversation, role="user", content="First query text")
        self.msg2 = Message.objects.create(conversation=self.conversation, role="assistant", content="First agent answer")

    def test_list_conversations(self):
        from django.urls import reverse
        url = reverse('conversation-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], str(self.conversation.id))
        self.assertEqual(data[0]["title"], "Test Job Search Chat")

    def test_detail_conversation(self):
        from django.urls import reverse
        url = reverse('conversation-detail', kwargs={"conversation_id": self.conversation.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], str(self.conversation.id))
        self.assertEqual(data["title"], "Test Job Search Chat")
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["messages"][0]["role"], "user")
        self.assertEqual(data["messages"][0]["content"], "First query text")
        self.assertEqual(data["messages"][1]["role"], "assistant")
        self.assertEqual(data["messages"][1]["content"], "First agent answer")

    def test_list_conversations_fallback_title(self):
        from chat.models import Conversation, Message
        from django.urls import reverse
        # Create a conversation without a title
        conv_no_title = Conversation.objects.create(user_profile=self.user)
        # Create a message under this conversation
        Message.objects.create(conversation=conv_no_title, role="user", content="This is a query about Python scraping tools")
        
        url = reverse('conversation-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Find conv_no_title in data
        target = next(item for item in data if item["id"] == str(conv_no_title.id))
        self.assertEqual(target["title"], "This is a query about Python scraping tools")
        
        # Verify it was saved back to database
        conv_no_title.refresh_from_db()
        self.assertEqual(conv_no_title.title, "This is a query about Python scraping tools")


class IntelligentRouterTestCase(TransactionTestCase):
    def setUp(self):
        from knowledge_base.models import UserProfile
        from llm.health import ProviderHealthMonitor
        ProviderHealthMonitor().health_status.clear()
        
        self.user = UserProfile.objects.create(
            username="test_router_user",
            email="router_test@example.com"
        )

    def test_complexity_scoring(self):
        from llm.scoring import calculate_complexity_score, get_complexity_tier
        
        # Simple prompt
        score_simple = calculate_complexity_score("Hi there")
        self.assertEqual(get_complexity_tier(score_simple), "simple")
        
        # Medium prompt
        score_med = calculate_complexity_score("I need to plan my job application process")
        self.assertEqual(get_complexity_tier(score_med), "medium")
        
        # Critical prompt (long prompt + keywords)
        critical_prompt = "Resume: Software Engineer. Please plan and reflect on customizing my resume for this JD: Python dev"
        score_crit = calculate_complexity_score(critical_prompt)
        self.assertEqual(get_complexity_tier(score_crit), "critical")

    def test_fallback_flow(self):
        from llm.router import IntelligentRouter
        with patch('llm.router.GeminiAdapter.generate') as mock_gemini, \
             patch('llm.router.GroqAdapter.generate') as mock_groq, \
             patch('llm.router.OllamaAdapter.generate') as mock_ollama:
             
            # Gemini and Groq return error, Ollama returns success
            mock_gemini.return_value = {"type": "error", "text": "Gemini Rate Limited", "status_code": 429}
            mock_groq.return_value = {"type": "error", "text": "Groq Rate Limited", "status_code": 429}
            mock_ollama.return_value = {"type": "text", "text": "Hello from local Ollama"}
            
            router = IntelligentRouter()
            result = router.generate(prompt="Explain python decorators")
            
            self.assertEqual(result["type"], "text")
            self.assertEqual(result["text"], "Hello from local Ollama")
            
            # Verify Groq is marked as unhealthy
            self.assertFalse(router.health_monitor.is_healthy("groq"))

    @patch('llm.router.GeminiAdapter.generate')
    def test_conversation_model_locking(self, mock_gemini):
        from chat.models import Conversation
        from llm.router import IntelligentRouter
        
        mock_gemini.return_value = {"type": "text", "text": "Tailored resume details..."}
        
        conv = Conversation.objects.create(user_profile=self.user)
        router = IntelligentRouter()
        router.set_active_conversation(str(conv.id))
        
        # Call generate (complexity will select critical -> gemini-flash)
        result = router.generate(
            prompt="Tailor my resume for this critical job description detailing all planning and execution steps",
        )
        
        self.assertEqual(result["type"], "text")
        
        # Verify provider and model are locked in DB
        conv.refresh_from_db()
        self.assertEqual(conv.selected_provider, "gemini-flash")
        self.assertEqual(conv.selected_model, "gemini-2.5-flash")

    @patch.dict(os.environ, {
        "ROUTER_PROVIDER_PRIORITY": "ollama,groq,gemini-flash",
        "ROUTER_FALLBACK_SIMPLE": "ollama,gemini-flash"
    })
    def test_env_fallback_priority_override(self):
        from llm.router import IntelligentRouter
        router = IntelligentRouter()
        
        # Verify master order parsed correctly for un-overridden tiers
        self.assertEqual(router.fallbacks["medium"], ["ollama", "groq", "gemini-flash"])
        # Verify tier-specific override takes precedence over master order
        self.assertEqual(router.fallbacks["simple"], ["ollama", "gemini-flash"])

    def test_provider_list_api_view(self):
        from rest_framework.test import APIClient
        client = APIClient()
        response = client.get('/api/providers/')
        self.assertEqual(response.status_code, 200)
        self.assertIn("providers", response.data)
        self.assertIn("fallbacks", response.data)
        provider_keys = [p["key"] for p in response.data["providers"]]
        self.assertIn("gemini-flash", provider_keys)
        self.assertIn("groq", provider_keys)

    @patch('llm.router.GeminiAdapter.generate')
    @patch('llm.router.GroqAdapter.generate')
    def test_manual_provider_selection_override(self, mock_groq_gen, mock_gemini_gen):
        mock_gemini_gen.return_value = {
            "type": "text",
            "text": "Planner response",
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20
        }
        mock_groq_gen.return_value = {
            "type": "text",
            "text": "Manual Groq response",
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20
        }
        from rest_framework.test import APIClient
        client = APIClient()
        response = client.post('/api/chat/', {
            "message": "Critical analytical architecture design request needing intense reasoning",
            "selected_provider": "groq"
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["selected_provider"], "groq")
        self.assertEqual(response.data["selected_model"], "mixtral-8x7b-32768")








