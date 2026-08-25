from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        """Kitob indeksini fon oqimida oldindan yuklash (birinchi tahlil tez boshlansin)."""
        import os

        if os.environ.get("RUN_MAIN") == "false":
            return
        try:
            from lab_core.histology_kb import warm_index

            warm_index(background=True)
        except Exception:  # indeks yo'q bo'lsa tahlil baribir ishlaydi
            pass
