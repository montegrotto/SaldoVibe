from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from attachments.utils import is_safe_return_to, parse_attachment_ids, replace_query_param
from attachments.view_helpers import selectable_attachments
from bookkeeping.company_scope import require_company
from bookkeeping.models import Transaction

from .forms import (
    BankAccountForm,
    BankImportForm,
    BankTransactionBookForm,
    BankTransactionManualForm,
    parse_bank_csv,
)
from .models import BankAccount, BankAccountType, BankImport, BankProfile, BankTransaction
from .services import (
    book_bank_transaction,
    build_account_balances,
    build_customer_invoice_booking_rows,
    build_expense_claim_booking_rows,
    build_supplier_invoice_booking_rows,
    ensure_tax_account_for_company,
    expected_customer_payment_sign,
    expected_supplier_payment_sign,
    get_managed_bank_accounts,
    get_manual_booking_invoice_options,
    get_payment_reversal_preview,
    get_quick_booking_suggestion,
    get_transfer_link_candidates,
    has_sufficient_amount,
    link_bank_transaction_to_transaction,
    resolve_quick_booking_rows,
    sign_of,
    to_decimal,
    undo_bank_payment,
)


def _book_transaction_template_context(*, bank_tx, form):
    amount = abs(bank_tx.amount)
    bank_debit = amount if bank_tx.amount > 0 else Decimal("0.00")
    bank_credit = amount if bank_tx.amount < 0 else Decimal("0.00")
    counter_debit = amount if bank_tx.amount < 0 else Decimal("0.00")
    counter_credit = amount if bank_tx.amount > 0 else Decimal("0.00")
    return {
        "bank_tx": bank_tx,
        "form": form,
        "bank_debit": bank_debit,
        "bank_credit": bank_credit,
        "counter_debit": counter_debit,
        "counter_credit": counter_credit,
    }


def _transaction_list_redirect_for_bank_account(bank_account_id):
    return redirect(f"{reverse('banking:transaction_list')}?bank_account={bank_account_id}")


ALLOC_MODE_PREFIXES = {
    "customer_invoice": "customer",
    "supplier_invoice": "supplier",
    "expense_claim": "expense",
    "payroll_run": "payroll",
}

ALLOC_MODE_NOUNS = {
    "customer_invoice": "kundfaktura",
    "supplier_invoice": "leverantörsfaktura",
    "expense_claim": "utlägg",
    "payroll_run": "lönekörning",
}


def _build_book_transaction_context(
    *,
    company,
    bank_tx,
    counter_account_choices,
    booking_mode=None,
    posted_account_ids=None,
    posted_amounts=None,
    selected_customer_invoice_id="",
    selected_supplier_invoice_id="",
    posted_invoice_allocations=None,
    posted_invoice_extras=None,
    selected_attachment_ids=None,
    posted_description=None,
    picker_return_to="",
    selected_transfer_transaction_id="",
):
    amount = abs(bank_tx.amount)
    is_incoming = bank_tx.amount > 0
    invoice_options = get_manual_booking_invoice_options(company=company, bank_tx=bank_tx)
    quick_suggestion = get_quick_booking_suggestion(company=company, bank_tx=bank_tx)
    transfer_candidates = get_transfer_link_candidates(company=company, bank_tx=bank_tx)

    other_managed_accounts = get_managed_bank_accounts(company)
    other_managed_accounts.pop(bank_tx.bank_account.bookkeeping_account_id, None)
    transfer_managed_accounts = {
        str(account_id): {"bank_account_id": managing_account.pk}
        for account_id, managing_account in other_managed_accounts.items()
    }

    # Legacy per-type modes collapse into the unified match mode in the UI.
    if booking_mode in ALLOC_MODE_PREFIXES:
        booking_mode = "match"
    if booking_mode not in {"match", "manual", "transfer"}:
        booking_mode = "manual"
        if quick_suggestion is not None and quick_suggestion.get("source_type") in ALLOC_MODE_PREFIXES:
            booking_mode = "match"

    group_definitions = [
        ("customer_invoice", "Kundfakturor"),
        ("supplier_invoice", "Leverantörsfakturor"),
        ("expense_claim", "Utlägg"),
        ("payroll_run", "Löneutbetalningar"),
    ]
    outgoing_only_modes = {"expense_claim", "payroll_run"}
    match_option_groups = []
    flat_options = []
    for mode, group_label in group_definitions:
        if mode in outgoing_only_modes and is_incoming:
            continue
        group_options = []
        for option in invoice_options[mode]:
            item = dict(option)
            item["key"] = f"{mode}:{option['id']}"
            group_options.append(item)
            flat_options.append(item)
        if group_options:
            match_option_groups.append({"label": group_label, "options": group_options})

    selected_key = ""
    if selected_customer_invoice_id:
        selected_key = f"customer_invoice:{selected_customer_invoice_id}"
    elif selected_supplier_invoice_id:
        selected_key = f"supplier_invoice:{selected_supplier_invoice_id}"
    elif quick_suggestion is not None and quick_suggestion.get("source_type") in ALLOC_MODE_PREFIXES:
        if quick_suggestion.get("source_id"):
            selected_key = f"{quick_suggestion['source_type']}:{quick_suggestion['source_id']}"
    elif len(flat_options) == 1:
        selected_key = flat_options[0]["key"]

    match_allocation_rows = [{"invoice_id": "", "amount": ""}]
    if selected_key:
        selected_option = next((item for item in flat_options if item["key"] == selected_key), None)
        if selected_option is not None:
            default_amount = min(Decimal(selected_option["remaining"]), amount)
            match_allocation_rows = [{"invoice_id": selected_key, "amount": f"{default_amount:.2f}"}]

    match_extra_rows = []
    if booking_mode == "match":
        if posted_invoice_allocations:
            match_allocation_rows = posted_invoice_allocations
        match_extra_rows = posted_invoice_extras or []

    selected_attachment_ids = selected_attachment_ids or []
    selected_attachments = selectable_attachments(company, selected_attachment_ids).order_by("-uploaded_at")

    description_value = posted_description if posted_description is not None else bank_tx.description

    return {
        "bank_tx": bank_tx,
        "description_value": description_value,
        "counter_account_choices": counter_account_choices,
        "account_balances": build_account_balances(company, reference_date=bank_tx.date),
        "transfer_managed_accounts": transfer_managed_accounts,
        "is_incoming": is_incoming,
        "locked_amount": amount,
        "posted_account_ids": posted_account_ids or [],
        "posted_amounts": posted_amounts or [],
        "booking_mode": booking_mode,
        "match_option_groups": match_option_groups,
        "match_has_options": bool(flat_options),
        "match_allocation_rows": match_allocation_rows,
        "match_extra_rows": match_extra_rows,
        "transfer_candidates": transfer_candidates,
        "selected_transfer_transaction_id": selected_transfer_transaction_id,
        "selected_attachments": selected_attachments,
        "selected_attachment_ids_csv": ",".join(str(attachment_id) for attachment_id in selected_attachment_ids),
        "picker_return_to": picker_return_to,
    }


