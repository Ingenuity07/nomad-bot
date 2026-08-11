from typing import TypedDict, List, Dict, Any, Optional
import json
from langgraph.graph import StateGraph, START, END
from llm.gemini_api import GeminiAPIProvider
from llm.tools.registry import ToolRegistry
from chat.agents.planner import PlannerAgent
import logging

logger = logging.getLogger(__name__)

class AgentGraphState(TypedDict):
    """LangGraph execution state representing the current run status."""
    messages: List[Dict[str, Any]]
    plan: List[str]
    step_index: int
    scraped_data: str
    customized_resume_path: str
    screenshot_name: str
    human_approved: bool
    status: str
    user_profile_data: Dict[str, Any]
    agent_memories: List[str]
    prompt_tokens: int
    completion_tokens: int
    agent_run_id: Optional[int]
    conversation_id: str
    retry_count: int
    error: Optional[str]


def stream_agent_update(conversation_id: str, event_type: str, data: dict):
    """Sends a real-time event update to the WebSocket channel layer group for the conversation."""
    if not conversation_id:
        return
    try:
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        if channel_layer:
            payload = {
                "type": "chat_message",
                "event_type": event_type,
                "data": data
            }
            
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Active event loop detected (e.g. async tests). Run via background task.
                loop.create_task(channel_layer.group_send(f"chat_{conversation_id}", payload))
            else:
                # Sync context (e.g. views, celery tasks). Run via async_to_sync.
                from asgiref.sync import async_to_sync
                async_to_sync(channel_layer.group_send)(f"chat_{conversation_id}", payload)
            
            logger.info(f"Streamed WebSocket event '{event_type}' to group chat_{conversation_id}")
    except Exception as e:
        logger.error(f"Failed to stream WebSocket event '{event_type}': {str(e)}")


def memory_injection_node(state: AgentGraphState) -> Dict[str, Any]:
    """Retrieves persistent memories for the user and injects them into the state."""
    username = state.get("user_profile_data", {}).get("username")
    if not username:
        return {"agent_memories": []}

    logger.info(f"Retrieving memories for user {username}...")
    from chat.models import AgentMemory
    memory_objs = AgentMemory.objects.filter(user_profile__username=username)
    
    agent_memories = []
    for m in memory_objs:
        agent_memories.append(f"{m.category}.{m.key}: {m.value}")
        
    logger.info(f"Retrieved memories: {agent_memories}")
    return {"agent_memories": agent_memories}


def planner_node(state: AgentGraphState) -> Dict[str, Any]:
    """Generates the execution plan if not already created."""
    if state.get("plan"):
        return {}

    last_user_message = ""
    for msg in reversed(state["messages"]):
        if msg["role"] == "user":
            last_user_message = msg["content"]
            break

    # Bypass planner for simple greetings/short queries
    clean_msg = last_user_message.strip().lower()
    if clean_msg in ["hello", "hi", "hey", "test"] or (len(clean_msg) < 15 and not any(w in clean_msg for w in ["apply", "job", "resume", "lever", "greenhouse"])):
        logger.info("Generic/short query detected. Bypassing planner...")
        plan = ["general_task"]
        stream_agent_update(state.get("conversation_id"), "planner_plan", {
            "plan": plan,
            "message": "Generic/short query detected. Bypassing planner..."
        })
        return {
            "plan": plan,
            "step_index": 0,
            "status": "Searching"
        }

    logger.info("Initializing high-level plan...")
    stream_agent_update(state.get("conversation_id"), "planner_start", {
        "message": "Initializing high-level plan..."
    })
    
    from chat.orchestrator.single_agent import SingleAgentOrchestrator
    orchestrator = SingleAgentOrchestrator()
    orchestrator.provider.set_active_conversation(state.get("conversation_id"))
    planner = PlannerAgent(provider=orchestrator.provider)

    plan = planner.generate_plan(last_user_message)
    if not plan:
        plan = ["general_task"]
    logger.info(f"Generated Plan: {plan}")

    stream_agent_update(state.get("conversation_id"), "planner_plan", {
        "plan": plan,
        "message": f"Generated high-level plan with {len(plan)} steps."
    })

    return {
        "plan": plan,
        "step_index": 0,
        "status": "Searching"
    }


