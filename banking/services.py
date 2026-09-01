"""Bank transaction booking, matching, and suggestion engine."""

from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from bookkeeping.balances import build_account_balances as _build_account_balances
from bookkeeping.models import Account, AccountClass, AccountingYear, JournalEntry, Transaction, TransactionSource
from bookkeeping.payables import AbstractPayment, add_journal_entry, reapply_payment_state

from .models import BankAccount, BankAccountType, BankProfile, BankTransaction


def ensure_tax_account_for_company(company):
    tax_gl_account, _ = company.accounts.get_or_create(
        number=BankAccount.TAX_BOOKKEEPING_ACCOUNT_NUMBER,
        defaults={
            "name": "Skattekonto",
            "account_class": AccountClass.ASSET,
            "is_active": True,
        },
    )

    tax_account, _ = BankAccount.objects.get_or_create(
        company=company,
        account_type=BankAccountType.TAX,
        defaults={
            "name": "Skattekonto",
            "account_number": BankAccount.TAX_BOOKKEEPING_ACCOUNT_NUMBER,
            "default_bank_profile": BankProfile.SKATTEVERKET,
            "bookkeeping_account": tax_gl_account,
            "is_active": True,
        },
    )

    changed = False
    if tax_account.bookkeeping_account_id != tax_gl_account.id:
        tax_account.bookkeeping_account = tax_gl_account
        changed = True
    if tax_account.default_bank_profile != BankProfile.SKATTEVERKET:
        tax_account.default_bank_profile = BankProfile.SKATTEVERKET
        changed = True
    if not tax_account.is_active:
        tax_account.is_active = True
        changed = True
    if changed:
        tax_account.full_clean()
        tax_account.save(update_fields=["bookkeeping_account", "default_bank_profile", "is_active"])

    return tax_account


def get_managed_bank_accounts(company):
    """Map bookkeeping Account id -> BankAccount for the company's active bank/tax connections.

    Used to warn when a user tries to book manually against an account (e.g. 1930, 1630)
    that a BankAccount already owns - such postings should normally go through the
    banking module instead, so its booked/linked bank transactions stay in sync with the
    ledger.
    """
    return {
        bank_account.bookkeeping_account_id: bank_account
        for bank_account in BankAccount.objects.filter(company=company, is_active=True).select_related(
            "bookkeeping_account"
        )
    }


def to_decimal(value):
    # Strip regular spaces AND non-breaking spaces (\xa0) used by Swedish locale grouping
    raw = str(value or "").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not raw:
        return Decimal("0")
    return Decimal(raw)


def build_account_balances(company, reference_date=None):
    return _build_account_balances(company, reference_date=reference_date)


def coerce_date(value):
    if hasattr(value, "year"):
        return value
    return timezone.datetime.fromisoformat(str(value)).date()


def find_account_for_quick_booking(*, company, account_number=None, name_contains=None):
    if account_number:
        by_number = company.accounts.filter(is_active=True, number=account_number).first()
        if by_number is not None:
            return by_number

    if name_contains:
        return company.accounts.filter(is_active=True, name__icontains=name_contains).order_by("number").first()

    return None


def get_quick_booking_suggestion(*, company, bank_tx):
    if bank_tx.bank_account.account_type == BankAccountType.TAX:
        return get_tax_account_quick_booking_suggestion(company=company, bank_tx=bank_tx)

    return get_bank_account_payment_suggestion(company=company, bank_tx=bank_tx)


def get_tax_account_quick_booking_suggestion(*, company, bank_tx):
    if bank_tx.bank_account.account_type != BankAccountType.TAX:
        return None

    description = (bank_tx.description or "").strip().casefold()

    quick_rules = [
        {
            "prefix": "korrigerad intäktsränta",
            "account_number": "8314",
            "name_contains": "Skattefria ränteintäkter",
            "rule_label": "Intäktsränta",
        },
        {
            "prefix": "intäktsränta",
            "account_number": "8314",
            "name_contains": "Skattefria ränteintäkter",
            "rule_label": "Intäktsränta",
        },
        {
            "prefix": "debiterad preliminärskatt",
            "account_number": "2510",
            "name_contains": "Skatteskulder",
            "rule_label": "Debiterad preliminärskatt",
        },
        {
            "prefix": "arbetsgivaravgift",
            "account_number": "2730",
            "name_contains": "Avräkning lagstadgade sociala avgifter",
            "rule_label": "Arbetsgivaravgift",
        },
        {
            "prefix": "avdragen skatt",
            "account_number": "2710",
            "name_contains": "Personalens källskatt",
            "rule_label": "Avdragen skatt",
        },
        {
            "prefix": "inbetalning bokförd",
            "account_number": "1930",
            "name_contains": "Företagskonto",
            "rule_label": "Inbetalning bokförd",
        },
        {
            "prefix": "moms",
            "account_number": "2650",
            "name_contains": "Redovisningskonto för moms",
            "rule_label": "Moms",
        },
    ]

    for rule in quick_rules:
        if not description.startswith(rule["prefix"]):
            continue

        account = find_account_for_quick_booking(
            company=company,
            account_number=rule["account_number"],
            name_contains=rule["name_contains"],
        )
        if account is None:
            return None

        return {
            "counter_account": account,
            "label": f"{account.number} {account.name}",
            "rule_label": rule["rule_label"],
        }

    return None


def amounts_equal(left, right):
    return (left or Decimal("0.00")).quantize(Decimal("0.01")) == (right or Decimal("0.00")).quantize(Decimal("0.01"))


