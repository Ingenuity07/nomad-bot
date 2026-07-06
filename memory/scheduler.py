import json
from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
from memory.models import UserProfile

def create_interval_agent_task(name: str, username: str, prompt: str, interval_minutes: int, agent_type: str = "JobReasoningAgent") -> PeriodicTask:
    """
    Schedules an agent execution loop to run every N minutes.
    """
    # Verify user exists
    UserProfile.objects.get(username=username)
    
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=interval_minutes,
        period=IntervalSchedule.MINUTES,
    )
    
    # Task kwargs
    task_kwargs = {
        "username": username,
        "conversation_id": None,  # Will auto-create a new conversation on execution
        "message_text": prompt,
        "agent_type": agent_type
    }
    
    periodic_task = PeriodicTask.objects.create(
        interval=schedule,
        name=name,
        task="memory.tasks.run_agent_task",
        kwargs=json.dumps(task_kwargs)
    )
    return periodic_task

def create_cron_agent_task(name: str, username: str, prompt: str, cron_expression: str, agent_type: str = "JobReasoningAgent") -> PeriodicTask:
    """
    Schedules an agent execution loop using a cron expression.
    Cron expression format: 'minute hour day_of_week day_of_month month_of_year' (space separated)
    """
    UserProfile.objects.get(username=username)
    
    parts = cron_expression.split()
    if len(parts) != 5:
        raise ValueError("Cron expression must have exactly 5 space-separated parts.")
        
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=parts[0],
        hour=parts[1],
        day_of_week=parts[2],
        day_of_month=parts[3],
        month_of_year=parts[4]
    )
    
    task_kwargs = {
        "username": username,
        "conversation_id": None,
        "message_text": prompt,
        "agent_type": agent_type
    }
    
    periodic_task = PeriodicTask.objects.create(
        crontab=schedule,
        name=name,
        task="memory.tasks.run_agent_task",
        kwargs=json.dumps(task_kwargs)
    )
    return periodic_task

def list_schedules(username: str):
    """
    Lists all scheduled tasks for a given username.
    """
    tasks = PeriodicTask.objects.filter(task="memory.tasks.run_agent_task")
    user_tasks = []
    for t in tasks:
        try:
            kwargs = json.loads(t.kwargs or "{}")
            if kwargs.get("username") == username:
                user_tasks.append({
                    "id": t.id,
                    "name": t.name,
                    "prompt": kwargs.get("message_text"),
                    "agent_type": kwargs.get("agent_type"),
                    "enabled": t.enabled,
                    "last_run": t.last_run_at
                })
        except Exception:
            continue
    return user_tasks

def disable_schedule(task_name: str):
    """
    Disables a scheduled periodic task.
    """
    task = PeriodicTask.objects.get(name=task_name)
    task.enabled = False
    task.save()
    return task