def executor_node(state: AgentGraphState) -> Dict[str, Any]:
    """Executes the current step in the plan using specialized agents."""
    plan = state["plan"]
    step_index = state["step_index"]
    current_goal = plan[step_index]

    if current_goal == "submit_application":
        # Let the dedicated submit_node handle this goal
        return {}

    logger.info(f"Executing step {step_index + 1}/{len(plan)}: {current_goal}")

    from chat.orchestrator.single_agent import SingleAgentOrchestrator
    orchestrator = SingleAgentOrchestrator()
    orchestrator.provider.set_active_conversation(state.get("conversation_id"))
    provider = orchestrator.provider
    tool_registry = orchestrator.tool_registry

    from chat.agents.research_agent import ResearchAgent
    from chat.agents.job_reasoning_agent import JobReasoningAgent

    memory_context = ""
    if state.get("agent_memories"):
        memory_context = f"User preference profile & rules:\n" + "\n".join(state["agent_memories"]) + "\n\n"

    status_map = {
        "search_jobs": "Searching",
        "scrape_job": "Ranking",
        "tailor_resume": "Resume",
        "fill_application": "Form Fill",
        "submit_application": "Submit"
    }
    status = status_map.get(current_goal, "Running")

    # Broadcast step start
    stream_agent_update(state.get("conversation_id"), "executor_step_start", {
        "goal": current_goal,
        "status": status,
        "step_index": step_index
    })

    # Adapt prompts for specialized executor agents
    if current_goal == "tailor_resume":
        agent = JobReasoningAgent(provider=provider, tool_registry=tool_registry)
        prompt = (
            f"{memory_context}"
            f"Tailor the base resume to match the scraped job description content: {state.get('scraped_data', '')}."
        )
    elif current_goal == "fill_application":
        agent = JobReasoningAgent(provider=provider, tool_registry=tool_registry)
        prompt = (
            f"{memory_context}"
            f"Fill out the job application form on the active browser page using the user profile: {state.get('user_profile_data', {})}. "
            "Make sure to take a screenshot named 'browser_screenshot.png' after filling it."
        )
    elif current_goal == "scrape_job":
        agent = ResearchAgent(provider=provider, tool_registry=tool_registry)
        prompt = (
            f"{memory_context}"
            "Look at the active page or search results, navigate to the specific job details URL, and scrape the full text content."
        )
    elif current_goal == "search_jobs":
        agent = ResearchAgent(provider=provider, tool_registry=tool_registry)
        last_user_msg = ""
        for m in reversed(state["messages"]):
            if m["role"] == "user":
                last_user_msg = m["content"]
                break
        prompt = f"{memory_context}Search for matching roles on job boards based on request: {last_user_msg}. Navigate to the listing."
    else:
        agent = ResearchAgent(provider=provider, tool_registry=tool_registry)
        prompt = f"{memory_context}Perform the high-level goal: {current_goal}."

    agent_run_id = state.get("agent_run_id")
    conversation_id = state.get("conversation_id")
    def log_tool_execution(tool_name: str, tool_args: dict, tool_result: str, status: str):
        if agent_run_id:
            from chat.models import ToolExecution, AgentRun
            run = AgentRun.objects.filter(id=agent_run_id).first()
            if run:
                ToolExecution.objects.create(
                    agent_run=run,
                    tool_name=tool_name,
                    input_data=tool_args,
                    output_data={"result": str(tool_result)[:10000]},
                    status=status
                )
        # Stream tool execution to WebSockets
        stream_agent_update(conversation_id, "tool_execution", {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": str(tool_result)[:1000],
            "status": status
        })

    try:
        response_text = agent.execute(prompt=prompt, on_tool_execution=log_tool_execution)
        
        updates = {
            "step_index": step_index + 1,
            "status": status,
            "messages": state["messages"] + [{"role": "assistant", "content": f"Completed {current_goal}: {response_text}"}],
            "prompt_tokens": state.get("prompt_tokens", 0) + getattr(agent, "accumulated_prompt_tokens", 0),
            "completion_tokens": state.get("completion_tokens", 0) + getattr(agent, "accumulated_completion_tokens", 0),
        }

        if current_goal == "scrape_job":
            updates["scraped_data"] = response_text
        elif current_goal == "tailor_resume":
            updates["customized_resume_path"] = "artifacts/customized_resume.md"
        elif current_goal == "fill_application":
            updates["screenshot_name"] = "browser_screenshot.png"

        stream_agent_update(conversation_id, "executor_step_end", {
            "goal": current_goal,
            "response": response_text,
            "status": status
        })

        return updates

    except BaseException as e:
        logger.error(f"Error executing step {current_goal}: {str(e)}", exc_info=True)
        stream_agent_update(conversation_id, "executor_step_failed", {
            "goal": current_goal,
            "error": str(e)
        })
        return {
            "error": str(e),
            "status": "Failed"
        }


