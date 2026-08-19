class LLMTelemetryRouter:
    """
    A router to control all database operations on models in the
    llm application to split telemetry and config models.
    """
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'llm' and model._meta.model_name == 'promptrun':
            return 'telemetry'
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'llm' and model._meta.model_name == 'promptrun':
            return 'telemetry'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._meta.app_label == 'llm' or obj2._meta.app_label == 'llm':
            db_set = {getattr(obj1, '_state', None) and obj1._state.db, getattr(obj2, '_state', None) and obj2._state.db}
            if 'telemetry' in db_set and 'default' in db_set:
                return False
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'llm':
            if model_name == 'promptrun':
                return db == 'telemetry'
            else:
                return db == 'default'
        if db == 'telemetry':
            return False
        return None
