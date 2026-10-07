from django.apps import AppConfig


class ForgeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.forge'

    def ready(self):
        # Import signals so badge computation hooks up on Review save.
        # No transaction blocks: signal does single-row get_or_create only.
        from . import signals  # noqa: F401
