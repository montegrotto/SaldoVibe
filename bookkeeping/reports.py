"""Context builders for the balance sheet, income statement, general ledger,
reskontra, and system documentation."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from auditlog.models import AuditLogEntry

from .models import (
    Account,
    AccountClass,
    AccountingYear,
    BudgetLine,
    JournalEntry,
)
from .year_end import year_end_voucher


def default_accounting_year(years):
    """Året som innehåller dagens datum, annars det senast påbörjade.

    Ett i förväg upplagt framtida räkenskapsår får inte gömma årets bokföring
    bakom en tom default på varje lista/rapport."""
    today = timezone.localdate()
    return (
        years.filter(start_date__lte=today, end_date__gte=today).order_by("-start_date", "-id").first()
        or years.order_by("-start_date", "-id").first()
    )


def get_year_context(request, company):
    years = AccountingYear.objects.filter(company=company).order_by("-start_date", "-id")
    selected_year = None
    year_id = request.GET.get("year")
    if year_id:
        selected_year = years.filter(pk=year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(years)
    return years, selected_year


def transaction_list_account_url_prefix(selected_year=None):
    base_url = reverse("bookkeeping:transaction_list")
    if selected_year is None:
        return f"{base_url}?account="
    return f"{base_url}?year={selected_year.pk}&account="


def build_account_drilldown_map(entries_qs):
    drilldown_map = defaultdict(list)
    entries_qs = entries_qs.order_by("-transaction__date", "-transaction__voucher_number", "id")
    for entry in entries_qs.select_related("transaction"):
        transaction = entry.transaction
        drilldown_map[entry.account_id].append(
            {
                "date": transaction.date,
                "reference": transaction.reference or f"{transaction.voucher_series}{transaction.voucher_number or ''}",
                "description": transaction.description,
                "entry_description": entry.description,
                "debit": entry.debit,
                "credit": entry.credit,
                "net_amount": (entry.debit or Decimal("0")) - (entry.credit or Decimal("0")),
            }
        )
    return drilldown_map


def build_balance_sheet_context(request, company):
    years, selected_year = get_year_context(request, company)

    def get_account_balances(account_class_list, *, number_startswith=None):
        accounts = Account.objects.filter(
            company=company,
            account_class__in=account_class_list,
            is_active=True,
        )
        if number_startswith is not None:
            if isinstance(number_startswith, tuple):
                number_filter = Q()
                for prefix in number_startswith:
                    number_filter |= Q(number__startswith=prefix)
                accounts = accounts.filter(number_filter)
            else:
                accounts = accounts.filter(number__startswith=number_startswith)
        result = []
        for acc in accounts:
            entries_qs = JournalEntry.objects.filter(account=acc)
            if selected_year:
                entries_qs = entries_qs.filter(transaction__date__lte=selected_year.end_date)
            entries = entries_qs.aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
            d = entries["total_debit"] or Decimal("0")
            c = entries["total_credit"] or Decimal("0")
            balance = d - c
            if balance != Decimal("0"):
                result.append({"account": acc, "balance": balance})
        return result

    def get_balances_for_prefixes(prefixes):
        if not prefixes:
            return []
        if len(prefixes) == 1:
            return get_account_balances(["2"], number_startswith=prefixes[0])
        return get_account_balances(["2"], number_startswith=tuple(prefixes))

    def sum_balances(rows):
        return sum((row["balance"] for row in rows), Decimal("0"))

    assets = get_account_balances(["1"])
    equity = get_balances_for_prefixes(["20"])
    untaxed_reserves = get_balances_for_prefixes(["21"])
    provisions = get_balances_for_prefixes(["22"])
    long_term_liabilities = get_balances_for_prefixes(["23"])
    current_liabilities = get_balances_for_prefixes(["24", "25", "26", "27", "28", "29"])
    liabilities = untaxed_reserves + provisions + long_term_liabilities + current_liabilities

    balance_sheet_rows = assets + equity + untaxed_reserves + provisions + long_term_liabilities + current_liabilities
    balance_sheet_account_ids = [row["account"].pk for row in balance_sheet_rows]
    balance_sheet_entries = JournalEntry.objects.filter(
        account_id__in=balance_sheet_account_ids,
        transaction__accounting_year__company=company,
    )
    if selected_year:
        balance_sheet_entries = balance_sheet_entries.filter(transaction__accounting_year=selected_year)
        balance_sheet_entries = balance_sheet_entries.filter(transaction__date__lte=selected_year.end_date)
    balance_sheet_entries = balance_sheet_entries.filter(
        transaction__correction_of__isnull=True,
        transaction__corrections__isnull=True,
    )
    balance_sheet_drilldown_map = build_account_drilldown_map(balance_sheet_entries)
    for row in balance_sheet_rows:
        row["drilldown_entries"] = balance_sheet_drilldown_map.get(row["account"].pk, [])

    total_assets = sum_balances(assets)
    total_equity = sum_balances(equity)
    total_untaxed_reserves = sum_balances(untaxed_reserves)
    total_provisions = sum_balances(provisions)
    total_long_term_liabilities = sum_balances(long_term_liabilities)
    total_current_liabilities = sum_balances(current_liabilities)
    total_liabilities = (
        total_untaxed_reserves + total_provisions + total_long_term_liabilities + total_current_liabilities
    )
    total_equity_and_liabilities = -(total_equity + total_liabilities)
    balance_difference = total_assets - total_equity_and_liabilities

    context = {
        "assets": assets,
        "equity": equity,
        "untaxed_reserves": untaxed_reserves,
        "provisions": provisions,
        "long_term_liabilities": long_term_liabilities,
        "current_liabilities": current_liabilities,
        "liabilities": liabilities,
        "total_assets": total_assets,
        "total_equity": -total_equity,
        "total_untaxed_reserves": -total_untaxed_reserves,
        "total_provisions": -total_provisions,
        "total_long_term_liabilities": -total_long_term_liabilities,
        "total_current_liabilities": -total_current_liabilities,
        "total_liabilities": -total_liabilities,
        "total_equity_and_liabilities": total_equity_and_liabilities,
        "balance_difference": balance_difference,
        "years": years,
        "selected_year": selected_year,
        "transaction_list_account_url_prefix": transaction_list_account_url_prefix(selected_year),
    }
    return context


def build_income_statement_context(request, company):
    years, selected_year = get_year_context(request, company)

    def get_accounts_for_class(account_class):
        return Account.objects.filter(company=company, account_class=account_class, is_active=True)

    def get_amount_for_account(acc):
        entries_qs = JournalEntry.objects.filter(account=acc)
        if selected_year:
            entries_qs = entries_qs.filter(transaction__accounting_year=selected_year)
        entries = entries_qs.aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
        debit = entries["total_debit"] or Decimal("0")
        credit = entries["total_credit"] or Decimal("0")
        return credit - debit

    def get_budget_for_account(acc):
        """Sum of the account's budgeted months (budgets.BudgetLine), same signed
        convention as get_amount_for_account - see BudgetLine's docstring."""
        budget_qs = BudgetLine.objects.filter(account=acc)
        if selected_year:
            budget_qs = budget_qs.filter(accounting_year=selected_year)
        return budget_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    def section_rows(account_class):
        rows = []
        for acc in get_accounts_for_class(account_class):
            amount = get_amount_for_account(acc)
            budget_amount = get_budget_for_account(acc)
            if amount != Decimal("0") or budget_amount != Decimal("0"):
                rows.append(
                    {
                        "account": acc,
                        "amount": amount,
                        "budget_amount": budget_amount,
                        "budget_difference": amount - budget_amount,
                    }
                )
        return rows

    def budget_total(rows):
        return sum(row["budget_amount"] for row in rows)

    operating_income_rows = section_rows(AccountClass.REVENUE)
    raw_material_rows = section_rows(AccountClass.COST_OF_GOODS)
    external_cost_rows = section_rows(AccountClass.OTHER_EXTERNAL) + section_rows(AccountClass.OTHER_EXTERNAL_2)
    personnel_rows = section_rows(AccountClass.PERSONNEL)
    financial_rows = section_rows(AccountClass.FINANCIAL)
    results_and_tax_rows = section_rows(AccountClass.RESULTS_AND_TAX)
    year_end_rows = section_rows(AccountClass.YEAR_END)

    total_operating_income = sum(row["amount"] for row in operating_income_rows)
    total_operating_income_budget = budget_total(operating_income_rows)
    total_raw_material = sum(row["amount"] for row in raw_material_rows)
    total_raw_material_budget = budget_total(raw_material_rows)
    total_external_costs = sum(row["amount"] for row in external_cost_rows)
    total_external_costs_budget = budget_total(external_cost_rows)
    total_personnel_costs = sum(row["amount"] for row in personnel_rows)
    total_personnel_costs_budget = budget_total(personnel_rows)

    operating_result = total_operating_income + total_raw_material + total_external_costs + total_personnel_costs
    operating_result_budget = (
        total_operating_income_budget
        + total_raw_material_budget
        + total_external_costs_budget
        + total_personnel_costs_budget
    )

    total_financial = sum(row["amount"] for row in financial_rows)
    total_financial_budget = budget_total(financial_rows)
    result_after_financial = operating_result + total_financial
    result_after_financial_budget = operating_result_budget + total_financial_budget
    total_results_and_tax = sum(row["amount"] for row in results_and_tax_rows)
    total_results_and_tax_budget = budget_total(results_and_tax_rows)
    total_year_end = sum(row["amount"] for row in year_end_rows)
    total_year_end_budget = budget_total(year_end_rows)
    total_year_end_combined = total_results_and_tax + total_year_end
    total_year_end_combined_budget = total_results_and_tax_budget + total_year_end_budget
    annual_result = result_after_financial + total_year_end_combined
    annual_result_budget = result_after_financial_budget + total_year_end_combined_budget
    result_after_taxes = result_after_financial + total_year_end
    result_after_taxes_budget = result_after_financial_budget + total_year_end_budget

    income_statement_rows = (
        operating_income_rows
        + raw_material_rows
        + external_cost_rows
        + personnel_rows
        + financial_rows
        + results_and_tax_rows
        + year_end_rows
    )
    income_statement_account_ids = [row["account"].pk for row in income_statement_rows]
    income_statement_entries = JournalEntry.objects.filter(
        account_id__in=income_statement_account_ids,
        transaction__accounting_year__company=company,
    )
    if selected_year:
        income_statement_entries = income_statement_entries.filter(transaction__accounting_year=selected_year)
    income_statement_entries = income_statement_entries.filter(
        transaction__correction_of__isnull=True,
        transaction__corrections__isnull=True,
    )
    income_statement_drilldown_map = build_account_drilldown_map(income_statement_entries)
    for row in income_statement_rows:
        row["drilldown_entries"] = income_statement_drilldown_map.get(row["account"].pk, [])

    context = {
        "operating_income_rows": operating_income_rows,
        "raw_material_rows": raw_material_rows,
        "external_cost_rows": external_cost_rows,
        "personnel_rows": personnel_rows,
        "financial_rows": financial_rows,
        "results_and_tax_rows": results_and_tax_rows,
        "year_end_rows": year_end_rows,
        "total_operating_income": total_operating_income,
        "total_operating_income_budget": total_operating_income_budget,
        "total_operating_income_diff": total_operating_income - total_operating_income_budget,
        "total_raw_material": total_raw_material,
        "total_raw_material_budget": total_raw_material_budget,
        "total_raw_material_diff": total_raw_material - total_raw_material_budget,
        "total_external_costs": total_external_costs,
        "total_external_costs_budget": total_external_costs_budget,
        "total_external_costs_diff": total_external_costs - total_external_costs_budget,
        "total_personnel_costs": total_personnel_costs,
        "total_personnel_costs_budget": total_personnel_costs_budget,
        "total_personnel_costs_diff": total_personnel_costs - total_personnel_costs_budget,
        "operating_result": operating_result,
        "operating_result_budget": operating_result_budget,
        "operating_result_diff": operating_result - operating_result_budget,
        "total_financial": total_financial,
        "total_financial_budget": total_financial_budget,
        "total_financial_diff": total_financial - total_financial_budget,
        "result_after_financial": result_after_financial,
        "result_after_financial_budget": result_after_financial_budget,
        "result_after_financial_diff": result_after_financial - result_after_financial_budget,
        "total_results_and_tax": total_results_and_tax,
        "total_results_and_tax_budget": total_results_and_tax_budget,
        "total_results_and_tax_diff": total_results_and_tax - total_results_and_tax_budget,
        "total_year_end": total_year_end,
        "total_year_end_budget": total_year_end_budget,
        "total_year_end_diff": total_year_end - total_year_end_budget,
        "total_year_end_combined": total_year_end_combined,
        "total_year_end_combined_budget": total_year_end_combined_budget,
        "total_year_end_combined_diff": total_year_end_combined - total_year_end_combined_budget,
        "annual_result": annual_result,
        "annual_result_budget": annual_result_budget,
        "annual_result_diff": annual_result - annual_result_budget,
        # Avslutat år: 8999-posten driver resultatet till 0 - infotexten i templaten
        # förklarar nollan (se bokslutsflode-design.md, 8999-beslutet).
        "year_is_closed": bool(selected_year and year_end_voucher(selected_year)),
        "years": years,
        "selected_year": selected_year,
        "result_after_taxes": result_after_taxes,
        "result_after_taxes_budget": result_after_taxes_budget,
        "result_after_taxes_diff": result_after_taxes - result_after_taxes_budget,
        "transaction_list_account_url_prefix": transaction_list_account_url_prefix(selected_year),
        "has_budget_data": any(
            row["budget_amount"] != Decimal("0")
            for row in operating_income_rows
            + raw_material_rows
            + external_cost_rows
            + personnel_rows
            + financial_rows
            + results_and_tax_rows
            + year_end_rows
        ),
    }
    return context


