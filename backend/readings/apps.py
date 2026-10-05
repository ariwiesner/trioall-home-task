from django.apps import AppConfig


class ReadingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "readings"

    def ready(self):
        from . import signals  # noqa: F401 — registers the @receiver hooks