def _render_book_transaction_form(
    request,
    *,
    company,
    bank_tx,
    counter_account_choices,
    booking_mode=None,
    posted_account_ids=None,
    posted_amounts=None,
    selected_customer_invoice_id="",
    selected_supplier_invoice_id="",
    posted_invoice_allocations=None,
    posted_invoice_extras=None,
    selected_attachment_ids=None,
    posted_description=None,
    selected_transfer_transaction_id="",
):
    return render(
        request,
        "banking/book_transaction_form.html",
        _build_book_transaction_context(
            company=company,
            bank_tx=bank_tx,
            counter_account_choices=counter_account_choices,
            booking_mode=booking_mode,
            posted_account_ids=posted_account_ids,
            posted_amounts=posted_amounts,
            selected_customer_invoice_id=selected_customer_invoice_id,
            selected_supplier_invoice_id=selected_supplier_invoice_id,
            posted_invoice_allocations=posted_invoice_allocations,
            posted_invoice_extras=posted_invoice_extras,
            selected_attachment_ids=selected_attachment_ids,
            posted_description=posted_description,
            picker_return_to=request.get_full_path(),
            selected_transfer_transaction_id=selected_transfer_transaction_id,
        ),
    )


def _parse_invoice_mode_post(request, *, prefix):
    """Parse posted invoice allocations and extra rows. Falls back to the legacy single-select field."""
    if f"{prefix}_alloc_invoice[]" in request.POST:
        alloc_ids = request.POST.getlist(f"{prefix}_alloc_invoice[]")
        alloc_amounts = request.POST.getlist(f"{prefix}_alloc_amount[]")
        allocations = []
        for invoice_id, raw_amount in zip(alloc_ids, alloc_amounts):
            invoice_id = invoice_id.strip()
            raw_amount = raw_amount.strip()
            if not invoice_id and not raw_amount:
                continue
            allocations.append({"invoice_id": invoice_id, "amount": raw_amount})
    else:
        legacy_id = (request.POST.get(f"{prefix}_invoice_id") or "").strip()
        allocations = [{"invoice_id": legacy_id, "amount": ""}] if legacy_id else []

    extras = []
    extra_ids = request.POST.getlist(f"{prefix}_extra_account[]")
    extra_amounts = request.POST.getlist(f"{prefix}_extra_amount[]")
    for account_id, raw_amount in zip(extra_ids, extra_amounts):
        account_id = account_id.strip()
        raw_amount = raw_amount.strip()
        if not account_id and not raw_amount:
            continue
        extras.append({"account_id": account_id, "amount": raw_amount})
    return allocations, extras