def build_general_ledger_context(request, company):
    """Huvudbok: per konto ingående saldo (balanskonton), årets rader med löpande
    saldo, och utgående saldo. Alla verifikationer visas, även korrigeringspar —
    huvudboken ska summera exakt till balans-/resultaträkningens saldon."""
    from .sie import BALANCE_SHEET_ACCOUNT_CLASSES, _account_net_amounts_by_id

    years, selected_year = get_year_context(request, company)

    entries = JournalEntry.objects.filter(transaction__accounting_year__company=company)
    if selected_year:
        entries = entries.filter(transaction__accounting_year=selected_year)
    entries = entries.select_related("transaction").order_by(
        "transaction__date", "transaction__voucher_series", "transaction__voucher_number", "id"
    )

    rows_by_account = defaultdict(list)
    for entry in entries:
        txn = entry.transaction
        rows_by_account[entry.account_id].append(
            {
                "transaction_id": txn.pk,
                "date": txn.date,
                "voucher": f"{txn.voucher_series or ''}{txn.voucher_number or ''}",
                "description": entry.description or txn.description,
                "debit": entry.debit or Decimal("0"),
                "credit": entry.credit or Decimal("0"),
            }
        )

    opening_balances = {}
    if selected_year:
        balance_account_ids = list(
            Account.objects.filter(company=company, account_class__in=BALANCE_SHEET_ACCOUNT_CLASSES).values_list(
                "pk", flat=True
            )
        )
        opening_balances = _account_net_amounts_by_id(
            balance_account_ids,
            company=company,
            upto_date=selected_year.start_date - timedelta(days=1),
        )
        opening_balances = {pk: amount for pk, amount in opening_balances.items() if amount != Decimal("0")}

    ledger_accounts = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    account_ids = set(rows_by_account) | set(opening_balances)
    for account in Account.objects.filter(pk__in=account_ids).order_by("number"):
        opening_balance = opening_balances.get(account.pk, Decimal("0"))
        balance = opening_balance
        account_debit = Decimal("0")
        account_credit = Decimal("0")
        rows = rows_by_account.get(account.pk, [])
        for row in rows:
            balance += row["debit"] - row["credit"]
            row["balance"] = balance
            account_debit += row["debit"]
            account_credit += row["credit"]
        ledger_accounts.append(
            {
                "account": account,
                "opening_balance": opening_balance,
                "rows": rows,
                "total_debit": account_debit,
                "total_credit": account_credit,
                "closing_balance": balance,
            }
        )
        total_debit += account_debit
        total_credit += account_credit

    return {
        "ledger_accounts": ledger_accounts,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "years": years,
        "selected_year": selected_year,
    }


