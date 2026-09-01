from contextlib import contextmanager
from contextvars import ContextVar

_current_user = ContextVar("auditlog_current_user", default=None)
_current_source = ContextVar("auditlog_current_source", default=None)


def get_current_user():
    return _current_user.get()


def get_current_source():
    return _current_source.get()


@contextmanager
def audit_user(user):
    token = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(token)


@contextmanager
def audit_source(source):
    token = _current_source.set(source)
    try:
        yield
    finally:
        _current_source.reset(token)
