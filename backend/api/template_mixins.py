"""Django shablonlariga umumiy kontekst (prod: alohida API domeni)."""
from django.conf import settings


class MedlabPublicTemplateMixin:
    """index / login / register — MEDLAB_PUBLIC_API_BASE ni JS ga uzatadi."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["medlab_api_base"] = getattr(
            settings, "MEDLAB_PUBLIC_API_BASE", ""
        ) or ""
        return ctx