def _resolve_invoice_mode_booking(
    *, company, bank_tx, booking_mode, posted_allocations, posted_extras, counter_account_choices
):
    """Validate posted invoice allocations + extra rows and build booking rows and payment allocations.

    booking_mode "match" allows mixing source types in one booking (e.g. netting a
    supplier invoice against a customer invoice); rows then settle on each source's
    normal side and the signed sum must equal the transaction amount.

    Returns (rows, allocations, error_message) where error_message is None on success.
    """
    amount = abs(bank_tx.amount)
    all_options = get_manual_booking_invoice_options(company=company, bank_tx=bank_tx)
    is_match_mode = booking_mode == "match"
    if is_match_mode:
        invoice_noun = "post"
        options_by_id = {
            f"{option['source_type']}:{option['id']}": option
            for mode_options in all_options.values()
            for option in mode_options
        }
    else:
        invoice_noun = ALLOC_MODE_NOUNS[booking_mode]
        options_by_id = {option["id"]: option for option in all_options[booking_mode]}

    if not posted_allocations:
        return None, None, f"Välj minst en {invoice_noun} att bokföra mot."

    rows = []
    allocations = []
    seen_invoice_ids = set()
    total_allocated = Decimal("0.00")
    normal_counter_side = "credit" if bank_tx.amount > 0 else "debit"

    for posted in posted_allocations:
        invoice_id = posted["invoice_id"]
        if not invoice_id:
            return None, None, "Välj post på alla matchningsrader."
        if invoice_id in seen_invoice_ids:
            return None, None, "Samma post kan bara förekomma på en rad."
        seen_invoice_ids.add(invoice_id)
        selected_option = options_by_id.get(invoice_id)
        if selected_option is None:
            return None, None, f"Välj en giltig {invoice_noun} att bokföra mot."

        if posted["amount"]:
            try:
                allocation_amount = to_decimal(posted["amount"]).quantize(Decimal("0.01"))
            except Exception:
                return None, None, "Ogiltigt belopp på en fakturarad."
            if allocation_amount <= Decimal("0.00"):
                return None, None, "Belopp på fakturarader måste vara större än 0."
        else:
            allocation_amount = (amount - total_allocated).quantize(Decimal("0.01"))
            if allocation_amount <= Decimal("0.00"):
                return None, None, "Ange belopp på fakturaraden."

        source_type = selected_option["source_type"]
        if source_type == "customer_invoice":
            from invoicing.models import Invoice

            invoice = (
                Invoice.objects.filter(
                    pk=selected_option["source_id"],
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
                return None, None, "Kundfakturan är inte längre valbar för betalning."
            if not is_match_mode and sign_of(bank_tx.amount) != expected_customer_payment_sign(invoice=invoice):
                return (
                    None,
                    None,
                    "Fel betalningsriktning för vald kundfaktura. Använd helt manuell bokning för justeringar.",
                )
            if not has_sufficient_amount(invoice.remaining_amount, allocation_amount):
                return None, None, "Beloppet är större än återstående belopp på vald kundfaktura."
            try:
                invoice_rows, settlement_difference = build_customer_invoice_booking_rows(
                    invoice=invoice,
                    payment_amount=allocation_amount,
                )
            except ValueError as exc:
                return None, None, str(exc)
        elif source_type == "supplier_invoice":
            from supplier_invoices.models import SupplierInvoice

            invoice = (
                SupplierInvoice.objects.filter(
                    pk=selected_option["source_id"],
                    company=company,
                    is_registered=True,
                    registered_transaction__isnull=False,
                    is_paid=False,
                )
                .select_related("payable_account")
                .first()
            )
            if invoice is None or invoice.payable_account is None:
                return None, None, "Leverantörsfakturan är inte längre valbar för betalning."
            if not is_match_mode and sign_of(bank_tx.amount) != expected_supplier_payment_sign(invoice=invoice):
                return (
                    None,
                    None,
                    "Fel betalningsriktning för vald leverantörsfaktura. Använd helt manuell bokning för justeringar.",
                )
            if not has_sufficient_amount(invoice.remaining_amount, allocation_amount):
                return None, None, "Beloppet är större än återstående belopp på vald leverantörsfaktura."
            try:
                invoice_rows, settlement_difference = build_supplier_invoice_booking_rows(
                    invoice=invoice,
                    payment_amount=allocation_amount,
                )
            except ValueError as exc:
                return None, None, str(exc)
        elif source_type == "expense_claim":
            from expenses.models import ExpenseClaim

            claim = (
                ExpenseClaim.objects.filter(
                    pk=selected_option["source_id"],
                    company=company,
                    is_registered=True,
                    registered_transaction__isnull=False,
                    is_paid=False,
                )
                .select_related("liability_account")
                .first()
            )
            if claim is None:
                return None, None, "Utlägget är inte längre valbart för utbetalning."
            if not is_match_mode and sign_of(bank_tx.amount) != -1:
                return (
                    None,
                    None,
                    "Utlägg kan bara bokas mot utgående betalningar. Använd helt manuell bokning för justeringar.",
                )
            if not has_sufficient_amount(claim.remaining_amount, allocation_amount):
                return None, None, "Beloppet är större än återstående belopp på valt utlägg."
            try:
                invoice_rows, settlement_difference = build_expense_claim_booking_rows(
                    claim=claim,
                    payment_amount=allocation_amount,
                )
            except ValueError as exc:
                return None, None, str(exc)
        else:
            from payroll.models import PayrollRun

            run = PayrollRun.objects.filter(
                pk=selected_option["source_id"],
                company=company,
                is_finished=True,
                booking_transaction__isnull=False,
            ).first()
            if run is None:
                return None, None, "Lönekörningen är inte längre valbar för betalning."
            if not is_match_mode and sign_of(bank_tx.amount) != -1:
                return (
                    None,
                    None,
                    "Löneutbetalningar kan bara bokas mot utgående betalningar. Använd helt manuell bokning för justeringar.",
                )
            if not has_sufficient_amount(run.salary_payment_remaining(), allocation_amount):
                return None, None, "Beloppet är större än återstående nettolön för vald lönekörning."
            invoice_rows = [(selected_option["counter_account"], allocation_amount, "debit")]
            settlement_difference = Decimal("0.00")

        rows.extend(invoice_rows)
        allocations.append(
            {
                "source_type": source_type,
                "source_id": selected_option["source_id"],
                "amount": allocation_amount,
                "settlement_difference": settlement_difference,
                "strict": True,
            }
        )
        # Each allocation settles on its source's normal side; a row on the opposite
        # side of the bank counter side (netting) reduces the amount to distribute.
        settle_row = invoice_rows[0]
        settle_side = settle_row[2] if len(settle_row) == 3 else normal_counter_side
        if settle_side == normal_counter_side:
            total_allocated += allocation_amount
        else:
            total_allocated -= allocation_amount

    accounts_by_id = {str(account.pk): account for account in counter_account_choices}
    default_side = normal_counter_side
    opposite_side = "debit" if default_side == "credit" else "credit"
    extras_total = Decimal("0.00")
    for extra in posted_extras:
        if not extra["account_id"]:
            return None, None, "Välj konto på alla extrarader."
        account = accounts_by_id.get(extra["account_id"])
        if account is None:
            return None, None, "Ett eller flera konton på extrarader är ogiltiga."
        try:
            extra_amount = to_decimal(extra["amount"]).quantize(Decimal("0.01"))
        except Exception:
            return None, None, "Ogiltigt belopp på en extrarad."
        if extra_amount == Decimal("0.00"):
            return None, None, "Belopp på extrarader kan inte vara 0."
        if extra_amount > Decimal("0.00"):
            rows.append((account, extra_amount, default_side))
        else:
            # Negative extra amount books the opposite side, e.g. a fee deducted
            # from an incoming invoice payment.
            rows.append((account, -extra_amount, opposite_side))
        extras_total += extra_amount

    if (total_allocated + extras_total).quantize(Decimal("0.01")) != amount.quantize(Decimal("0.01")):
        return (
            None,
            None,
            "Raderna måste tillsammans summera till transaktionsbeloppet. Kontrollera belopp och betalningsriktning.",
        )

    return rows, allocations, None


company_required = require_company(ensure_company=ensure_tax_account_for_company)


@login_required
@company_required
def account_list(request, company):

    accounts = company.bank_accounts.select_related("bookkeeping_account").order_by("name")
    return_bank_account_id = request.GET.get("return_to")
    return render(
        request,
        "banking/account_list.html",
        {
            "accounts": accounts,
            "return_bank_account_id": return_bank_account_id,
        },
    )


@login_required
@company_required
def account_create(request, company):

    if request.method == "POST":
        form = BankAccountForm(request.POST, company=company)
        if form.is_valid():
            bank_account = form.save(commit=False)
            bank_account.company = company
            bank_account.save()
            messages.success(request, "Bankkälla har skapats.")
            return redirect("banking:account_list")
        messages.error(request, "Kunde inte skapa bankkällan. Kontrollera fälten.")
    else:
        form = BankAccountForm(company=company, initial={"is_active": True})

    return render(
        request,
        "banking/account_form.html",
        {
            "form": form,
            "page_title_text": "Ny bankkälla",
            "submit_label": "Skapa bankkälla",
        },
    )


@login_required
@company_required
def account_update(request, company, pk):

    account = get_object_or_404(BankAccount, pk=pk, company=company)
    if account.account_type == BankAccountType.TAX:
        messages.error(request, "Skattekonto hanteras automatiskt och kan inte redigeras här.")
        return redirect("banking:account_list")

    if request.method == "POST":
        form = BankAccountForm(request.POST, company=company, instance=account)
        if form.is_valid():
            form.save()
            messages.success(request, "Bankkälla har uppdaterats.")
            return redirect("banking:account_list")
        messages.error(request, "Kunde inte uppdatera bankkällan. Kontrollera fälten.")
    else:
        form = BankAccountForm(company=company, instance=account)

    return render(
        request,
        "banking/account_form.html",
        {
            "form": form,
            "account": account,
            "page_title_text": "Redigera bankkälla",
            "submit_label": "Spara ändringar",
        },
    )


@login_required
@company_required
def transaction_list(request, company):

    bank_accounts = company.bank_accounts.filter(is_active=True).order_by("name")
    import_form = BankImportForm(company=company)
    selected_bank_account_id = request.GET.get("bank_account")
    selected_bank_account = None
    if selected_bank_account_id:
        selected_bank_account = bank_accounts.filter(pk=selected_bank_account_id).first()
    if selected_bank_account is None:
        selected_bank_account = bank_accounts.first()
        selected_bank_account_id = str(selected_bank_account.pk) if selected_bank_account is not None else ""

    manual_form_initial = {}
    if selected_bank_account_id:
        manual_form_initial["bank_account"] = selected_bank_account_id
    manual_form = BankTransactionManualForm(company=company, initial=manual_form_initial)

    transactions = BankTransaction.objects.filter(company=company).select_related(
        "bank_account",
        "booked_transaction",
    )

    if selected_bank_account is not None:
        transactions = transactions.filter(bank_account=selected_bank_account)

    transactions = transactions.order_by("-date", "-id")[:300]

    selected_bank_account_balance = None
    if selected_bank_account is not None:
        account_balances = build_account_balances(company)
        balance_str = account_balances.get(str(selected_bank_account.bookkeeping_account_id))
        if balance_str is not None:
            selected_bank_account_balance = Decimal(balance_str)

    transaction_rows = []
    for tx in transactions:
        book_form = None
        quick_suggestion = None
        if not tx.is_booked:
            book_form = BankTransactionBookForm(
                company=company,
                initial={"description": tx.description},
                prefix=f"book-{tx.pk}",
            )
            quick_suggestion = get_quick_booking_suggestion(company=company, bank_tx=tx)
        transaction_rows.append({"tx": tx, "book_form": book_form, "quick_suggestion": quick_suggestion})

    return render(
        request,
        "banking/transaction_list.html",
        {
            "import_form": import_form,
            "manual_form": manual_form,
            "transaction_rows": transaction_rows,
            "bank_accounts": bank_accounts,
            "selected_bank_account": selected_bank_account,
            "selected_bank_account_id": str(selected_bank_account_id or ""),
            "selected_bank_account_balance": selected_bank_account_balance,
        },
    )


@login_required
@require_POST
@company_required
def create_manual_transaction(request, company):

    post_data = request.POST.copy()
    if not post_data.get("bank_account"):
        default_bank_account = company.bank_accounts.filter(is_active=True).order_by("name").first()
        if default_bank_account is not None:
            post_data["bank_account"] = str(default_bank_account.pk)

    form = BankTransactionManualForm(post_data, company=company)
    if not form.is_valid():
        messages.error(request, "Kunde inte spara transaktionen. Kontrollera fälten.")
        fallback_id = request.POST.get("bank_account")
        if fallback_id:
            return _transaction_list_redirect_for_bank_account(fallback_id)
        return redirect("banking:transaction_list")

    bank_tx = form.save(commit=False)
    bank_tx.company = company

    if not bank_tx.external_id:
        bank_tx.external_id = f"manual-{bank_tx.date.isoformat()}-{uuid4().hex[:8]}"

    if BankTransaction.objects.filter(
        company=company,
        bank_account=bank_tx.bank_account,
        external_id=bank_tx.external_id,
    ).exists():
        messages.error(request, "Referensen används redan för vald bankkälla. Ange en unik referens.")
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    bank_tx.save()
    messages.success(request, "Manuell banktransaktion har lagts till.")
    return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)


@login_required
@require_POST
@company_required
def import_transactions(request, company):

    post_data = request.POST.copy()
    if not post_data.get("bank_account"):
        default_bank_account = company.bank_accounts.filter(is_active=True).order_by("name").first()
        if default_bank_account is not None:
            post_data["bank_account"] = str(default_bank_account.pk)

    form = BankImportForm(post_data, request.FILES, company=company)
    if not form.is_valid():
        messages.error(request, "Kunde inte importera filen. Kontrollera fil och bankkälla.")
        fallback_id = request.POST.get("bank_account")
        if fallback_id:
            return _transaction_list_redirect_for_bank_account(fallback_id)
        return redirect("banking:transaction_list")

    bank_account = form.cleaned_data["bank_account"]
    if bank_account.account_type == BankAccountType.TAX:
        bank_profile = BankProfile.SKATTEVERKET
    else:
        bank_profile = bank_account.default_bank_profile or BankProfile.AUTO
    profile_label = dict(BankProfile.choices).get(bank_profile, bank_profile)
    source_name = profile_label
    csv_file = form.cleaned_data["csv_file"]

    try:
        rows = parse_bank_csv(csv_file, bank_profile=bank_profile)
    except ValueError as exc:
        messages.error(request, str(exc))
        return _transaction_list_redirect_for_bank_account(bank_account.pk)

    created_count = 0
    import_batch = BankImport.objects.create(
        company=company,
        bank_account=bank_account,
        source_name=source_name,
        imported_by=request.user,
    )

    for row in rows:
        _, created = BankTransaction.objects.get_or_create(
            company=company,
            bank_account=bank_account,
            external_id=row["external_id"],
            defaults={
                "import_batch": import_batch,
                "date": row["date"],
                "description": row["description"],
                "amount": row["amount"],
                "balance": row["balance"],
            },
        )
        if created:
            created_count += 1

    messages.success(request, f"Import klar. {created_count} nya transaktioner lades till.")
    return _transaction_list_redirect_for_bank_account(bank_account.pk)


@login_required
@company_required
def book_transaction(request, company, transaction_id):

    bank_tx = get_object_or_404(
        BankTransaction.objects.select_related("bank_account", "bank_account__bookkeeping_account"),
        pk=transaction_id,
        company=company,
    )

    if bank_tx.is_booked:
        messages.info(request, "Banktransaktionen är redan bokförd.")
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    counter_account_choices = company.accounts.filter(is_active=True).order_by("number")
    amount = abs(bank_tx.amount)

    if request.method != "POST":
        return _render_book_transaction_form(
            request,
            company=company,
            bank_tx=bank_tx,
            counter_account_choices=counter_account_choices,
            booking_mode=request.GET.get("booking_mode"),
            selected_attachment_ids=parse_attachment_ids(request.GET.get("selected_attachments")),
        )

    booking_mode = (request.POST.get("booking_mode") or "manual").strip()
    selected_attachment_ids = parse_attachment_ids(request.POST.get("selected_attachment_ids"))
    posted_description = (request.POST.get("description") or "").strip()

    if booking_mode == "transfer":
        transfer_transaction_id = (request.POST.get("transfer_transaction_id") or "").strip()
        transfer_transaction = (
            Transaction.objects.filter(pk=transfer_transaction_id, accounting_year__company=company).first()
            if transfer_transaction_id
            else None
        )
        error_message = None
        if transfer_transaction is None:
            error_message = "Välj en verifikation att koppla banktransaktionen till."
        else:
            try:
                link_bank_transaction_to_transaction(bank_tx=bank_tx, transaction=transfer_transaction)
            except ValueError as exc:
                error_message = str(exc)

        if error_message is not None:
            messages.error(request, error_message)
            return _render_book_transaction_form(
                request,
                company=company,
                bank_tx=bank_tx,
                counter_account_choices=counter_account_choices,
                booking_mode=booking_mode,
                selected_attachment_ids=selected_attachment_ids,
                posted_description=posted_description,
                selected_transfer_transaction_id=transfer_transaction_id,
            )

        selected_attachments = selectable_attachments(company, selected_attachment_ids)
        if selected_attachments.exists():
            transfer_transaction.attachments.add(*selected_attachments)

        messages.success(
            request,
            "Banktransaktionen har kopplats till verifikation "
            f"{transfer_transaction.voucher_series}{transfer_transaction.voucher_number}.",
        )
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    resolved_rows = None
    allocations = []

    if booking_mode == "match" or booking_mode in ALLOC_MODE_PREFIXES:
        prefix = "match" if booking_mode == "match" else ALLOC_MODE_PREFIXES[booking_mode]
        posted_allocations, posted_extras = _parse_invoice_mode_post(request, prefix=prefix)
        resolved_rows, allocations, error_message = _resolve_invoice_mode_booking(
            company=company,
            bank_tx=bank_tx,
            booking_mode=booking_mode,
            posted_allocations=posted_allocations,
            posted_extras=posted_extras,
            counter_account_choices=counter_account_choices,
        )
        if error_message is not None:
            messages.error(request, error_message)
            return _render_book_transaction_form(
                request,
                company=company,
                bank_tx=bank_tx,
                counter_account_choices=counter_account_choices,
                booking_mode=booking_mode,
                selected_attachment_ids=selected_attachment_ids,
                posted_invoice_allocations=posted_allocations,
                posted_invoice_extras=posted_extras,
                posted_description=posted_description,
            )

    else:
        booking_mode = "manual"
        account_ids = [v.strip().replace("\xa0", "") for v in request.POST.getlist("counter_account[]")]
        row_amounts = [v.strip() for v in request.POST.getlist("counter_amount[]")]

        rows = []
        for account_id, raw_amount in zip(account_ids, row_amounts):
            if not account_id and not raw_amount:
                continue
            if not account_id:
                messages.error(request, "Alla motrader måste ha konto valt.")
                return _render_book_transaction_form(
                    request,
                    company=company,
                    bank_tx=bank_tx,
                    counter_account_choices=counter_account_choices,
                    booking_mode=booking_mode,
                    selected_attachment_ids=selected_attachment_ids,
                    posted_account_ids=account_ids,
                    posted_amounts=row_amounts,
                    posted_description=posted_description,
                )
            try:
                parsed_amount = to_decimal(raw_amount)
            except Exception:
                messages.error(request, "Ogiltigt belopp i en motrad.")
                return _render_book_transaction_form(
                    request,
                    company=company,
                    bank_tx=bank_tx,
                    counter_account_choices=counter_account_choices,
                    booking_mode=booking_mode,
                    selected_attachment_ids=selected_attachment_ids,
                    posted_account_ids=account_ids,
                    posted_amounts=row_amounts,
                    posted_description=posted_description,
                )
            if parsed_amount <= 0:
                messages.error(request, "Belopp i motrader måste vara större än 0.")
                return _render_book_transaction_form(
                    request,
                    company=company,
                    bank_tx=bank_tx,
                    counter_account_choices=counter_account_choices,
                    booking_mode=booking_mode,
                    selected_attachment_ids=selected_attachment_ids,
                    posted_account_ids=account_ids,
                    posted_amounts=row_amounts,
                    posted_description=posted_description,
                )
            rows.append((account_id, parsed_amount))

        if not rows:
            messages.error(request, "Lägg till minst en motrad för att bokföra.")
            return _render_book_transaction_form(
                request,
                company=company,
                bank_tx=bank_tx,
                counter_account_choices=counter_account_choices,
                booking_mode=booking_mode,
                selected_attachment_ids=selected_attachment_ids,
                posted_account_ids=account_ids,
                posted_amounts=row_amounts,
                posted_description=posted_description,
            )

        counter_accounts = {str(acc.pk): acc for acc in counter_account_choices.filter(pk__in=[row[0] for row in rows])}
        if len(counter_accounts) != len({row[0] for row in rows}):
            messages.error(request, "Ett eller flera motkonton är ogiltiga.")
            return _render_book_transaction_form(
                request,
                company=company,
                bank_tx=bank_tx,
                counter_account_choices=counter_account_choices,
                booking_mode=booking_mode,
                selected_attachment_ids=selected_attachment_ids,
                posted_account_ids=account_ids,
                posted_amounts=row_amounts,
                posted_description=posted_description,
            )

        counter_total = sum((row[1] for row in rows), Decimal("0"))
        if counter_total != amount:
            messages.error(request, "Motradernas summa måste vara exakt samma som transaktionsbeloppet.")
            return _render_book_transaction_form(
                request,
                company=company,
                bank_tx=bank_tx,
                counter_account_choices=counter_account_choices,
                booking_mode=booking_mode,
                selected_attachment_ids=selected_attachment_ids,
                posted_account_ids=account_ids,
                posted_amounts=row_amounts,
                posted_description=posted_description,
            )

        resolved_rows = [(counter_accounts[account_id], row_amount) for account_id, row_amount in rows]

        # Best-effort invoice matching for manual bookings: only settle the suggested invoice
        # when the user actually posted against its receivable/payable account, and only with
        # the amount posted there.
        suggestion = get_quick_booking_suggestion(company=company, bank_tx=bank_tx)
        if suggestion is not None and suggestion.get("source_id"):
            matched_account = suggestion["counter_account"]
            matched_amount = sum(
                (row_amount for account, row_amount in resolved_rows if account.pk == matched_account.pk),
                Decimal("0.00"),
            )
            if matched_amount > Decimal("0.00"):
                allocations.append(
                    {
                        "source_type": suggestion["source_type"],
                        "source_id": suggestion["source_id"],
                        "amount": matched_amount,
                        "settlement_difference": Decimal("0.00"),
                        "strict": False,
                    }
                )

    selected_attachments = selectable_attachments(company, selected_attachment_ids)

    try:
        booked_txn = book_bank_transaction(
            bank_tx=bank_tx,
            rows=resolved_rows,
            user=request.user,
            allocations=allocations,
            description=posted_description,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    if selected_attachments.exists():
        booked_txn.attachments.add(*selected_attachments)

    messages.success(request, "Banktransaktionen har bokförts.")
    return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)


@login_required
@require_POST
@company_required
def quick_book_transaction(request, company, transaction_id):

    bank_tx = get_object_or_404(
        BankTransaction.objects.select_related("bank_account", "bank_account__bookkeeping_account"),
        pk=transaction_id,
        company=company,
    )

    if bank_tx.is_booked:
        messages.info(request, "Banktransaktionen är redan bokförd.")
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    suggestion = get_quick_booking_suggestion(company=company, bank_tx=bank_tx)
    if suggestion is None:
        messages.error(request, "Ingen snabbbokning finns för denna transaktion ännu.")
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    try:
        amount = abs(bank_tx.amount)
        source_type = suggestion.get("source_type")
        rows, settlement_difference = resolve_quick_booking_rows(
            company=company, suggestion=suggestion, payment_amount=amount
        )

        allocations = []
        if suggestion.get("source_id") and source_type in ALLOC_MODE_PREFIXES:
            allocations.append(
                {
                    "source_type": source_type,
                    "source_id": suggestion["source_id"],
                    "amount": amount,
                    "settlement_difference": settlement_difference,
                    "strict": True,
                }
            )

        book_bank_transaction(bank_tx=bank_tx, rows=rows, user=request.user, allocations=allocations)
    except ValueError as exc:
        messages.error(request, str(exc))
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    messages.success(
        request,
        f"Snabbbokföring klar enligt förslag: {suggestion['label']}.",
    )
    return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)


