"""Token authentication and the decorator every mobile-API endpoint is built on."""

import functools
import json

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse, QueryDict
from django.views.decorators.csrf import csrf_exempt

from accounts.models import ApiToken
from auditlog.context import audit_user
from bookkeeping.company_scope import get_user_companies, is_read_only_member


class ApiError(Exception):
    def __init__(self, message, status=400, errors=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors


def error_response(message, status=400, errors=None):
    payload = {"error": message}
    if errors:
        payload["errors"] = errors
    return JsonResponse(payload, status=status)


def form_error(form):
    """ApiError carrying the form's first message plus the full per-field dict."""
    errors = {field: [str(message) for message in messages] for field, messages in form.errors.items()}
    return ApiError(next(iter(errors.values()))[0], errors=errors)


def json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except ValueError:
        raise ApiError("Ogiltig JSON.") from None
    if not isinstance(data, dict):
        raise ApiError("Ett JSON-objekt förväntades.")
    return data


def form_data(data):
    """A JSON object as the QueryDict the web forms expect (normalize_decimal_fields calls
    getlist). null drops the key, booleans become "true"/"false" for CheckboxInput."""
    query = QueryDict(mutable=True)
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, list):
            query.setlist(key, [str(item) for item in value])
        elif isinstance(value, bool):
            query[key] = "true" if value else "false"
        else:
            query[key] = str(value)
    return query


def _bearer_token(request):
    scheme, _, raw_token = request.headers.get("Authorization", "").partition(" ")
    return raw_token.strip() if scheme.lower() == "bearer" else ""


def _company_from_header(request, user):
    raw_id = request.headers.get("X-Company-Id", "")
    if not raw_id.isdigit():
        raise ApiError("Ange företag i X-Company-Id.", 400)
    company = get_user_companies(user).filter(pk=int(raw_id)).first()
    if company is None:
        raise ApiError("Du har inte tillgång till företaget.", 403)
    return company


def api_view(methods=("GET",), *, auth=True, company=True):
    """JSON endpoint. Authenticates `Authorization: Bearer <token>` (accounts.ApiToken),
    resolves the company from `X-Company-Id` and passes it as the view's second argument,
    binds the token's user for the audit log, and turns ApiError/ValidationError/Http404
    into `{"error": ...}`. Writes are refused for read-only members, as on the web.
    Token calls carry no session cookie, so there is nothing for CSRF to protect."""

    def decorator(view):
        @csrf_exempt
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return error_response("Metoden stöds inte.", 405)
            try:
                if not auth:
                    return view(request, *args, **kwargs)
                token = ApiToken.resolve(_bearer_token(request))
                if token is None:
                    return error_response("Ogiltig eller återkallad inloggning.", 401)
                request.user = token.user
                request.api_token = token
                with audit_user(token.user):
                    if not company:
                        return view(request, *args, **kwargs)
                    active_company = _company_from_header(request, token.user)
                    if request.method != "GET" and is_read_only_member(token.user, active_company):
                        return error_response("Du har bara läsbehörighet i det här företaget.", 403)
                    return view(request, active_company, *args, **kwargs)
            except ApiError as exc:
                return error_response(exc.message, exc.status, exc.errors)
            except ValidationError as exc:
                return error_response(" ".join(exc.messages), 400)
            except Http404:
                return error_response("Hittades inte.", 404)

        return wrapper

    return decorator