def approval_wait_node(state: AgentGraphState) -> Dict[str, Any]:
    """Halt execution state to wait for user form approval."""
    logger.info("Halting execution for human approval...")
    stream_agent_update(state.get("conversation_id"), "approval_requested", {
        "message": "Form filled successfully. Halted waiting for human approval."
    })
    return {
        "status": "Waiting Approval"
    }


def submit_node(state: AgentGraphState) -> Dict[str, Any]:
    """Submits the form and updates run state to complete."""
    logger.info("Submitting application...")
    conversation_id = state.get("conversation_id")
    stream_agent_update(conversation_id, "submit_start", {
        "message": "Submitting job application form..."
    })

    from chat.orchestrator.single_agent import SingleAgentOrchestrator
    from chat.agents.research_agent import ResearchAgent
    orchestrator = SingleAgentOrchestrator()
    orchestrator.provider.set_active_conversation(conversation_id)
    agent = ResearchAgent(provider=orchestrator.provider, tool_registry=orchestrator.tool_registry)
    
    agent_run_id = state.get("agent_run_id")
    def log_tool_execution(tool_name: str, tool_args: dict, tool_result: str, status: str):
        if agent_run_id:
            from chat.models import ToolExecution, AgentRun
            run = AgentRun.objects.filter(id=agent_run_id).first()
            if run:
                ToolExecution.objects.create(
                    agent_run=run,
                    tool_name=tool_name,
                    input_data=tool_args,
                    output_data={"result": str(tool_result)[:10000]},
                    status=status
                )
        # Stream tool execution to WebSockets
        stream_agent_update(conversation_id, "tool_execution", {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": str(tool_result)[:1000],
            "status": status
        })

    try:
        # Click submit button
        res = agent.execute("Find the submit button on the page and click it to submit the application.", on_tool_execution=log_tool_execution)
        
        stream_agent_update(conversation_id, "submit_end", {
            "message": "Application submitted successfully!",
            "response": res
        })

        return {
            "step_index": state["step_index"] + 1,
            "status": "Complete",
            "messages": state["messages"] + [{"role": "assistant", "content": f"Form submitted: {res}"}],
            "prompt_tokens": state.get("prompt_tokens", 0) + getattr(agent, "accumulated_prompt_tokens", 0),
            "completion_tokens": state.get("completion_tokens", 0) + getattr(agent, "accumulated_completion_tokens", 0),
        }
    except BaseException as e:
        logger.error(f"Error during submit: {str(e)}", exc_info=True)
        stream_agent_update(conversation_id, "submit_failed", {
            "error": str(e)
        })
        return {
            "error": str(e),
            "status": "Failed"
        }


