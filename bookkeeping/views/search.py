"""Global sök: ett fält i sidhuvudet som slår på verifikationer, fakturor, utlägg och motparter
i det aktiva företaget. Exakt beloppsträff när söksträngen är ett tal, annars textträff."""

import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from expenses.models import ExpenseClaim
from invoicing.models import Customer, Invoice
from supplier_invoices.models import Supplier, SupplierInvoice

from ..models import Transaction
from ._base import company_required

LIMIT = 20  # ponytail: per grupp, ingen paginering – förfina söksträngen i stället
_VOUCHER_RE = re.compile(r"^([A-Za-zÅÄÖåäö]{1,3})\s?(\d+)$")


def parse_amount(text):
    """'1 234,50' -> Decimal('1234.50'); None om texten inte är ett tal."""
    cleaned = text.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def search_company(company, q):
    amount = parse_amount(q)
    voucher = _VOUCHER_RE.match(q.strip())

    tx_filter = Q(description__icontains=q) | Q(reference__icontains=q) | Q(entries__description__icontains=q)
    if amount is not None:
        tx_filter |= Q(entries__debit=amount) | Q(entries__credit=amount)
    if voucher:
        tx_filter |= Q(voucher_series__iexact=voucher.group(1), voucher_number=int(voucher.group(2)))
    transactions = (
        Transaction.objects.filter(accounting_year__company=company)
        .filter(tx_filter)
        .distinct()
        .order_by("-date", "-id")[:LIMIT]
    )

    invoices = (
        Invoice.objects.filter(company=company)
        .filter(
            Q(invoice_number__icontains=q)
            | Q(ocr_code__icontains=q)
            | Q(reference__icontains=q)
            | Q(customer__name__icontains=q)
        )
        .select_related("customer")
        .order_by("-invoice_date", "-id")[:LIMIT]
    )

    supplier_filter = Q(invoice_number__icontains=q) | Q(ocr_code__icontains=q) | Q(supplier_name__icontains=q)
    expense_filter = Q(description__icontains=q) | Q(person_name__icontains=q)
    if amount is not None:
        supplier_filter |= Q(total_amount=amount)
        expense_filter |= Q(total_amount=amount)
    supplier_invoices = (
        SupplierInvoice.objects.filter(company=company).filter(supplier_filter).order_by("-invoice_date", "-id")[:LIMIT]
    )
    expenses = (
        ExpenseClaim.objects.filter(company=company).filter(expense_filter).order_by("-expense_date", "-id")[:LIMIT]
    )

    customers = Customer.objects.filter(company=company).filter(Q(name__icontains=q) | Q(org_number__icontains=q))[
        :LIMIT
    ]
    suppliers = Supplier.objects.filter(company=company, name__icontains=q)[:LIMIT]

    return {
        "transactions": transactions,
        "invoices": invoices,
        "supplier_invoices": supplier_invoices,
        "expenses": expenses,
        "customers": customers,
        "suppliers": suppliers,
    }


@login_required
@company_required
def search(request, company):
    q = request.GET.get("q", "").strip()
    results = search_company(company, q) if q else {}
    return render(
        request,
        "bookkeeping/search_results.html",
        {"q": q, "results": results, "has_hits": any(results.values()), "limit": LIMIT},
    )