@login_required
@require_POST
@company_required
def delete_transaction(request, company, transaction_id):

    bank_tx = get_object_or_404(
        BankTransaction,
        pk=transaction_id,
        company=company,
    )

    if bank_tx.is_booked:
        messages.error(request, "Bokförda banktransaktioner kan inte tas bort.")
        return _transaction_list_redirect_for_bank_account(bank_tx.bank_account_id)

    bank_account_id = bank_tx.bank_account_id
    bank_tx.delete()
    messages.success(request, "Banktransaktionen togs bort.")
    return _transaction_list_redirect_for_bank_account(bank_account_id)


@login_required
@company_required
def payment_undo_confirm(request, company, transaction_id):

    payment_transaction = get_object_or_404(
        Transaction.objects.select_related("accounting_year").prefetch_related("entries__account", "corrections"),
        pk=transaction_id,
        accounting_year__company=company,
    )
    return_to = request.GET.get("return_to")
    if not is_safe_return_to(return_to):
        return_to = None
    fallback_url = return_to or reverse("bookkeeping:transaction_detail", args=[payment_transaction.pk])

    if payment_transaction.corrections.exists():
        messages.info(request, "Betalningsverifikationen har redan en registrerad korrigering.")
        return redirect(fallback_url)

    preview = get_payment_reversal_preview(payment_transaction, company=company)
    detail_url_names = {
        "Kundfaktura": ("invoicing:invoice_detail", "invoice_id"),
        "Leverantörsfaktura": ("supplier_invoices:invoice_detail", "invoice_id"),
        "Utlägg": ("expenses:expense_detail", "claim_id"),
    }
    affected = []
    for item in preview["affected"]:
        # This preview is the user's only view of what an undo will touch, so a payable
        # type without a detail route here must still be listed - just without a link.
        route = detail_url_names.get(item["label"])
        detail_url = reverse(route[0], kwargs={route[1]: item["object"].pk}) if route else None
        affected.append(
            {
                "label": item["label"],
                "object": item["object"],
                "detail_url": (
                    replace_query_param(detail_url, "return_to", request.get_full_path()) if detail_url else None
                ),
            }
        )

    return render(
        request,
        "banking/payment_undo_confirm.html",
        {
            "payment_transaction": payment_transaction,
            "affected": affected,
            "bank_transactions": preview["bank_transactions"],
            "return_to": return_to,
            "cancel_url": fallback_url,
        },
    )


@login_required
@require_POST
@company_required
def payment_undo(request, company, transaction_id):

    payment_transaction = get_object_or_404(
        Transaction,
        pk=transaction_id,
        accounting_year__company=company,
    )
    return_to = request.POST.get("return_to")
    if not is_safe_return_to(return_to):
        return_to = None
    fallback_url = return_to or reverse("bookkeeping:transaction_detail", args=[payment_transaction.pk])

    try:
        reversal = undo_bank_payment(payment_transaction, user=request.user, company=company)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(fallback_url)

    messages.success(
        request,
        f"Betalningen har ångrats. Korrigeringsverifikation: {reversal.voucher_series}{reversal.voucher_number}.",
    )
    return redirect(return_to or reverse("bookkeeping:transaction_detail", args=[reversal.pk]))
