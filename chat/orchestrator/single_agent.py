from django.utils import timezone
from chat.agents.research_agent import ResearchAgent
from chat.agents.job_reasoning_agent import JobReasoningAgent
from llm.router import IntelligentRouter
from llm.tools.registry import ToolRegistry
from llm.tools.implementations.file_tool import FileTool
from llm.tools.implementations.github_tool import (
    GitHubSearchCodeTool,
    GitHubReadFileTool,
    GitHubWriteFileTool,
    GitHubCreatePRTool
)
from llm.tools.implementations.browser_tool import BrowserTool
from llm.tools.implementations.vision_tool import AnalyzeScreenshotTool
from llm.tools.implementations.discovery_tools import (
    SearchCompaniesTool,
    SearchWebTool,
    CrawlWebsiteTool,
    ExtractContactDataTool
)
from chat.models import Conversation, Message, AgentRun

class SingleAgentOrchestrator:
    """
    Phase 1 Orchestrator: Routes requests directly to the selected agent.
    Handles memory persistence.
    """
    
    def __init__(self):
        self.provider = IntelligentRouter()
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(FileTool())
        self.tool_registry.register(GitHubSearchCodeTool())
        self.tool_registry.register(GitHubReadFileTool())
        self.tool_registry.register(GitHubWriteFileTool())
        self.tool_registry.register(GitHubCreatePRTool())
        self.tool_registry.register(BrowserTool())
        self.tool_registry.register(AnalyzeScreenshotTool())
        self.tool_registry.register(SearchCompaniesTool())
        self.tool_registry.register(SearchWebTool())
        self.tool_registry.register(CrawlWebsiteTool())
        self.tool_registry.register(ExtractContactDataTool())
        
        self.research_agent = ResearchAgent(
            provider=self.provider,
            tool_registry=self.tool_registry
        )
        self.job_reasoning_agent = JobReasoningAgent(
            provider=self.provider,
            tool_registry=self.tool_registry
        )

    def handle_request(self, user_profile, conversation_id, message_text: str, agent_type: str = "ResearchAgent", selected_provider: str = None) -> dict:
        agent = self.research_agent
        if agent_type == "JobReasoningAgent":
            agent = self.job_reasoning_agent

        if conversation_id:
            conversation = Conversation.objects.get(id=conversation_id, user_profile=user_profile)
        else:
            conversation = Conversation.objects.create(user_profile=user_profile)
            
        # Handle explicit provider override if specified by user (not 'auto')
        if selected_provider and selected_provider.strip().lower() != "auto":
            prov_key = selected_provider.strip().lower()
            adapter = self.provider.adapters.get(prov_key)
            if adapter:
                conversation.selected_provider = prov_key
                conversation.selected_model = getattr(adapter, "model_name", prov_key)
                conversation.save(update_fields=['selected_provider', 'selected_model'])
            
        self.provider.set_active_conversation(str(conversation.id))
            
        if message_text:
            if not conversation.title:
                conversation.title = message_text[:50] + ("..." if len(message_text) > 50 else "")
                conversation.save(update_fields=['title'])
            Message.objects.create(
                conversation=conversation,
                role='user',
                content=message_text
            )
        
        agent_run = AgentRun.objects.create(
            conversation=conversation,
            agent_type=agent.name,
            status='running'
        )
        
        history = list(conversation.messages.order_by('created_at').values('role', 'content'))
        
        # Callback to log tool executions to the database
        def log_tool_execution(tool_name: str, tool_args: dict, tool_result: str, status: str):
            from chat.models import ToolExecution
            ToolExecution.objects.create(
                agent_run=agent_run,
                tool_name=tool_name,
                input_data=tool_args,
                output_data={"result": str(tool_result)[:10000]},  # Limit output size in DB
                status=status
            )
        
        # Extract user profile data to pass to the agent
        user_profile_data = {
            "full_name": user_profile.full_name or "Shivam Singh",
            "email": user_profile.email or "shivam@example.com",
            "phone": user_profile.phone or "+1-555-0199",
            "linkedin_url": user_profile.linkedin_url or "https://linkedin.com/in/shivam",
            "github_url": user_profile.github_url or "https://github.com/Ingenuity07",
            "portfolio_url": user_profile.portfolio_url or "https://shivam.dev"
        }
        
        try:
            from chat.agents.checkpoint_saver import DjangoCheckpointSaver
            from chat.agents.v2_graph import get_v2_agent_graph
            
            saver = DjangoCheckpointSaver()
            graph = get_v2_agent_graph(checkpoint_saver=saver)
            
            config = {"configurable": {"thread_id": str(conversation.id)}}
            
            messages_list = []
            for h in history:
                messages_list.append({"role": h["role"], "content": h["content"]})
                
            checkpoint_tuple = saver.get_tuple(config)
            
            initial_state = {
                "messages": messages_list,
                "plan": [],
                "step_index": 0,
                "scraped_data": "",
                "customized_resume_path": "",
                "screenshot_name": "",
                "human_approved": False,
                "status": "Searching",
                "user_profile_data": user_profile_data,
                "agent_memories": [],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "agent_run_id": agent_run.id,
                "conversation_id": str(conversation.id),
                "retry_count": 0,
                "error": None
            }
            
            # If checkpoint exists, we pass a message update if present, otherwise pass None to resume
            if checkpoint_tuple:
                if message_text:
                    resumed_state = graph.invoke({"messages": [{"role": "user", "content": message_text}]}, config)
                else:
                    resumed_state = graph.invoke(None, config)
            else:
                resumed_state = graph.invoke(initial_state, config)
                
            if resumed_state.get("error"):
                raise Exception(resumed_state["error"])

            # Extract response text (last assistant message)
            response_text = ""
            for m in reversed(resumed_state.get("messages", [])):
                if m.get("role") == "assistant":
                    response_text = m.get("content") or ""
                    break
                    
            if not response_text:
                response_text = f"Agent status is currently: {resumed_state.get('status')}"
            
            conversation.refresh_from_db()
            prompt_tokens = resumed_state.get("prompt_tokens", 0)
            completion_tokens = resumed_state.get("completion_tokens", 0)
            total_tokens = prompt_tokens + completion_tokens
            
            Message.objects.create(
                conversation=conversation,
                role='assistant',
                content=response_text,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                provider=conversation.selected_provider,
                model=conversation.selected_model
            )
            
            # Save token usage and cost metrics
            # Gemini 2.5 Flash Pricing: $0.075 / 1M input tokens, $0.30 / 1M output tokens
            from decimal import Decimal
            total_cost = (Decimal(prompt_tokens) * Decimal("0.075") + Decimal(completion_tokens) * Decimal("0.30")) / Decimal("1000000")
            
            agent_run.prompt_tokens = prompt_tokens
            agent_run.completion_tokens = completion_tokens
            agent_run.total_cost = total_cost
            
            if resumed_state.get("status") == "Complete":
                agent_run.status = 'completed'
            else:
                # If waiting approval or still running plan
                agent_run.status = 'completed'  # complete this task turn
                
            agent_run.completed_at = timezone.now()
            agent_run.save()
            
            return {
                "conversation_id": str(conversation.id),
                "response": response_text,
                "selected_provider": conversation.selected_provider,
                "selected_model": conversation.selected_model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
            
        except Exception as e:
            # Fallback to local variables or graph state values if defined
            try:
                prompt_tokens = resumed_state.get("prompt_tokens", 0)
                completion_tokens = resumed_state.get("completion_tokens", 0)
            except NameError:
                prompt_tokens = 0
                completion_tokens = 0
            from decimal import Decimal
            total_cost = (Decimal(prompt_tokens) * Decimal("0.075") + Decimal(completion_tokens) * Decimal("0.30")) / Decimal("1000000")
            
            agent_run.prompt_tokens = prompt_tokens
            agent_run.completion_tokens = completion_tokens
            agent_run.total_cost = total_cost
            agent_run.status = 'failed'
            agent_run.completed_at = timezone.now()
            agent_run.save()
            raise e
