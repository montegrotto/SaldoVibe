"""Views for the bookkeeping app.


Split out of a single 2700-line views.py. urls.py still does `from . import views`
and refers to `views.<name>`, so every public view is re-exported here; keep that
contract when adding one.
"""

from .accounts import (
    account_balances_lookup,
    account_create,
    account_list,
    account_suggest_codes,
    account_update,
    accounting_year_create,
    accounting_year_delete,
    accounting_year_list,
    accounting_year_update,
)
from .companies import (
    company_create,
    company_delete,
    company_list,
    company_update,
    no_company_access,
    select_company,
    switch_company,
)
from .dashboard import (
    compliance_dashboard,
    dashboard,
    liquidity_forecast_accounts,
    system_documentation,
    system_documentation_pdf,
    topbar_alert_status,
)
from .export import (
    export_bundle_delete,
    export_bundle_download,
    export_bundle_form,
    export_bundle_start,
)
from .help import (
    help_chapter,
    help_index,
)
from .imports import (
    si_import,
    si_import_diagnostics_download,
    sie_import,
    sie_import_diagnostics_download,
)
from .period_locks import (
    period_lock_create,
    period_lock_list,
    period_lock_lock_year,
    period_lock_relock,
    period_lock_reopen,
)
from .search import search
from .sru import (
    sru_download,
    sru_preflight_report_download,
    sru_report,
)
from .statements import (
    balance_sheet,
    balance_sheet_pdf,
    budget_edit,
    general_ledger,
    general_ledger_pdf,
    income_statement,
    income_statement_pdf,
    reskontra,
    reskontra_pdf,
)
from .transactions import (
    periodization_create,
    sie_export,
    transaction_add,
    transaction_attachment_add,
    transaction_attachment_remove,
    transaction_detail,
    transaction_list,
    transaction_reverse,
)
from .verification_templates import (
    verification_template_create,
    verification_template_delete,
    verification_template_library,
    verification_template_list,
    verification_template_update,
    voucher_series_settings,
)
from .year_end import (
    year_end_close,
)

__all__ = [
    "account_balances_lookup",
    "account_create",
    "account_list",
    "account_suggest_codes",
    "account_update",
    "accounting_year_create",
    "accounting_year_delete",
    "accounting_year_list",
    "accounting_year_update",
    "balance_sheet",
    "balance_sheet_pdf",
    "budget_edit",
    "company_create",
    "company_delete",
    "company_list",
    "search",
    "company_update",
    "compliance_dashboard",
    "dashboard",
    "export_bundle_delete",
    "export_bundle_download",
    "export_bundle_form",
    "export_bundle_start",
    "general_ledger",
    "general_ledger_pdf",
    "help_chapter",
    "help_index",
    "income_statement",
    "income_statement_pdf",
    "liquidity_forecast_accounts",
    "no_company_access",
    "period_lock_create",
    "period_lock_list",
    "period_lock_lock_year",
    "period_lock_relock",
    "period_lock_reopen",
    "periodization_create",
    "reskontra",
    "reskontra_pdf",
    "select_company",
    "si_import",
    "si_import_diagnostics_download",
    "sie_export",
    "sie_import",
    "sie_import_diagnostics_download",
    "sru_download",
    "sru_preflight_report_download",
    "sru_report",
    "switch_company",
    "system_documentation",
    "system_documentation_pdf",
    "topbar_alert_status",
    "transaction_add",
    "transaction_attachment_add",
    "transaction_attachment_remove",
    "transaction_detail",
    "transaction_list",
    "transaction_reverse",
    "verification_template_create",
    "verification_template_delete",
    "verification_template_library",
    "verification_template_list",
    "verification_template_update",
    "voucher_series_settings",
    "year_end_close",
]
