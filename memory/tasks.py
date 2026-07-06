from celery import shared_task
from django.core.cache import cache
from memory.models import UserProfile
from orchestrator.single_agent import SingleAgentOrchestrator
import logging

logger = logging.getLogger(__name__)

@shared_task
def run_agent_task(username: str, conversation_id: str, message_text: str, agent_type: str = "ResearchAgent"):
    """
    Celery task to execute the agent reasoning loop asynchronously in the background.
    Protected with a Redis-backed distributed cache lock.
    """
    lock_key = f"lock:run_agent_task:{username}:{conversation_id or 'new'}"
    # Acquire lock with a 10-minute timeout (600s)
    lock_acquired = cache.add(lock_key, "locked", timeout=600)
    
    if not lock_acquired:
        logger.warning(f"Task already running for key {lock_key}. Skipping execution.")
        return {"status": "Skipped", "reason": "Task lock already held."}
        
    logger.info(f"Lock acquired for task: {lock_key}")
    logger.info(f"Starting async task for user '{username}', conversation '{conversation_id}', agent_type '{agent_type}'")
    
    try:
        user_profile = UserProfile.objects.get(username=username)
        orchestrator = SingleAgentOrchestrator()
        result = orchestrator.handle_request(
            user_profile=user_profile,
            conversation_id=conversation_id,
            message_text=message_text,
            agent_type=agent_type
        )
        logger.info(f"Async task completed successfully: {result}")
        return result
    except Exception as e:
        logger.error(f"Error running agent task: {str(e)}", exc_info=True)
        raise e
    finally:
        cache.delete(lock_key)
        logger.info(f"Lock released for task: {lock_key}")