def has_sufficient_amount(total_or_remaining, requested_amount):
    remaining = (total_or_remaining or Decimal("0.00")).quantize(Decimal("0.01"))
    requested = (requested_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    return remaining >= requested


def _opposite_side(side):
    return "credit" if side == "debit" else "debit"


def build_payable_booking_rows(*, payable, payment_amount, settlement_account, settlement_side):
    """Rows that clear a payable's balance when a payment settles it.

    A payment that falls just short of the remaining balance is topped up to the full
    amount and the shortfall written off as öresavrundning, so the document ends up
    fully settled. The write-off row always faces the settlement row, which is what
    keeps the verification balanced.

    Returns (rows, settlement_difference) where each row is
    (account, amount, "debit" | "credit").
    """
    payment_amount = (payment_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    settlement_difference = payable.get_payment_write_off_difference(payment_amount)

    rows = [(settlement_account, payment_amount + settlement_difference, settlement_side)]

    if settlement_difference > Decimal("0.00"):
        rounding_account = payable.get_payment_write_off_account()
        if rounding_account is None:
            raise ValueError("Standardkonto 3740 saknas i kontoplanen för att bokföra avrundningsdifferens.")
        rows.append((rounding_account, settlement_difference, _opposite_side(settlement_side)))

    return rows, settlement_difference


def build_customer_invoice_booking_rows(*, invoice, payment_amount):
    # An incoming payment credits the receivable; a credit invoice reverses that.
    expected_sign = expected_customer_payment_sign(invoice=invoice)
    return build_payable_booking_rows(
        payable=invoice,
        payment_amount=payment_amount,
        settlement_account=invoice.receivable_account,
        settlement_side="credit" if expected_sign > 0 else "debit",
    )


def build_supplier_invoice_booking_rows(*, invoice, payment_amount):
    # An outgoing payment debits the payable; a credit invoice reverses that.
    expected_sign = expected_supplier_payment_sign(invoice=invoice)
    return build_payable_booking_rows(
        payable=invoice,
        payment_amount=payment_amount,
        settlement_account=invoice.payable_account,
        settlement_side="debit" if expected_sign < 0 else "credit",
    )


def build_expense_claim_booking_rows(*, claim, payment_amount):
    # Utlägg är alltid en skuld till den som gjort utlägget: utbetalning debiterar skuldkontot.
    return build_payable_booking_rows(
        payable=claim,
        payment_amount=payment_amount,
        settlement_account=claim.liability_account,
        settlement_side="debit",
    )


def resolve_quick_booking_rows(*, company, suggestion, payment_amount):
    """Rows + settlement difference for a quick-booking suggestion's source document.

    Re-fetches the referenced payable with the same "still open" filters the suggestion
    used, so a document paid in the meantime raises ValueError (with the user-facing
    message) instead of being settled twice. Falls back to the plain counter-account
    row when the suggestion carries no payable, or the payable lacks its settlement
    account.
    """
    rows = [(suggestion["counter_account"], payment_amount)]
    settlement_difference = Decimal("0.00")
    source_type = suggestion.get("source_type")
    source_id = suggestion.get("source_id")

    if source_type == "customer_invoice":
        from invoicing.models import Invoice

        invoice = (
            Invoice.objects.filter(
                pk=source_id,
                company=company,
                is_booked=True,
                booked_transaction__isnull=False,
                is_paid=False,
            )
            .select_related("receivable_account")
            .prefetch_related("lines")
            .first()
        )
        if invoice is None:
            raise ValueError("Kundfakturan är inte längre valbar för betalning.")
        if invoice.receivable_account is not None:
            rows, settlement_difference = build_customer_invoice_booking_rows(
                invoice=invoice, payment_amount=payment_amount
            )
    elif source_type == "supplier_invoice":
        from supplier_invoices.models import SupplierInvoice

        invoice = (
            SupplierInvoice.objects.filter(
                pk=source_id,
                company=company,
                is_registered=True,
                registered_transaction__isnull=False,
                is_paid=False,
            )
            .select_related("payable_account")
            .first()
        )
        if invoice is None:
            raise ValueError("Leverantörsfakturan är inte längre valbar för betalning.")
        if invoice.payable_account is not None:
            rows, settlement_difference = build_supplier_invoice_booking_rows(
                invoice=invoice, payment_amount=payment_amount
            )
    elif source_type == "expense_claim":
        from expenses.models import ExpenseClaim

        claim = (
            ExpenseClaim.objects.filter(
                pk=source_id,
                company=company,
                is_registered=True,
                registered_transaction__isnull=False,
                is_paid=False,
            )
            .select_related("liability_account")
            .first()
        )
        if claim is None:
            raise ValueError("Utlägget är inte längre valbart för utbetalning.")
        rows, settlement_difference = build_expense_claim_booking_rows(claim=claim, payment_amount=payment_amount)
    elif source_type == "payroll_run":
        from payroll.models import PayrollRun

        run = PayrollRun.objects.filter(
            pk=source_id,
            company=company,
            is_finished=True,
            booking_transaction__isnull=False,
        ).first()
        if run is None:
            raise ValueError("Lönekörningen är inte längre valbar för betalning.")

    return rows, settlement_difference


def build_candidate_suggestion(*, counter_account, rule_label, date_distance, priority):
    return {
        "counter_account": counter_account,
        "label": f"{counter_account.number} {counter_account.name}",
        "rule_label": rule_label,
        "_date_distance": date_distance,
        "_priority": priority,
    }


def pick_best_candidate(candidates):
    if not candidates:
        return None

    best = min(candidates, key=lambda item: (item["_date_distance"], item["_priority"], item["rule_label"]))
    return {
        "counter_account": best["counter_account"],
        "label": best["label"],
        "rule_label": best["rule_label"],
        "source_type": best.get("source_type"),
        "source_id": best.get("source_id"),
    }


def get_manual_booking_invoice_options(*, company, bank_tx):
    signed_amount = to_decimal(bank_tx.amount)
    amount = abs(signed_amount)
    if amount <= Decimal("0.00"):
        return {"customer_invoice": [], "supplier_invoice": [], "expense_claim": [], "payroll_run": []}

    tx_date = coerce_date(bank_tx.date)

    from invoicing.models import Invoice
    from supplier_invoices.models import SupplierInvoice

    customer_options = []
    invoices = (
        Invoice.objects.filter(
            company=company,
            is_booked=True,
            booked_transaction__isnull=False,
            is_paid=False,
        )
        .select_related("customer", "receivable_account")
        .prefetch_related("lines")
        .order_by("due_date", "id")
    )
    fallback_receivable = company.accounts.filter(is_active=True, number="1510").first()
    for invoice in invoices:
        counter_account = invoice.receivable_account or fallback_receivable
        if counter_account is None:
            continue
        invoice_label = invoice.invoice_number or f"{invoice.pk}"
        remaining = invoice.remaining_amount
        customer_options.append(
            {
                "id": str(invoice.pk),
                "counter_account": counter_account,
                "account_label": f"{counter_account.number} {counter_account.name}",
                "label": f"{invoice_label} · {invoice.customer.name} · förfallo {invoice.due_date} · återstår {remaining:.2f} kr",
                "remaining": f"{remaining:.2f}",
                "direction": str(expected_customer_payment_sign(invoice=invoice)),
                "source_type": "customer_invoice",
                "source_id": invoice.pk,
                "date_distance": abs((invoice.due_date - tx_date).days),
            }
        )

    supplier_options = []
    supplier_invoices = (
        SupplierInvoice.objects.filter(
            company=company,
            is_registered=True,
            registered_transaction__isnull=False,
            is_paid=False,
        )
        .select_related("supplier", "payable_account")
        .order_by("due_date", "id")
    )
    for invoice in supplier_invoices:
        if invoice.payable_account is None:
            continue
        invoice_label = invoice.invoice_number or f"{invoice.pk}"
        supplier_name = invoice.supplier.name if invoice.supplier_id else invoice.supplier_name
        remaining = invoice.remaining_amount
        supplier_options.append(
            {
                "id": str(invoice.pk),
                "counter_account": invoice.payable_account,
                "account_label": f"{invoice.payable_account.number} {invoice.payable_account.name}",
                "label": f"{invoice_label} · {supplier_name} · förfallo {invoice.due_date} · återstår {remaining:.2f} kr",
                "remaining": f"{remaining:.2f}",
                "direction": str(expected_supplier_payment_sign(invoice=invoice)),
                "source_type": "supplier_invoice",
                "source_id": invoice.pk,
                "date_distance": abs((invoice.due_date - tx_date).days),
            }
        )

    from expenses.models import ExpenseClaim
    from payroll.models import PayrollRun

    expense_options = []
    claims = (
        ExpenseClaim.objects.filter(
            company=company,
            is_registered=True,
            registered_transaction__isnull=False,
            is_paid=False,
        )
        .select_related("employee", "liability_account")
        .order_by("expense_date", "id")
    )
    for claim in claims:
        remaining = claim.remaining_amount
        expense_options.append(
            {
                "id": str(claim.pk),
                "counter_account": claim.liability_account,
                "account_label": f"{claim.liability_account.number} {claim.liability_account.name}",
                "label": f"{claim.description} · {claim.person_display_name} · {claim.expense_date} · återstår {remaining:.2f} kr",
                "remaining": f"{remaining:.2f}",
                "direction": "-1",
                "source_type": "expense_claim",
                "source_id": claim.pk,
                "date_distance": abs((claim.expense_date - tx_date).days),
            }
        )

    payroll_options = []
    salary_account = company.accounts.filter(is_active=True, number=PayrollRun.SALARY_LIABILITY_ACCOUNT_NUMBER).first()
    if salary_account is not None:
        payroll_runs = PayrollRun.objects.filter(
            company=company,
            is_finished=True,
            booking_transaction__isnull=False,
        ).order_by("-payment_date", "-id")
        for run in payroll_runs:
            remaining = run.salary_payment_remaining()
            if remaining <= Decimal("0.00"):
                continue
            payroll_options.append(
                {
                    "id": str(run.pk),
                    "counter_account": salary_account,
                    "account_label": f"{salary_account.number} {salary_account.name}",
                    "label": f"Lön {run.period_label} · utbetalningsdatum {run.payment_date} · återstår {remaining:.2f} kr",
                    "remaining": f"{remaining:.2f}",
                    "direction": "-1",
                    "source_type": "payroll_run",
                    "source_id": run.pk,
                    "date_distance": abs((run.payment_date - tx_date).days),
                }
            )

    customer_options.sort(key=lambda item: (item["date_distance"], item["label"]))
    supplier_options.sort(key=lambda item: (item["date_distance"], item["label"]))
    expense_options.sort(key=lambda item: (item["date_distance"], item["label"]))
    payroll_options.sort(key=lambda item: (item["date_distance"], item["label"]))
    return {
        "customer_invoice": customer_options,
        "supplier_invoice": supplier_options,
        "expense_claim": expense_options,
        "payroll_run": payroll_options,
    }


class InvoiceAllocationError(ValueError):
    """En betalningsallokering mot en faktura kunde inte genomföras."""


def _allocation_failed(message, strict):
    if strict:
        raise InvoiceAllocationError(message)
    return False


def _settle_payable_from_bank_booking(
    *,
    payable,
    payment_model,
    bank_tx,
    booked_transaction,
    booked_amount,
    settlement_difference,
    strict,
    update_fields,
):
    if payable is None or payable.is_paid:
        return _allocation_failed("Posten är inte längre valbar för betalning.", strict)

    if not has_sufficient_amount(payable.remaining_amount, booked_amount):
        return _allocation_failed("Beloppet är större än återstående belopp på posten.", strict)

    new_paid_amount = (payable.paid_amount + booked_amount).quantize(Decimal("0.01"))
    effective_paid_amount = (new_paid_amount + settlement_difference).quantize(Decimal("0.01"))
    total_amount = abs(payable.total_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    if effective_paid_amount > total_amount:
        return _allocation_failed("Betalningen skulle överstiga fakturans totalbelopp.", strict)

    is_fully_paid = amounts_equal(effective_paid_amount, total_amount)
    payable.paid_amount = new_paid_amount
    payable.is_paid = is_fully_paid
    payable.paid_at = timezone.now() if is_fully_paid else None
    payable.payment_date = bank_tx.date
    payable.payment_account = bank_tx.bank_account.bookkeeping_account
    payable.payment_transaction = booked_transaction
    payable.save(update_fields=update_fields)

    payment_model.objects.create(
        payable=payable,
        transaction=booked_transaction,
        amount=booked_amount,
        write_off_amount=settlement_difference,
        payment_date=bank_tx.date,
        payment_account=bank_tx.bank_account.bookkeeping_account,
    )
    return True


def apply_invoice_payment_allocation(*, allocation, bank_tx, booked_transaction):
    """Register one (partial) payment against an invoice. Must run inside the booking's atomic block.

    Allocation keys: source_type, source_id, amount, settlement_difference, strict.
    With strict=True a failed allocation raises InvoiceAllocationError (rolling back the booking);
    with strict=False (best-effort match from manual booking) it is skipped silently.
    """
    source_type = allocation.get("source_type")
    invoice_id = allocation.get("source_id")
    strict = allocation.get("strict", True)
    if not invoice_id or source_type not in {"customer_invoice", "supplier_invoice", "expense_claim", "payroll_run"}:
        return False

    booked_amount = (allocation.get("amount") or abs(bank_tx.amount) or Decimal("0.00")).quantize(Decimal("0.01"))
    settlement_difference = (allocation.get("settlement_difference") or Decimal("0.00")).quantize(Decimal("0.01"))
    if booked_amount <= Decimal("0.00"):
        return _allocation_failed("Betalningsbeloppet måste vara större än 0.", strict)

    if source_type == "payroll_run":
        from payroll.models import PayrollRun

        run = (
            PayrollRun.objects.select_for_update()
            .filter(
                pk=invoice_id,
                company=bank_tx.company,
                is_finished=True,
                booking_transaction__isnull=False,
            )
            .first()
        )
        if run is None:
            return _allocation_failed("Lönekörningen är inte längre valbar för betalning.", strict)
        if not has_sufficient_amount(run.salary_payment_remaining(), booked_amount):
            return _allocation_failed("Beloppet är större än återstående nettolön för lönekörningen.", strict)

        run.paid_amount = ((run.paid_amount or Decimal("0.00")) + booked_amount).quantize(Decimal("0.01"))
        run.save(update_fields=["paid_amount"])
        return True

    if source_type == "expense_claim":
        from expenses.models import ExpenseClaim, ExpenseClaimPayment

        claim = (
            ExpenseClaim.objects.select_for_update()
            .filter(
                pk=invoice_id,
                company=bank_tx.company,
                is_registered=True,
                registered_transaction__isnull=False,
            )
            .first()
        )
        return _settle_payable_from_bank_booking(
            payable=claim,
            payment_model=ExpenseClaimPayment,
            bank_tx=bank_tx,
            booked_transaction=booked_transaction,
            booked_amount=booked_amount,
            settlement_difference=settlement_difference,
            strict=strict,
            update_fields=[
                "paid_amount",
                "is_paid",
                "paid_at",
                "payment_date",
                "payment_account",
                "payment_transaction",
                "updated_at",
            ],
        )

    if source_type == "supplier_invoice":
        from supplier_invoices.models import SupplierInvoice, SupplierInvoicePayment

        invoice = (
            SupplierInvoice.objects.select_for_update()
            .filter(
                pk=invoice_id,
                company=bank_tx.company,
                is_registered=True,
                registered_transaction__isnull=False,
            )
            .first()
        )
        return _settle_payable_from_bank_booking(
            payable=invoice,
            payment_model=SupplierInvoicePayment,
            bank_tx=bank_tx,
            booked_transaction=booked_transaction,
            booked_amount=booked_amount,
            settlement_difference=settlement_difference,
            strict=strict,
            update_fields=[
                "paid_amount",
                "is_paid",
                "paid_at",
                "payment_date",
                "payment_account",
                "payment_transaction",
                "updated_at",
            ],
        )

    from invoicing.models import Invoice, InvoicePayment

    invoice = (
        Invoice.objects.select_for_update()
        .filter(
            pk=invoice_id,
            company=bank_tx.company,
            is_booked=True,
            booked_transaction__isnull=False,
        )
        .first()
    )
    return _settle_payable_from_bank_booking(
        payable=invoice,
        payment_model=InvoicePayment,
        bank_tx=bank_tx,
        booked_transaction=booked_transaction,
        booked_amount=booked_amount,
        settlement_difference=settlement_difference,
        strict=strict,
        update_fields=[
            "paid_amount",
            "is_paid",
            "paid_at",
            "payment_date",
            "payment_account",
            "payment_transaction",
        ],
    )


def book_bank_transaction(*, bank_tx, rows, user, allocations=None, description=None):
    """Book the bank transaction and settle matched invoices in one atomic unit."""
    with db_transaction.atomic():
        booked_txn = book_bank_transaction_rows(bank_tx=bank_tx, rows=rows, user=user, description=description)
        for allocation in allocations or []:
            apply_invoice_payment_allocation(
                allocation=allocation,
                bank_tx=bank_tx,
                booked_transaction=booked_txn,
            )
    return booked_txn


def sign_of(value):
    if value > Decimal("0.00"):
        return 1
    if value < Decimal("0.00"):
        return -1
    return 0


def expected_customer_payment_sign(*, invoice):
    # +1 => expected incoming payment, -1 => expected outgoing payment (credit invoice)
    if not invoice.booked_transaction_id:
        return 1

    receivable_account = invoice.receivable_account
    if receivable_account is None:
        receivable_account = invoice.company.accounts.filter(is_active=True, number="1510").first()
    if receivable_account is None:
        return 1

    entry_totals = JournalEntry.objects.filter(
        transaction_id=invoice.booked_transaction_id,
        account_id=receivable_account.id,
    ).aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    net = (entry_totals["total_debit"] or Decimal("0.00")) - (entry_totals["total_credit"] or Decimal("0.00"))
    net_sign = sign_of(net)
    return 1 if net_sign == 0 else net_sign


def expected_supplier_payment_sign(*, invoice):
    # -1 => expected outgoing payment, +1 => expected incoming payment (credit invoice)
    if not invoice.registered_transaction_id or invoice.payable_account_id is None:
        return -1

    entry_totals = JournalEntry.objects.filter(
        transaction_id=invoice.registered_transaction_id,
        account_id=invoice.payable_account_id,
    ).aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    net = (entry_totals["total_credit"] or Decimal("0.00")) - (entry_totals["total_debit"] or Decimal("0.00"))
    net_sign = sign_of(net)
    return -1 if net_sign == 0 else -net_sign


def get_bank_account_payment_suggestion(*, company, bank_tx):
    if bank_tx.bank_account.account_type != BankAccountType.BANK:
        return None

    signed_amount = to_decimal(bank_tx.amount)
    amount = abs(signed_amount)
    if amount <= Decimal("0.00"):
        return None

    tx_date = bank_tx.date
    if isinstance(tx_date, str):
        tx_date = timezone.datetime.fromisoformat(tx_date).date()
    date_window = timedelta(days=14)
    window_start = tx_date - date_window
    window_end = tx_date + date_window
    candidates = []

    from django.db.models import Q

    from invoicing.models import Invoice

    in_window_or_partially_paid = Q(due_date__gte=window_start, due_date__lte=window_end) | Q(
        paid_amount__gt=Decimal("0.00")
    )
    invoices = (
        Invoice.objects.filter(
            in_window_or_partially_paid,
            company=company,
            is_booked=True,
            booked_transaction__isnull=False,
            is_paid=False,
        )
        .select_related("customer", "receivable_account")
        .prefetch_related("lines")
        .order_by("due_date", "id")
    )
    fallback_receivable = company.accounts.filter(is_active=True, number="1510").first()

    for invoice in invoices:
        if not amounts_equal(invoice.remaining_amount, amount):
            continue

        expected_sign = expected_customer_payment_sign(invoice=invoice)
        if sign_of(signed_amount) != expected_sign:
            continue

        counter_account = invoice.receivable_account or fallback_receivable
        if counter_account is None:
            continue

        invoice_label = invoice.invoice_number or f"{invoice.pk}"
        candidates.append(
            build_candidate_suggestion(
                counter_account=counter_account,
                rule_label=f"Kundfaktura {invoice_label}",
                date_distance=abs((invoice.due_date - tx_date).days),
                priority=1,
            )
        )
        candidates[-1]["source_type"] = "customer_invoice"
        candidates[-1]["source_id"] = invoice.pk

    from payroll.models import PayrollRun
    from supplier_invoices.models import SupplierInvoice

    supplier_invoices = (
        SupplierInvoice.objects.filter(
            in_window_or_partially_paid,
            company=company,
            is_registered=True,
            registered_transaction__isnull=False,
            is_paid=False,
        )
        .select_related("payable_account")
        .order_by("due_date", "id")
    )

    for invoice in supplier_invoices:
        if not amounts_equal(invoice.remaining_amount, amount):
            continue
        if invoice.payable_account is None:
            continue

        expected_sign = expected_supplier_payment_sign(invoice=invoice)
        if sign_of(signed_amount) != expected_sign:
            continue

        invoice_label = invoice.invoice_number or f"{invoice.pk}"
        candidates.append(
            build_candidate_suggestion(
                counter_account=invoice.payable_account,
                rule_label=f"Leverantörsfaktura {invoice_label}",
                date_distance=abs((invoice.due_date - tx_date).days),
                priority=1,
            )
        )
        candidates[-1]["source_type"] = "supplier_invoice"
        candidates[-1]["source_id"] = invoice.pk

    if signed_amount < 0:
        from expenses.models import ExpenseClaim

        expense_window_or_partially_paid = Q(expense_date__gte=window_start, expense_date__lte=window_end) | Q(
            paid_amount__gt=Decimal("0.00")
        )
        claims = (
            ExpenseClaim.objects.filter(
                expense_window_or_partially_paid,
                company=company,
                is_registered=True,
                registered_transaction__isnull=False,
                is_paid=False,
            )
            .select_related("employee", "liability_account")
            .order_by("expense_date", "id")
        )
        for claim in claims:
            if not amounts_equal(claim.remaining_amount, amount):
                continue

            candidates.append(
                build_candidate_suggestion(
                    counter_account=claim.liability_account,
                    rule_label=f"Utlägg {claim.person_display_name}",
                    date_distance=abs((claim.expense_date - tx_date).days),
                    priority=1,
                )
            )
            candidates[-1]["source_type"] = "expense_claim"
            candidates[-1]["source_id"] = claim.pk

        payroll_runs = list(
            PayrollRun.objects.filter(
                company=company,
                is_finished=True,
                booking_transaction__isnull=False,
                payment_date__gte=window_start,
                payment_date__lte=window_end,
            )
            .select_related("booking_transaction")
            .order_by("payment_date", "id")
        )

        payroll_tx_ids = [run.booking_transaction_id for run in payroll_runs if run.booking_transaction_id]
        payroll_entries = (
            JournalEntry.objects.filter(
                transaction_id__in=payroll_tx_ids,
                account__number=PayrollRun.SALARY_LIABILITY_ACCOUNT_NUMBER,
            )
            .values("transaction_id", "account_id")
            .annotate(total_credit=Sum("credit"))
        )
        payroll_amount_by_tx = {row["transaction_id"]: row for row in payroll_entries}
        payroll_account_ids = [row["account_id"] for row in payroll_entries if row.get("account_id")]
        payroll_accounts = Account.objects.in_bulk(payroll_account_ids)

        for run in payroll_runs:
            payroll_row = payroll_amount_by_tx.get(run.booking_transaction_id)
            if payroll_row is None:
                continue

            total_credit = payroll_row["total_credit"] or Decimal("0.00")
            remaining = (total_credit - (run.paid_amount or Decimal("0.00"))).quantize(Decimal("0.01"))
            if not amounts_equal(remaining, amount):
                continue

            counter_account = payroll_accounts.get(payroll_row["account_id"])
            if counter_account is None:
                continue

            candidates.append(
                build_candidate_suggestion(
                    counter_account=counter_account,
                    rule_label=f"Löneutbetalning {run.period_year}-{run.period_month:02d}",
                    date_distance=abs((run.payment_date - tx_date).days),
                    priority=2,
                )
            )
            candidates[-1]["source_type"] = "payroll_run"
            candidates[-1]["source_id"] = run.pk

    return pick_best_candidate(candidates)


def book_bank_transaction_rows(*, bank_tx, rows, user, description=None):
    from bookkeeping.period_locking import is_date_locked

    amount = abs(bank_tx.amount)
    if amount <= Decimal("0"):
        raise ValueError("Beloppet måste vara större än 0 för bokföring.")

    if is_date_locked(bank_tx.company, bank_tx.date):
        raise ValueError("Perioden för transaktionsdatumet är låst. Bokför i öppen period eller lås upp perioden.")

    accounting_year = get_accounting_year_for_date(company=bank_tx.company, date_value=bank_tx.date)
    description = (description or "").strip() or bank_tx.description
    bank_account = bank_tx.bank_account.bookkeeping_account
    default_row_side = "credit" if bank_tx.amount > 0 else "debit"
    normalized_rows = []
    for row in rows:
        if len(row) == 2:
            account, row_amount = row
            row_side = default_row_side
        elif len(row) == 3:
            account, row_amount, row_side = row
        else:
            raise ValueError("Ogiltig bokningsrad för banktransaktion.")
        normalized_amount = (row_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        if normalized_amount <= Decimal("0.00"):
            raise ValueError("Alla bokningsrader måste ha ett belopp större än 0.")
        if row_side not in {"debit", "credit"}:
            raise ValueError("Ogiltig sida på bokningsrad.")
        normalized_rows.append((account, normalized_amount, row_side))

    total_debit = sum(
        (row_amount for _, row_amount, row_side in normalized_rows if row_side == "debit"), Decimal("0.00")
    )
    total_credit = sum(
        (row_amount for _, row_amount, row_side in normalized_rows if row_side == "credit"), Decimal("0.00")
    )
    if bank_tx.amount > 0:
        total_debit += amount
    else:
        total_credit += amount

    if not amounts_equal(total_debit, total_credit):
        raise ValueError("Bokningen balanserar inte banktransaktionen.")

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date=bank_tx.date,
            description=description,
            created_by=user,
            source=TransactionSource.BANK,
        )

        if bank_tx.amount > 0:
            add_journal_entry(
                transaction=txn,
                account=bank_account,
                debit=amount,
                credit=Decimal("0.00"),
                description=description,
            )
            for account, row_amount, row_side in normalized_rows:
                add_journal_entry(
                    transaction=txn,
                    account=account,
                    debit=row_amount if row_side == "debit" else Decimal("0.00"),
                    credit=row_amount if row_side == "credit" else Decimal("0.00"),
                    description=description,
                )
        else:
            for account, row_amount, row_side in normalized_rows:
                add_journal_entry(
                    transaction=txn,
                    account=account,
                    debit=row_amount if row_side == "debit" else Decimal("0.00"),
                    credit=row_amount if row_side == "credit" else Decimal("0.00"),
                    description=description,
                )
            add_journal_entry(
                transaction=txn,
                account=bank_account,
                debit=Decimal("0.00"),
                credit=amount,
                description=description,
            )

        txn.validate_balanced()

        bank_tx.is_booked = True
        bank_tx.booked_at = timezone.now()
        bank_tx.booked_transaction = txn
        bank_tx.save(update_fields=["is_booked", "booked_at", "booked_transaction"])

    return txn


TRANSFER_LINK_DATE_WINDOW_DAYS = 7


def _journal_net_for_account(*, transaction, account):
    totals = JournalEntry.objects.filter(transaction=transaction, account=account).aggregate(
        total_debit=Sum("debit"), total_credit=Sum("credit")
    )
    return (totals["total_debit"] or Decimal("0.00")) - (totals["total_credit"] or Decimal("0.00"))


def _other_bank_side_transaction_ids(*, company, bank_account):
    """Transaction ids that a DIFFERENT one of the company's own bank accounts was
    actually booked/linked against - the universe of valid "other leg of a transfer"
    verifications. Excludes anything only reachable through a manual entry, an invoice
    settlement, or any other non-transfer booking that happens to touch the same account.
    """
    return (
        BankTransaction.objects.filter(company=company, booked_transaction__isnull=False)
        .exclude(bank_account=bank_account)
        .values_list("booked_transaction_id", flat=True)
    )


def get_transfer_link_candidates(*, company, bank_tx):
    """Booked verifications a raw bank feed row can be *linked* to instead of re-booked.

    Used to reconcile internal transfers between two of the company's own bank accounts:
    one side gets booked normally (book_bank_transaction_rows), which posts a journal
    entry for both bank accounts in a single balanced verification. The other side's raw
    feed row would otherwise have to create a second, duplicate verification for the same
    movement - instead it just links to the verification the first side already produced.

    A candidate must be a verification that a DIFFERENT one of the company's own bank
    accounts was actually booked against (see _other_bank_side_transaction_ids), dated
    within TRANSFER_LINK_DATE_WINDOW_DAYS of bank_tx's own date - a transfer's two legs
    clear within a few days of each other, not months apart - with a journal entry on
    bank_tx's own bookkeeping account whose net amount exactly matches bank_tx's signed
    amount, and that isn't already claimed by another BankTransaction from the same bank
    account (the uniq_bank_tx_per_account_per_verification constraint enforces this at
    save time).
    """
    if bank_tx.is_booked:
        return []

    account = bank_tx.bank_account.bookkeeping_account
    signed_amount = to_decimal(bank_tx.amount).quantize(Decimal("0.01"))
    if signed_amount == Decimal("0.00"):
        return []

    claimed_transaction_ids = BankTransaction.objects.filter(
        bank_account=bank_tx.bank_account,
        booked_transaction__isnull=False,
    ).values_list("booked_transaction_id", flat=True)

    tx_date = coerce_date(bank_tx.date)
    date_window = timedelta(days=TRANSFER_LINK_DATE_WINDOW_DAYS)

    entries = (
        JournalEntry.objects.filter(
            account=account,
            transaction_id__in=_other_bank_side_transaction_ids(company=company, bank_account=bank_tx.bank_account),
            transaction__date__gte=tx_date - date_window,
            transaction__date__lte=tx_date + date_window,
        )
        .exclude(transaction_id__in=claimed_transaction_ids)
        .select_related("transaction")
    )

    net_by_transaction = {}
    for entry in entries:
        bucket = net_by_transaction.setdefault(
            entry.transaction_id, {"net": Decimal("0.00"), "transaction": entry.transaction}
        )
        bucket["net"] += entry.debit - entry.credit

    candidates = []
    for bucket in net_by_transaction.values():
        if bucket["net"].quantize(Decimal("0.01")) != signed_amount:
            continue
        txn = bucket["transaction"]
        candidates.append(
            {
                "id": txn.pk,
                "transaction": txn,
                "label": f"{txn.voucher_series}{txn.voucher_number} · {txn.date} · {txn.description}",
                "date_distance": abs((txn.date - tx_date).days),
            }
        )

    candidates.sort(key=lambda item: (item["date_distance"], -item["id"]))
    return candidates


def link_bank_transaction_to_transaction(*, bank_tx, transaction):
    """Reconcile bank_tx against an already-booked verification without adding new
    journal entries - the counterpart to get_transfer_link_candidates above.
    """
    if bank_tx.is_booked:
        raise ValueError("Banktransaktionen är redan bokförd.")

    if transaction.accounting_year.company_id != bank_tx.company_id:
        raise ValueError("Verifikationen tillhör inte samma företag som banktransaktionen.")

    has_other_bank_side = (
        BankTransaction.objects.filter(company=bank_tx.company, booked_transaction=transaction)
        .exclude(bank_account=bank_tx.bank_account)
        .exists()
    )
    if not has_other_bank_side:
        raise ValueError("Verifikationen är inte bokförd från en banktransaktion på ett annat bankkonto.")

    date_distance = abs((transaction.date - coerce_date(bank_tx.date)).days)
    if date_distance > TRANSFER_LINK_DATE_WINDOW_DAYS:
        raise ValueError(
            f"Verifikationens datum ligger mer än {TRANSFER_LINK_DATE_WINDOW_DAYS} dagar från banktransaktionens datum."
        )

    account = bank_tx.bank_account.bookkeeping_account
    expected_amount = to_decimal(bank_tx.amount).quantize(Decimal("0.01"))
    net = _journal_net_for_account(transaction=transaction, account=account).quantize(Decimal("0.01"))
    if net != expected_amount:
        raise ValueError(
            f"Verifikationen innehåller ingen rad på {account.number} {account.name} "
            "som matchar banktransaktionens belopp."
        )

    already_claimed = BankTransaction.objects.filter(
        bank_account=bank_tx.bank_account,
        booked_transaction=transaction,
    ).exclude(pk=bank_tx.pk)
    if already_claimed.exists():
        raise ValueError("En annan banktransaktion från samma bankkälla är redan kopplad till denna verifikation.")

    with db_transaction.atomic():
        bank_tx.is_booked = True
        bank_tx.booked_transaction = transaction
        bank_tx.booked_at = timezone.now()
        bank_tx.save(update_fields=["is_booked", "booked_transaction", "booked_at"])

    return transaction


def get_accounting_year_for_date(*, company, date_value):
    years = AccountingYear.objects.filter(
        company=company,
        start_date__lte=date_value,
        end_date__gte=date_value,
    ).order_by("-start_date", "-id")

    if not years.exists():
        raise ValueError("Inget räkenskapsår matchar valt datum.")
    if years.count() > 1:
        raise ValueError("Flera räkenskapsår matchar valt datum. Kontrollera räkenskapsåren.")

    return years.first()


class PaymentReversalError(ValueError):
    """A payment verification could not be undone."""


def _payable_type_registry():
    """(label, model, payment_model) for every object type that can be settled by a bank payment.

    Derived, not listed: every concrete `AbstractPayment` subclass is a payment-history
    table, and its `invoice` FK — named that on all three, see `bookkeeping.payables` —
    points back at the payable it settles. The label comes off that payable's
    `PAYABLE_LABEL`, so the Swedish wording lives with the model rather than here.

    Walks the app registry rather than `AbstractPayment.__subclasses__()`: `get_models()`
    only returns models Django has actually registered, so the result can't depend on
    which modules happened to be imported first. Sorted by label to keep the payment-undo
    preview's ordering stable no matter what INSTALLED_APPS looks like.
    """
    registry = [
        (payment_model._meta.get_field("payable").related_model, payment_model)
        for payment_model in apps.get_models()
        if issubclass(payment_model, AbstractPayment)
    ]
    return tuple(
        sorted(
            ((model.PAYABLE_LABEL, model, payment_model) for model, payment_model in registry),
            key=lambda item: (item[0], item[1]._meta.label),
        )
    )


def get_payment_reversal_preview(payment_transaction, *, company):
    """List every invoice/claim settled by this transaction, plus its BankTransaction(s), if any.

    Used to show the user the full blast radius before they confirm an undo — a single
    bank transaction can settle several invoices/claims at once, and undoing it un-settles
    all of them. A verification can also have more than one linked BankTransaction when it
    books an internal transfer between two of the company's own bank accounts (see
    link_bank_transaction_to_transaction) — undoing it releases every one of them.
    """
    affected = []
    for label, model, payment_model in _payable_type_registry():
        obj_ids = (
            payment_model.objects.filter(transaction=payment_transaction, reversed_at__isnull=True)
            .values_list("payable_id", flat=True)
            .distinct()
        )
        for obj in model.objects.filter(company=company, id__in=obj_ids):
            affected.append({"label": label, "object": obj})

    bank_transactions = list(payment_transaction.bank_transaction_sources.all())
    return {"affected": affected, "bank_transactions": bank_transactions}


def undo_bank_payment(payment_transaction, *, user, company):
    """Undo a payment verification created via bank reconciliation.

    Creates a correction Transaction mirroring every JournalEntry on the payment
    verification (debit/credit swapped, never editing/deleting the original), resets
    payment status on every invoice/claim it settled, and releases every BankTransaction
    linked to it (is_booked=False) so they can be booked/linked again — usually one, but
    an internal transfer verification can have two (see link_bank_transaction_to_transaction).
    """
    from bookkeeping.period_locking import is_date_locked

    if payment_transaction.corrections.exists():
        raise PaymentReversalError("Betalningsverifikationen har redan en registrerad korrigering.")

    reversal_date = timezone.localdate()
    if is_date_locked(company, reversal_date):
        raise PaymentReversalError(
            "Dagens period är låst. Lås upp perioden eller välj en öppen period för korrigering."
        )

    with db_transaction.atomic():
        reversal = Transaction.objects.create(
            accounting_year=payment_transaction.accounting_year,
            date=reversal_date,
            description=(
                f"Ångrad betalning – korrigering av verifikation "
                f"{payment_transaction.reference or payment_transaction.pk}"
            ),
            created_by=user,
            correction_of=payment_transaction,
        )
        for entry in payment_transaction.entries.all():
            add_journal_entry(
                transaction=reversal,
                account=entry.account,
                debit=entry.credit,
                credit=entry.debit,
                description=f"Ångrad betalning {payment_transaction.reference or payment_transaction.pk}"[:300],
            )
        reversal.validate_balanced()

        for _label, model, payment_model in _payable_type_registry():
            obj_ids = (
                payment_model.objects.filter(transaction=payment_transaction, reversed_at__isnull=True)
                .values_list("payable_id", flat=True)
                .distinct()
            )
            affected = model.objects.select_for_update().filter(company=company, id__in=obj_ids)
            for obj in affected:
                payment_model.objects.filter(
                    payable=obj, transaction=payment_transaction, reversed_at__isnull=True
                ).update(reversed_at=timezone.now(), reversal_transaction=reversal)
                reapply_payment_state(obj, payment_model)

        for bank_tx in payment_transaction.bank_transaction_sources.all():
            bank_tx.is_booked = False
            bank_tx.booked_transaction = None
            bank_tx.booked_at = None
            bank_tx.save(update_fields=["is_booked", "booked_transaction", "booked_at"])

        return reversal
