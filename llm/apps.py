from django.apps import AppConfig


class LlmConfig(AppConfig):
    name = 'llm'

    def ready(self):
        try:
            from llm.tracing import init_tracer
            init_tracer()
        except Exception:
            pass
