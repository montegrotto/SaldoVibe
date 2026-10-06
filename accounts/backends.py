from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

MAX_LOGIN_FAILURES = 10
LOCKOUT_SECONDS = 15 * 60


class ThrottledModelBackend(ModelBackend):
    """ModelBackend that refuses an e-mail address for LOCKOUT_SECONDS after
    MAX_LOGIN_FAILURES wrong passwords, so the web login, the app's API login and the admin
    can't be used to guess passwords. A successful login resets the count."""

    # ponytail: the default LocMemCache counts per Gunicorn worker, so the real limit is
    # MAX_LOGIN_FAILURES × workers; point CACHES at a shared cache if that ever matters.

    def authenticate(self, request, username=None, password=None, **kwargs):
        key = f"login-failures:{str(username or kwargs.get('email') or '').strip().lower()}"
        if cache.get(key, 0) >= MAX_LOGIN_FAILURES:
            raise PermissionDenied
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            cache.add(key, 0, LOCKOUT_SECONDS)
            cache.incr(key)
        else:
            cache.delete(key)
        return user
