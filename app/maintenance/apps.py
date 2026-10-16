from django.apps import AppConfig


class MaintenanceConfig(AppConfig):
    name = 'maintenance'
    verbose_name = 'Health & Maintenance'

    def ready(self):
        from maintenance import services  # noqa: F401