def memory_extraction_node(state: AgentGraphState) -> Dict[str, Any]:
    """Analyzes the message log to extract new user preferences/profile details and saves to DB."""
    username = state.get("user_profile_data", {}).get("username")
    if not username:
        return {}

    if state.get("plan") == ["general_task"]:
        return {}

    logger.info(f"Running memory extraction for user {username}...")
    from chat.orchestrator.single_agent import SingleAgentOrchestrator
    orchestrator = SingleAgentOrchestrator()
    orchestrator.provider.set_active_conversation(state.get("conversation_id"))
    provider = orchestrator.provider

    chat_log = []
    for m in state.get("messages", []):
        role = m.get("role")
        content = m.get("content") or ""
        chat_log.append(f"{role}: {content}")
        
    prompt = (
        "Analyze the following conversation logs. Extract any user preferences, experience profile details, or specific rules. "
        "For example:\n"
        "- Location preferences (e.g., remote only, New York)\n"
        "- Blocked/excluded companies (e.g., Facebook, Amazon)\n"
        "- Core tech stack skills (e.g., Python, React)\n\n"
        "Return a JSON object containing a list of objects under 'memories'. Each object must have:\n"
        "- 'category': String (e.g., 'preference', 'profile', 'experience')\n"
        "- 'key': String (e.g., 'blocked_companies', 'tech_stack', 'work_location')\n"
        "- 'value': JSON array or object containing the facts.\n\n"
        f"Conversation Logs:\n" + "\n".join(chat_log)
    )
    
    system_prompt = (
        "You are an assistant that extracts user preferences and profile facts to build a long-term memory. "
        "Return only JSON matching the schema: {\"memories\": [{\"category\": \"...\", \"key\": \"...\", \"value\": ...}]}"
    )

    try:
        response = provider.generate(prompt=prompt, system_prompt=system_prompt, tools=None)
        text = response.get("text", "")
        
        # Clean code wraps
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text.strip())
        memories = data.get("memories", [])
        
        from chat.models import AgentMemory
        from knowledge_base.models import UserProfile
        user_profile = UserProfile.objects.get(username=username)
        
        for m in memories:
            category = m.get("category", "").lower()
            key = m.get("key", "").lower()
            value = m.get("value")
            
            if category and key and value is not None:
                AgentMemory.objects.update_or_create(
                    user_profile=user_profile,
                    category=category,
                    key=key,
                    defaults={"value": value}
                )
                logger.info(f"Stored memory: {category}.{key} = {value}")
                
    except Exception as e:
        logger.error(f"Error extracting memories: {str(e)}")

    conversation_id = state.get("conversation_id")
    stream_agent_update(conversation_id, "run_completed", {
        "status": "Complete",
        "message": "Agent execution completed successfully!"
    })
    return {}


