import logging

from django.views.csrf import csrf_failure as default_csrf_failure

logger = logging.getLogger("django.security.csrf")


def csrf_failure(request, reason=""):
    logger.warning(
        "CSRF blocked: reason=%s origin=%s host=%s referer=%s path=%s",
        reason,
        request.META.get("HTTP_ORIGIN", ""),
        request.get_host(),
        request.META.get("HTTP_REFERER", ""),
        request.path,
    )
    return default_csrf_failure(request, reason=reason)
