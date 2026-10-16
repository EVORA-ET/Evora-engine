from django.apps import AppConfig


class EvMigrationConfig(AppConfig):
    name = 'ev_migration'
    verbose_name = 'EV Migration'

    def ready(self):
        # Registers the method versions used to reproduce assessments.
        from ev_migration import services  # noqa: F401