RESKONTRA_AGING_LABELS = ("Ej förfallet", "1–30 dagar", "31–60 dagar", "61–90 dagar", "Över 90 dagar")


def _reversal_date(payment):
    """The date a reversed payment left the books, or date.max while it still stands."""
    if payment.reversed_at is None:
        return date.max
    if payment.reversal_transaction is not None:
        return payment.reversal_transaction.date
    return timezone.localdate(payment.reversed_at)


def _reskontra_aging_index(days_overdue):
    if days_overdue <= 0:
        return 0
    if days_overdue <= 30:
        return 1
    if days_overdue <= 60:
        return 2
    if days_overdue <= 90:
        return 3
    return 4


def _build_reskontra_side(*, company, open_documents, booked_account_ids, counterparty_attr, sign, report_date):
    """One side of the reskontra: documents open on report_date with aging, plus
    the avstämning against the ledger balance of the reskontrakonton the booked
    documents use, both cut off at report_date.

    sign: +1 for kundfordringar (debit balance is positive), -1 for
    leverantörsskulder (credit balance shown as a positive skuld).
    """
    from .sie import _account_net_amounts_by_id

    rows = []
    buckets = [{"label": label, "total": Decimal("0.00")} for label in RESKONTRA_AGING_LABELS]
    reskontra_total = Decimal("0.00")
    for document in open_documents:
        total = document.total_amount or Decimal("0")
        if total == Decimal("0"):
            continue
        # Remaining as of report_date: settled_total minus the payments (incl.
        # öresavskrivningar) dated on or before it. A reversed payment still counts
        # on dates before its reversal voucher's date — the ledger side cuts off on
        # voucher dates, so the avstämning would otherwise break for historical dates.
        settled = sum(
            (
                payment.amount + payment.write_off_amount
                for payment in document.payments.all()
                if payment.payment_date <= report_date and _reversal_date(payment) > report_date
            ),
            Decimal("0.00"),
        )
        remaining_magnitude = document.settled_total - settled
        if remaining_magnitude <= Decimal("0.00"):
            continue
        # The magnitude is signed here; credit invoices reduce the reskontra.
        remaining = remaining_magnitude if total > 0 else -remaining_magnitude
        days_overdue = (report_date - document.due_date).days if document.due_date else 0
        buckets[_reskontra_aging_index(days_overdue)]["total"] += remaining
        reskontra_total += remaining
        rows.append(
            {
                "document": document,
                "counterparty": getattr(document, counterparty_attr),
                "due_date": document.due_date,
                "days_overdue": max(days_overdue, 0),
                "is_overdue": days_overdue > 0,
                "remaining": remaining,
            }
        )
    rows.sort(key=lambda row: (row["due_date"] or report_date, row["document"].pk))

    account_ids = [pk for pk in set(booked_account_ids) if pk is not None]
    ledger_accounts = list(Account.objects.filter(pk__in=account_ids).order_by("number"))
    ledger_total = sign * sum(
        _account_net_amounts_by_id(account_ids, company=company, upto_date=report_date).values(), Decimal("0")
    )

    return {
        "rows": rows,
        "buckets": buckets,
        "reskontra_total": reskontra_total,
        "ledger_accounts": ledger_accounts,
        "ledger_total": ledger_total,
        "difference": reskontra_total - ledger_total,
    }


