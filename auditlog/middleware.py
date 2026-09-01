from .context import audit_user


class AuditUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        with audit_user(user):
            return self.get_response(request)
