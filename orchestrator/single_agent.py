from django.utils import timezone
from core.agents.research_agent import ResearchAgent
from core.agents.job_reasoning_agent import JobReasoningAgent
from core.llm_providers.gemini_api import GeminiAPIProvider
from core.tools.registry import ToolRegistry
from core.tools.implementations.file_tool import FileTool
from core.tools.implementations.github_tool import (
    GitHubSearchCodeTool,
    GitHubReadFileTool,
    GitHubWriteFileTool,
    GitHubCreatePRTool
)
from core.tools.implementations.browser_tool import BrowserTool
from core.tools.implementations.vision_tool import AnalyzeScreenshotTool
from memory.models import Conversation, Message, AgentRun

class SingleAgentOrchestrator:
    """
    Phase 1 Orchestrator: Routes requests directly to the selected agent.
    Handles memory persistence.
    """
    
    def __init__(self):
        self.provider = GeminiAPIProvider(
            api_key="AQ.Ab8RN6Iv9rXDPDXtsa4xv69CfapI_zWl3uGCUe-n3qmzZ0xt4Q",
            model="gemma-4-26b-a4b-it"
        )
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(FileTool())
        self.tool_registry.register(GitHubSearchCodeTool())
        self.tool_registry.register(GitHubReadFileTool())
        self.tool_registry.register(GitHubWriteFileTool())
        self.tool_registry.register(GitHubCreatePRTool())
        self.tool_registry.register(BrowserTool())
        self.tool_registry.register(AnalyzeScreenshotTool())
        
        self.research_agent = ResearchAgent(
            provider=self.provider,
            tool_registry=self.tool_registry
        )
        self.job_reasoning_agent = JobReasoningAgent(
            provider=self.provider,
            tool_registry=self.tool_registry
        )

    def handle_request(self, user_profile, conversation_id, message_text: str, agent_type: str = "ResearchAgent") -> dict:
        agent = self.research_agent
        if agent_type == "JobReasoningAgent":
            agent = self.job_reasoning_agent

        if conversation_id:
            conversation = Conversation.objects.get(id=conversation_id, user_profile=user_profile)
        else:
            conversation = Conversation.objects.create(user_profile=user_profile)
            
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
            from memory.models import ToolExecution
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
            response_text = agent.execute(
                prompt=message_text,
                conversation_history=history,
                on_tool_execution=log_tool_execution,
                user_profile_data=user_profile_data
            )
            
            Message.objects.create(
                conversation=conversation,
                role='assistant',
                content=response_text
            )
            
            # Save token usage and cost metrics
            prompt_tokens = getattr(agent, "accumulated_prompt_tokens", 0)
            completion_tokens = getattr(agent, "accumulated_completion_tokens", 0)
            # Gemini 2.5 Flash Pricing: $0.075 / 1M input tokens, $0.30 / 1M output tokens
            from decimal import Decimal
            total_cost = (Decimal(prompt_tokens) * Decimal("0.075") + Decimal(completion_tokens) * Decimal("0.30")) / Decimal("1000000")
            
            agent_run.prompt_tokens = prompt_tokens
            agent_run.completion_tokens = completion_tokens
            agent_run.total_cost = total_cost
            agent_run.status = 'completed'
            agent_run.completed_at = timezone.now()
            agent_run.save()
            
            return {
                "conversation_id": str(conversation.id),
                "response": response_text
            }
            
        except Exception as e:
            prompt_tokens = getattr(agent, "accumulated_prompt_tokens", 0)
            completion_tokens = getattr(agent, "accumulated_completion_tokens", 0)
            from decimal import Decimal
            total_cost = (Decimal(prompt_tokens) * Decimal("0.075") + Decimal(completion_tokens) * Decimal("0.30")) / Decimal("1000000")
            
            agent_run.prompt_tokens = prompt_tokens
            agent_run.completion_tokens = completion_tokens
            agent_run.total_cost = total_cost
            agent_run.status = 'failed'
            agent_run.completed_at = timezone.now()
            agent_run.save()
            raise e
