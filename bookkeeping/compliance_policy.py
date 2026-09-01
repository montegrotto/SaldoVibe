from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

ROLE_GROUPS = {
    "finance_operator": {"finance_operator", "finance-admin", "finance_admin", "system_admin"},
    "finance_admin": {"finance-admin", "finance_admin", "system_admin"},
    "system_admin": {"system_admin"},
}

ACTION_ROLE_MATRIX = {
    "period_lock.manage": "finance_admin",
    "accounting_year.delete": "finance_admin",
    "accounting_year.close": "finance_admin",
    "company.delete": "finance_admin",
    "export.sru": "finance_admin",
    "export.sie4": "finance_admin",
    "import.sie": "finance_admin",
    "vat.close_period": "finance_admin",
    "export.bundle": "finance_admin",
    "payroll.report_mark": "finance_admin",
    "audit.verify_chain": "finance_admin",
    "gdpr.erase": "finance_admin",
    "restore.dry_run": "system_admin",
    "voucher_series.manage": "finance_admin",
}


def _user_group_names(user):
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.values_list("name", flat=True))


def has_compliance_role(user, role_name):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    if role_name in {"finance_operator", "finance_admin"} and user.is_staff:
        return True

    allowed_groups = ROLE_GROUPS.get(role_name, set())
    return bool(_user_group_names(user).intersection(allowed_groups))


def is_action_allowed(user, action_key):
    role_name = ACTION_ROLE_MATRIX.get(action_key)
    if role_name is None:
        return False
    return has_compliance_role(user, role_name)


def require_compliance_action(action_key, redirect_name="bookkeeping:dashboard"):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if is_action_allowed(request.user, action_key):
                return view_func(request, *args, **kwargs)
            messages.error(request, "Du saknar behörighet för denna compliance-åtgärd.")
            return redirect(redirect_name)

        return wrapper

    return decorator