def build_reskontra_context(company, report_date=None):
    """Kund- och leverantörsreskontra per valfritt datum (default idag): fakturor
    som var obetalda per datumet, med åldersanalys och avstämning mot
    huvudbokssaldot på reskontrakontona per samma datum.

    Bokföringsverifikationen för båda dokumenttyperna dateras invoice_date, så
    filtret invoice_date <= report_date matchar huvudbokssidan i avstämningen.
    """
    from invoicing.models import Invoice
    from supplier_invoices.models import SupplierInvoice

    if report_date is None:
        report_date = timezone.localdate()
    customer_side = _build_reskontra_side(
        company=company,
        open_documents=(
            Invoice.objects.filter(company=company, is_booked=True, invoice_date__lte=report_date)
            .select_related("customer")
            .prefetch_related("lines", "payments__reversal_transaction")
        ),
        booked_account_ids=Invoice.objects.filter(company=company, is_booked=True).values_list(
            "receivable_account_id", flat=True
        ),
        counterparty_attr="customer",
        sign=1,
        report_date=report_date,
    )
    supplier_side = _build_reskontra_side(
        company=company,
        open_documents=(
            SupplierInvoice.objects.filter(company=company, is_registered=True, invoice_date__lte=report_date)
            .select_related("supplier")
            .prefetch_related("payments__reversal_transaction")
        ),
        booked_account_ids=SupplierInvoice.objects.filter(company=company, is_registered=True).values_list(
            "payable_account_id", flat=True
        ),
        counterparty_attr="supplier",
        sign=-1,
        report_date=report_date,
    )
    return {
        "report_date": report_date,
        "customer_side": customer_side,
        "supplier_side": supplier_side,
    }


def build_system_documentation_context(company):
    """Systemdokumentation per BFNAR 2013:2 kap. 9. Kontoplan and voucher-series
    rows are per-company and generated live; the rest (samlingsplan,
    behandlingsregler, informationsflöden, m.m.) describes SaldoVibe itself and
    is the same for every company, since it's a property of the software, not
    of the bookkeeping data."""

    accounts = company.accounts.filter(is_active=True).order_by("number")

    voucher_series_rows = []
    for rule in company.voucher_series_rules.order_by("source"):
        last_change = (
            AuditLogEntry.objects.filter(model_label="bookkeeping.voucherseriesrule", object_pk=str(rule.pk))
            .order_by("-occurred_at")
            .first()
        )
        voucher_series_rows.append(
            {
                "source_display": rule.get_source_display(),
                "series_code": rule.series_code,
                "last_changed_at": last_change.occurred_at if last_change else None,
                "last_changed_by": last_change.actor_display if last_change else "",
            }
        )

    accounting_years = company.accounting_years.order_by("start_date")

    return {
        "company": company,
        "generated_at": timezone.now(),
        "accounts": accounts,
        "voucher_series_rows": voucher_series_rows,
        "accounting_years": accounting_years,
    }