def critic_node(state: AgentGraphState) -> Dict[str, Any]:
    """Evaluates the latest executed plan step. Triggering retries if deficient."""
    if state.get("error"):
        return {}

    plan = state.get("plan", [])
    step_index = state.get("step_index", 0)
    
    # The step that just finished is at step_index - 1
    completed_idx = step_index - 1
    if completed_idx < 0 or completed_idx >= len(plan):
        return {}

    current_goal = plan[completed_idx]
    
    # Bypass critic for simple/fallback/general tasks
    if current_goal == "general_task":
        return {}

    # Extract latest assistant message content (the agent output for this step)
    latest_response = ""
    for m in reversed(state.get("messages", [])):
        if m.get("role") == "assistant" and f"Completed {current_goal}" in (m.get("content") or ""):
            latest_response = m.get("content")
            break

    if not latest_response:
        return {}

    conversation_id = state.get("conversation_id")
    logger.info(f"Running reflection critic for step: {current_goal}...")
    stream_agent_update(conversation_id, "critic_start", {
        "message": f"Critiquing output for step '{current_goal}'..."
    })

    from chat.orchestrator.single_agent import SingleAgentOrchestrator
    orchestrator = SingleAgentOrchestrator()
    orchestrator.provider.set_active_conversation(conversation_id)
    provider = orchestrator.provider

    prompt = (
        f"You are a strict quality controller. Evaluate if the following step execution was successful.\n\n"
        f"Step Name: {current_goal}\n"
        f"Execution Output: {latest_response}\n\n"
        f"Check for common errors:\n"
        f"- Empty content or placeholder text\n"
        f"- Out of memory or missing file errors\n"
        f"- Failure to complete the core action (e.g. form filling failed or resume was not tailored)\n\n"
        f"Provide a brief critique detailing any deficiencies. Return ONLY a JSON object matching:\n"
        f"{{\n"
        f"  \"success\": true/false,\n"
        f"  \"critique\": \"Reason for failure or confirmation of success\"\n"
        f"}}"
    )

    system_prompt = "You are a quality controller. Respond ONLY with valid JSON containing 'success' and 'critique' keys."

    try:
        response = provider.generate(prompt=prompt, system_prompt=system_prompt, tools=None)
        text = response.get("text", "")

        # Clean code wrap block backticks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text.strip())
        success = data.get("success", True)
        critique = data.get("critique", "Looks good.")
        
    except Exception as e:
        logger.error(f"Error executing critic LLM: {str(e)}")
        # Default to success to ensure robustness if Critic LLM crashes
        success = True
        critique = "Critic LLM error. Proceeding."

    retry_count = state.get("retry_count", 0)

    if success or retry_count >= 3:
        if not success:
            logger.warning(f"Step {current_goal} failed critic but reached max retries. Proceeding.")
            stream_agent_update(conversation_id, "critic_max_retries", {
                "message": f"Step '{current_goal}' failed critique but reached max retries. Proceeding.",
                "critique": critique
            })
        else:
            logger.info(f"Step {current_goal} passed critic.")
            stream_agent_update(conversation_id, "critic_pass", {
                "message": f"Step '{current_goal}' passed critique.",
                "critique": critique
            })
        
        # Reset retry count for next steps
        return {
            "retry_count": 0
        }
    else:
        # Critic failed and retries remain. Trigger execution node retry!
        logger.warning(f"Step {current_goal} failed critic. Retrying step. Retry count: {retry_count + 1}. Critique: {critique}")
        stream_agent_update(conversation_id, "critic_fail", {
            "message": f"Step '{current_goal}' failed critique. Retrying step...",
            "critique": critique,
            "retry_count": retry_count + 1
        })

        feedback_msg = {
            "role": "user",
            "content": f"CRITIQUE FOR STEP '{current_goal}': {critique}. Please execute the step again and address these points."
        }

        return {
            "step_index": completed_idx,  # Reset step index back to trigger retry on same goal
            "messages": state["messages"] + [feedback_msg],
            "retry_count": retry_count + 1
        }


def route_after_critic(state: AgentGraphState):
    """Determines transition path after critic node completes verification."""
    if state.get("error"):
        return "memory_extraction"

    plan = state.get("plan", [])
    step_index = state.get("step_index", 0)

    if step_index >= len(plan):
        return "memory_extraction"

    next_goal = plan[step_index]
    if next_goal == "submit_application":
        if not state.get("human_approved", False):
            return "approval_wait"
        else:
            return "submit"

    return "executor"


def get_v2_agent_graph(checkpoint_saver=None):
    """Compiles the V2 agent execution graph workflow."""
    workflow = StateGraph(AgentGraphState)

    # Register nodes
    workflow.add_node("memory_injection", memory_injection_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("approval_wait", approval_wait_node)
    workflow.add_node("submit", submit_node)
    workflow.add_node("memory_extraction", memory_extraction_node)

    # Register edges
    workflow.add_edge(START, "memory_injection")
    workflow.add_edge("memory_injection", "planner")
    workflow.add_edge("planner", "executor")
    workflow.add_edge("executor", "critic")
    
    # Register conditional edges
    workflow.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "executor": "executor",
            "approval_wait": "approval_wait",
            "submit": "submit",
            "memory_extraction": "memory_extraction"
        }
    )

    workflow.add_edge("approval_wait", END)
    workflow.add_edge("submit", "memory_extraction")
    workflow.add_edge("memory_extraction", END)

    return workflow.compile(checkpointer=checkpoint_saver)
