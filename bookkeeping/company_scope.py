import functools

from django.contrib import messages
from django.shortcuts import redirect

from .models import Company, CompanyMembership

SESSION_COMPANY_KEY = "active_company_id"
_DEFAULT_ERROR_MESSAGE = "Du har inte tillgång till något företag ännu. Kontakta administratör."


def get_user_companies(user):
    queryset = Company.objects.filter(is_active=True).order_by("name")
    if user.is_superuser:
        return queryset
    return queryset.filter(users=user)


def get_active_company(request):
    companies = get_user_companies(request.user)
    active_company = None

    active_company_id = request.session.get(SESSION_COMPANY_KEY)
    if active_company_id:
        active_company = companies.filter(pk=active_company_id).first()

    if active_company is None and companies.count() == 1:
        active_company = companies.first()
        request.session[SESSION_COMPANY_KEY] = active_company.pk

    return active_company


def require_active_company(
    request,
    *,
    error_message=_DEFAULT_ERROR_MESSAGE,
    logger=None,
    log_message=None,
    ensure_company=None,
):
    company = get_active_company(request)
    if company is None:
        if logger is not None and log_message:
            logger.warning(log_message, extra={"user_id": request.user.id})
        if error_message:
            messages.error(request, error_message)
        return None

    if ensure_company is not None:
        ensure_company(company)

    return company


def can_create_company(user):
    return user.has_perm("bookkeeping.add_company")


def redirect_missing_company(request):
    """Send a user without an active company to the right onboarding view.

    Users with several companies but none selected yet land on the company
    picker; users with no companies at all land on the create form (if
    allowed) or the "contact administrator" page.
    """
    if get_user_companies(request.user).exists():
        return redirect("bookkeeping:select_company")
    if can_create_company(request.user):
        messages.info(request, "Skapa ett företag för att komma igång.")
        return redirect("bookkeeping:company_create")
    return redirect("bookkeeping:no_company_access")


def require_company(
    view=None,
    *,
    error_message=_DEFAULT_ERROR_MESSAGE,
    logger=None,
    log_message=None,
    ensure_company=None,
):
    """View decorator that resolves the active company and passes it to the view.

    The wrapped view receives the company as its second positional argument:

        @login_required
        @require_company
        def my_view(request, company, ...):

    When no company is available the user is redirected via
    ``redirect_missing_company``. Keyword arguments mirror
    ``require_active_company``.
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            company = require_active_company(
                request,
                error_message=error_message if not can_create_company(request.user) else None,
                logger=logger,
                log_message=log_message,
                ensure_company=ensure_company,
            )
            if company is None:
                return redirect_missing_company(request)
            if request.method == "POST" and is_read_only_member(request.user, company):
                messages.error(request, "Du har bara läsbehörighet i det här företaget.")
                return redirect("bookkeeping:dashboard")
            return view_func(request, company, *args, **kwargs)

        return wrapper

    if view is not None:
        return decorator(view)
    return decorator


def set_active_company(request, company):
    request.session[SESSION_COMPANY_KEY] = company.pk


def can_access_company(user, company):
    if user.is_superuser:
        return True
    return company.users.filter(pk=user.pk).exists()


def is_read_only_member(user, company):
    if user.is_superuser:
        return False
    return CompanyMembership.objects.filter(company=company, user=user, role=CompanyMembership.Role.VIEWER).exists()


def can_edit_company(user, company):
    """Medlem med full behörighet (eller superuser) – får ändra företaget och dess användare."""
    if user.is_superuser:
        return True
    return CompanyMembership.objects.filter(company=company, user=user, role=CompanyMembership.Role.EDITOR).exists()
