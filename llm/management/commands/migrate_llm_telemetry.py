from django.core.management.base import BaseCommand
from llm.models import PromptRun

class Command(BaseCommand):
    help = 'Migrates historical LLM telemetry PromptRun records from default database to telemetry database.'

    def handle(self, *args, **options):
        self.stdout.write("Checking for historical PromptRun records on 'default' database...")
        
        try:
            default_count = PromptRun.objects.using('default').count()
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not read default PromptRun table (it may not exist or has been removed): {e}"))
            return

        self.stdout.write(f"Found {default_count} historical records in the 'default' database.")
        
        if default_count == 0:
            self.stdout.write("No historical records to migrate.")
            return

        migrated_count = 0
        skipped_count = 0
        
        for run in PromptRun.objects.using('default').iterator():
            # Check if it already exists in telemetry DB (idempotence)
            exists = PromptRun.objects.using('telemetry').filter(id=run.id).exists()
            if exists:
                skipped_count += 1
                continue
            
            # Reconstruct the record on telemetry database, preserving all fields
            PromptRun.objects.using('telemetry').create(
                id=run.id,
                purpose=run.purpose,
                prompt_text=run.prompt_text,
                response_text=run.response_text,
                model_name=run.model_name,
                temperature=run.temperature,
                tokens_used=run.tokens_used,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                cost_usd=run.cost_usd,
                latency_ms=run.latency_ms,
                ats_score=run.ats_score,
                created_at=run.created_at,
                correlation_id=run.correlation_id,
                trace_id=run.trace_id,
                span_id=run.span_id,
                operation=run.operation,
                prompt_version=run.prompt_version,
                prompt_key=run.prompt_key,
                template_variables=run.template_variables,
                rendered_prompt=run.rendered_prompt,
                provider=run.provider,
                model=run.model,
                input_cost=run.input_cost,
                output_cost=run.output_cost,
                total_cost=run.total_cost,
                duration_ms=run.duration_ms,
                status=run.status,
                error_type=run.error_type,
                error_code=run.error_code,
                error_message=run.error_message,
                provider_status_code=run.provider_status_code,
                retry_count=run.retry_count,
                max_tokens=run.max_tokens,
                metadata=run.metadata,
                started_at=run.started_at,
                completed_at=run.completed_at
            )
            migrated_count += 1
            
        self.stdout.write(self.style.SUCCESS(f"Successfully migrated {migrated_count} records. (Skipped {skipped_count} duplicates)"))
