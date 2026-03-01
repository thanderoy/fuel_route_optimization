import os
import sys

from django.apps import AppConfig


class RoutesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "routes"

    def ready(self):
        # Skip index build during management commands where it's unnecessary
        # (migrate, test, makemigrations, etc.) to avoid noisy errors.
        running_management = any(
            cmd in sys.argv
            for cmd in ("migrate", "makemigrations", "test", "collectstatic", "check")
        )
        if running_management:
            return

        # Also skip in auto-reloader child process (index is built by parent)
        if os.environ.get("RUN_MAIN") != "true":
            return

        import threading

        from .services import build_station_index

        threading.Thread(target=build_station_index, daemon=True).start()
