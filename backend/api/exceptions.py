import logging

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger("medlab")


def medlab_exception_handler(exc, context):
    if isinstance(exc, RequestDataTooBig):
        return Response(
            {
                "success": False,
                "message": "So'rov tanasi juda katta (413).",
            },
            status=413,
        )
    if isinstance(exc, Throttled):
        wait = getattr(exc, "wait", None)
        msg = "So'rovlar juda tez. "
        if wait is not None:
            msg += f"Taxminan {int(wait)} s kuting."
        else:
            msg += "Birozdan keyin qayta urining."
        return Response(
            {"success": False, "message": msg, "throttled": True},
            status=429,
        )
    response = drf_exception_handler(exc, context)
    if response is None:
        log.exception("Kutilmagan API xato: %s", exc)
        msg = str(exc) if settings.DEBUG else (
            "Serverda ichki xato. Administratorga murojaat qiling yoki keyinroq qayta urining."
        )
        return Response({"success": False, "message": msg}, status=500)
    return response
