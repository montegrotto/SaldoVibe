"""Shared payment behaviour for documents that get settled by a payment.

A "payable" here is any document that carries a balance someone eventually settles —
customer invoices, supplier invoices and expense claims. They post different journal
entries and use different nouns in the UI, but the money side is identical: a total, a
running paid amount, an öresavrundning write-off tolerance, and a manual "mark as paid"
escape hatch for payments that will never show up as a bank transaction.

Keeping that logic here means the write-off limit and the rounding account cannot drift
apart between the three flows.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction
from django.utils import timezone


def quantize_amount(value):
    """Round to whole öre, the precision every stored amount uses."""
    return (value or Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def add_journal_entry(*, transaction, account, debit, credit, description):
    """Post one debit/credit line. Every posting service builds its lines this way."""
    from bookkeeping.models import JournalEntry

    return JournalEntry.objects.create(
        transaction=transaction,
        account=account,
        debit=debit,
        credit=credit,
        description=description,
    )


def validate_payable_registration(document, *, date, total_amount, vat_amount, component_total, labels):
    """The checks every purchase-side registration runs before posting.

    labels: dict with the per-app wording for "total_not_positive", "component_sum_mismatch"
    and "period_locked". The VAT-account message is identical across apps.
    """
    from bookkeeping.period_locking import is_date_locked

    if total_amount <= Decimal("0"):
        raise ValidationError(labels["total_not_positive"])
    if vat_amount > Decimal("0") and not document.vat_account_id:
        raise ValidationError("Momskonto krävs när momsbelopp är större än 0.")
    if component_total + vat_amount != total_amount:
        raise ValidationError(labels["component_sum_mismatch"])
    if is_date_locked(document.company, date):
        raise ValidationError(labels["period_locked"])


def post_payable_registration(document, user, *, date, description, source, rows, reference=""):
    """Create the voucher for a purchase-side document and flag it registered.

    rows: (account, debit, credit, description) tuples; must balance. The document must
    use the is_registered/registered_at/registered_transaction field trio.
    """
    from bookkeeping.models import Transaction

    with db_transaction.atomic():
        # Radlås + omkontroll: två samtidiga (eller dubbelklickade) bokföringar får en verifikation, inte två.
        locked = type(document).objects.select_for_update().get(pk=document.pk)
        if locked.is_registered:
            return locked.registered_transaction
        txn = Transaction.objects.create(
            accounting_year=document.accounting_year,
            date=date,
            description=description,
            reference=reference,
            created_by=user,
            source=source,
        )
        for account, debit, credit, row_description in rows:
            add_journal_entry(transaction=txn, account=account, debit=debit, credit=credit, description=row_description)
        txn.validate_balanced()

        document.is_registered = True
        document.registered_at = timezone.now()
        document.registered_transaction = txn
        document.save(update_fields=["is_registered", "registered_at", "registered_transaction", "updated_at"])
        return txn


@dataclass(frozen=True)
class PayableLabels:
    """User-facing messages for the manual-payment flow.

    Each payable type says the same things in its own vocabulary — "fakturan"
    vs "utlägget", "betald" vs "utbetalt" — so the wording travels with the model
    instead of being re-implemented alongside the logic.
    """

    not_bookkept: str
    already_paid: str
    not_manually_paid: str
    payment_registered: str = "Betalningen har registrerats och bokförts."
    unmarked_paid: str = "Den manuella betalningsmarkeringen har ångrats."


class PayableMixin(models.Model):
    """Payment state shared by Invoice, SupplierInvoice and ExpenseClaim.

    Contributes no fields: the concrete models keep their own `is_paid`/`paid_amount`/...
    declarations because their verbose names differ ("Betald" vs "Utbetald"). Subclasses
    must provide `total_amount`, the payment fields, `BOOKKEPT_FIELD`, `PAYMENT_LABELS`
    and `PAYABLE_LABEL`.
    """

    # Öresavrundning: a shortfall up to this much is written off to
    # PAYMENT_ROUNDING_ACCOUNT_NUMBER instead of leaving the document part-paid.
    PAYMENT_ROUNDING_WRITE_OFF_LIMIT = Decimal("1.00")
    PAYMENT_ROUNDING_ACCOUNT_NUMBER = "3740"

    # Suggested account for writing off a remaining balance in the manual payment
    # form. Invoice overrides with 6351 (konstaterade kundförluster).
    PAYMENT_WRITE_OFF_DEFAULT_ACCOUNT = "3740"

    # Whether the manual payment form offers splitting a write-off into net + VAT
    # (moms återtas vid konstaterad kundförlust). Only customer invoices support it.
    PAYMENT_VAT_ADJUST = False

    # FK name of the counterparty whose open counter-sign documents can be offset
    # (kvittas) against this one. None = the type has no kvittning (expense claims).
    COUNTERPARTY_FIELD = None

    # Name of the payable type, in singular, for lists that mix all three types —
    # the payment-undo preview in banking. Concrete models override it.
    PAYABLE_LABEL = "Post"

    # Name of the flag meaning "posted to the ledger". Customer invoices call this
    # is_booked, the purchase-side documents call it is_registered.
    BOOKKEPT_FIELD = "is_registered"

    PAYMENT_LABELS = PayableLabels(
        not_bookkept="Posten måste vara bokförd innan den kan markeras som betald.",
        already_paid="Posten är redan markerad som betald.",
        not_manually_paid="Posten är inte manuellt markerad som betald.",
    )

    # (label, badge css class) per payment state, for the status badge shown on list/detail
    # pages. Concrete models override entries where their wording or color scheme differs.
    PAYMENT_STATUS_BADGES = {
        "paid": ("Betald", "bg-success"),
        "partial": ("Delbetald", "bg-info text-dark"),
        "bookkept": ("Bokförd", "bg-warning text-dark"),
        "draft": ("Utkast", "bg-secondary"),
    }

    class Meta:
        abstract = True

    @staticmethod
    def _amount(value):
        return quantize_amount(value)

    @property
    def is_bookkept(self):
        return bool(getattr(self, self.BOOKKEPT_FIELD))

    def delete(self, *args, **kwargs):
        # Ett bokfört dokument är underlaget till sin verifikation (räkenskapsinformation
        # enligt BFL) — vyerna vägrar redan, det här är skyddsnätet för alla andra vägar.
        # Kaskadradering (t.ex. Company.delete) går via Djangos collector och berörs inte.
        if self.is_bookkept:
            raise ValidationError("Bokförda dokument kan inte tas bort. Skapa en korrigering istället.")
        return super().delete(*args, **kwargs)

    @property
    def payment_status_badge(self):
        if self.is_paid:
            key = "paid"
        elif self.is_partially_paid:
            key = "partial"
        elif self.is_bookkept:
            key = "bookkept"
        else:
            key = "draft"
        label, css_class = self.PAYMENT_STATUS_BADGES[key]
        return {"label": label, "css_class": css_class}

    @property
    def settled_total(self):
        """The document's total as a positive amount.

        Credit invoices carry a negative total; everything about payment tracking is
        expressed as a magnitude, so normalise here rather than at each call site.
        """
        return self._amount(abs(self.total_amount or Decimal("0.00")))

    @property
    def remaining_amount(self):
        if self.is_paid:
            return Decimal("0.00")
        remaining = self.settled_total - self._amount(self.paid_amount)
        return remaining if remaining > Decimal("0.00") else Decimal("0.00")

    @property
    def is_partially_paid(self):
        return self._amount(self.paid_amount) > Decimal("0.00") and not self.is_paid

    def get_payment_write_off_difference(self, payment_amount):
        remaining_amount = self.remaining_amount
        payment_amount = self._amount(payment_amount)
        if payment_amount >= remaining_amount:
            return Decimal("0.00")
        difference = self._amount(remaining_amount - payment_amount)
        if difference <= Decimal("0.00"):
            return Decimal("0.00")
        if difference > self.PAYMENT_ROUNDING_WRITE_OFF_LIMIT:
            return Decimal("0.00")
        return difference

    def get_payment_write_off_account(self):
        return self.company.accounts.filter(is_active=True, number=self.PAYMENT_ROUNDING_ACCOUNT_NUMBER).first()

    def payment_settlement(self):
        """(account, side) for the row that clears this document's balance when paid.

        Implemented by each concrete model — the account field and the debit/credit
        side differ per type (kundfordran krediteras, leverantörsskuld debiteras).
        """
        raise NotImplementedError

    def unmark_manually_paid(self, user):
        return unmark_payable_manually_paid(self, user)


class AbstractPayment(models.Model):
    """One registered (partial) payment against a payable.

    Keeps history when the payable's latest-payment fields are overwritten. Subclasses
    add the FK to their own payable - named `payable` on all three, which is what lets
    `banking.services._payable_type_registry()` walk them generically - plus the
    `transaction` and `payment_account` FKs, which need per-app related names.
    """

    amount = models.DecimalField("Belopp", max_digits=15, decimal_places=2)
    write_off_amount = models.DecimalField("Avskrivet belopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    payment_date = models.DateField("Betalningsdatum")
    created_at = models.DateTimeField(auto_now_add=True)
    reversed_at = models.DateTimeField("Ångrad vid", null=True, blank=True)
    reversal_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Korrigeringsverifikation",
    )

    class Meta:
        abstract = True
        ordering = ["payment_date", "id"]

    def __str__(self):
        return f"{self.payable} {self.payment_date} {self.amount}"


def payment_state_update_fields(model, extra_fields=()):
    """The payment fields to pass to save(update_fields=...), plus updated_at where it exists."""
    fields = [
        "paid_amount",
        "is_paid",
        "paid_at",
        "payment_date",
        *extra_fields,
    ]
    if any(field.name == "updated_at" for field in model._meta.fields):
        fields.append("updated_at")
    return fields


def reapply_payment_state(obj, payment_model):
    """Recompute an object's paid_amount/is_paid/... from its non-reversed payment rows."""
    remaining_rows = list(
        payment_model.objects.filter(payable=obj, reversed_at__isnull=True).order_by("payment_date", "id")
    )
    total_paid = sum((row.amount for row in remaining_rows), Decimal("0.00"))
    total_write_off = sum((row.write_off_amount for row in remaining_rows), Decimal("0.00"))
    total_amount = obj.settled_total
    effective_paid = (total_paid + total_write_off).quantize(Decimal("0.01"))

    obj.paid_amount = total_paid.quantize(Decimal("0.01"))
    obj.is_paid = total_amount > Decimal("0.00") and effective_paid >= total_amount

    latest_row = remaining_rows[-1] if remaining_rows else None
    obj.paid_at = timezone.now() if obj.is_paid else None
    obj.payment_date = latest_row.payment_date if latest_row else None
    obj.payment_account = latest_row.payment_account if latest_row else None
    obj.payment_transaction = latest_row.transaction if latest_row else None

    obj.save(
        update_fields=payment_state_update_fields(
            type(obj),
            extra_fields=("payment_account", "payment_transaction"),
        )
    )


def _validate_open_payment_period(company, payment_date):
    from bookkeeping.period_locking import is_date_locked

    if payment_date is None:
        raise ValidationError("Betalningsdatum krävs.")
    if is_date_locked(company, payment_date):
        raise ValidationError("Perioden för betalningsdatumet är låst. Välj ett datum i en öppen period.")


def _create_payment_transaction(*, company, user, payment_date, description, rows):
    """One balanced verifikation from (account, amount, side) rows, in the date's year."""
    from banking.services import get_accounting_year_for_date
    from bookkeeping.models import Transaction, TransactionSource

    try:
        accounting_year = get_accounting_year_for_date(company=company, date_value=payment_date)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    description = description[:500]
    txn = Transaction.objects.create(
        accounting_year=accounting_year,
        date=payment_date,
        description=description,
        created_by=user,
        source=TransactionSource.MANUAL,
    )
    for account, amount, side in rows:
        if amount <= Decimal("0.00"):
            continue
        add_journal_entry(
            transaction=txn,
            account=account,
            debit=amount if side == "debit" else Decimal("0.00"),
            credit=amount if side == "credit" else Decimal("0.00"),
            description=description[:300],
        )
    txn.validate_balanced()
    return txn


def _opposite_side(side):
    return "credit" if side == "debit" else "debit"


def _write_off_rows(payable, *, write_off_amount, write_off_account, side, adjust_vat):
    """The row(s) that absorb a written-off remainder, on the given side.

    For customer invoices with adjust_vat, the write-off is split per VAT rate into a
    net part (the chosen account, typically 6351 konstaterade kundförluster) and a VAT
    part that reclaims the previously reported output VAT — Skatteverket allows reducing
    utgående moms by the VAT share of a confirmed kundförlust. Allocation across rates
    is proportional to each rate bucket's share of the invoice total.
    """
    if not (adjust_vat and payable.PAYMENT_VAT_ADJUST):
        return [(write_off_account, write_off_amount, side)]

    from invoicing.services import _vat_account_number_for_rate

    buckets = [bucket for bucket in payable.vat_summary if abs(bucket["base"] + bucket["vat"]) > Decimal("0.00")]
    total_incl = sum((abs(bucket["base"] + bucket["vat"]) for bucket in buckets), Decimal("0.00"))
    if total_incl <= Decimal("0.00") or not any(abs(bucket["vat"]) > Decimal("0.00") for bucket in buckets):
        return [(write_off_account, write_off_amount, side)]

    amounts_by_account = {}
    remaining = write_off_amount
    for index, bucket in enumerate(buckets):
        if index == len(buckets) - 1:
            share = remaining
        else:
            share = quantize_amount(write_off_amount * abs(bucket["base"] + bucket["vat"]) / total_incl)
        remaining -= share
        rate = bucket["rate"] or Decimal("0.00")
        vat_part = quantize_amount(share * rate / (Decimal("100") + rate))
        if vat_part > Decimal("0.00"):
            vat_account_number = _vat_account_number_for_rate(rate)
            if vat_account_number is None:
                raise ValidationError(f"Moms {rate}% stöds inte för momsjustering vid avskrivning.")
            vat_account = payable.company.accounts.filter(is_active=True, number=vat_account_number).first()
            if vat_account is None:
                raise ValidationError(f"Momskonto {vat_account_number} saknas i kontoplanen.")
            amounts_by_account[vat_account] = amounts_by_account.get(vat_account, Decimal("0.00")) + vat_part
        net_part = share - vat_part
        if net_part > Decimal("0.00"):
            amounts_by_account[write_off_account] = (
                amounts_by_account.get(write_off_account, Decimal("0.00")) + net_part
            )

    return [(account, amount, side) for account, amount in amounts_by_account.items()]


def register_manual_payment(
    payable,
    user,
    *,
    payment_date,
    amount,
    payment_account=None,
    write_off_amount=Decimal("0.00"),
    write_off_account=None,
    adjust_vat=False,
):
    """Register a payment (and/or write-off) against a payable, posting a real verifikation.

    Replaces the old flag-only "markera som betald": every settlement now hits the ledger,
    so the reskontra and huvudbok can't drift apart. amount goes to payment_account
    (kassa/bank/eget konto), write_off_amount to write_off_account (öresavrundning,
    kundförlust, rabatt ...). Fully paid when they cover the remaining balance together.
    Undo goes through banking's payment-undo flow like any other payment verification.
    """
    labels = payable.PAYMENT_LABELS
    amount = quantize_amount(amount)
    write_off_amount = quantize_amount(write_off_amount)

    if not payable.is_bookkept:
        raise ValidationError(labels.not_bookkept)
    if payable.is_paid:
        raise ValidationError(labels.already_paid)
    _validate_open_payment_period(payable.company, payment_date)
    if amount < Decimal("0.00") or write_off_amount < Decimal("0.00"):
        raise ValidationError("Beloppen kan inte vara negativa.")
    if amount + write_off_amount <= Decimal("0.00"):
        raise ValidationError("Ange ett betalbelopp och/eller ett avskrivningsbelopp.")
    if amount > Decimal("0.00") and payment_account is None:
        raise ValidationError("Välj ett betalkonto för betalbeloppet.")
    if write_off_amount > Decimal("0.00") and write_off_account is None:
        raise ValidationError("Välj ett konto för avskrivningsbeloppet.")

    settlement_account, settlement_side = payable.payment_settlement()
    if settlement_account is None:
        raise ValidationError("Dokumentet saknar reskontrakonto och kan inte regleras.")

    model = type(payable)

    with db_transaction.atomic():
        payable = model.objects.select_for_update().get(pk=payable.pk)
        if payable.is_paid:
            raise ValidationError(labels.already_paid)
        if amount + write_off_amount > payable.remaining_amount:
            raise ValidationError(
                f"Betalning och avskrivning ({amount + write_off_amount:.2f} kr) överstiger "
                f"återstående belopp ({payable.remaining_amount:.2f} kr)."
            )

        rows = [(settlement_account, amount + write_off_amount, settlement_side)]
        if amount > Decimal("0.00"):
            rows.append((payment_account, amount, _opposite_side(settlement_side)))
        if write_off_amount > Decimal("0.00"):
            rows.extend(
                _write_off_rows(
                    payable,
                    write_off_amount=write_off_amount,
                    write_off_account=write_off_account,
                    side=_opposite_side(settlement_side),
                    adjust_vat=adjust_vat,
                )
            )

        txn = _create_payment_transaction(
            company=payable.company,
            user=user,
            payment_date=payment_date,
            description=f"Betalning {payable.PAYABLE_LABEL.lower()} {payable}",
            rows=rows,
        )
        payment_model = payable.payments.model
        payment_model.objects.create(
            payable=payable,
            transaction=txn,
            amount=amount,
            write_off_amount=write_off_amount,
            payment_date=payment_date,
            payment_account=payment_account,
        )
        reapply_payment_state(payable, payment_model)
        return txn


def offsettable_counterparts(payable):
    """Open counter-sign documents of the same type and counterparty — kvittning candidates."""
    model = type(payable)
    if payable.COUNTERPARTY_FIELD is None or not payable.is_bookkept or payable.is_paid:
        return model.objects.none()

    total = payable.total_amount or Decimal("0.00")
    if total == Decimal("0.00"):
        return model.objects.none()

    candidates = model.objects.filter(
        company=payable.company,
        is_paid=False,
        **{
            payable.BOOKKEPT_FIELD: True,
            f"{payable.COUNTERPARTY_FIELD}_id": getattr(payable, f"{payable.COUNTERPARTY_FIELD}_id"),
        },
    ).exclude(pk=payable.pk)

    # Customer invoice totals are computed from lines, so the sign filter runs in Python.
    return [candidate for candidate in candidates if (candidate.total_amount or Decimal("0.00")) * total < 0]


def offset_payables(payable, counterpart, user, *, payment_date):
    """Kvitta two counter-sign documents (debet- mot kreditfaktura) against each other.

    Posts one verifikation moving min(remaining, remaining) between the two documents'
    reskontrakonton — debit on the credit invoice's side, credit on the debit invoice's —
    and registers a payment row on both, exactly like Fortnox's kvittningsflöde. Both
    documents end up paid when their remaining balances match.
    """
    labels = payable.PAYMENT_LABELS
    if type(counterpart) is not type(payable) or counterpart.company_id != payable.company_id:
        raise ValidationError("Kvittning kan bara göras mellan dokument av samma typ och företag.")
    for document in (payable, counterpart):
        if not document.is_bookkept:
            raise ValidationError(labels.not_bookkept)
        if document.is_paid:
            raise ValidationError(labels.already_paid)
    _validate_open_payment_period(payable.company, payment_date)

    model = type(payable)

    with db_transaction.atomic():
        # Lock in pk order so two concurrent kvittningar of the same pair can't deadlock.
        first_pk, second_pk = sorted([payable.pk, counterpart.pk])
        locked = {doc.pk: doc for doc in (model.objects.select_for_update().get(pk=pk) for pk in (first_pk, second_pk))}
        payable, counterpart = locked[payable.pk], locked[counterpart.pk]

        account_a, side_a = payable.payment_settlement()
        account_b, side_b = counterpart.payment_settlement()
        if account_a is None or account_b is None:
            raise ValidationError("Dokumentet saknar reskontrakonto och kan inte kvittas.")
        if side_a == side_b:
            raise ValidationError("Kvittning kräver en debetfaktura och en kreditfaktura.")

        offset_amount = min(payable.remaining_amount, counterpart.remaining_amount)
        if offset_amount <= Decimal("0.00"):
            raise ValidationError("Det finns inget återstående belopp att kvitta.")

        txn = _create_payment_transaction(
            company=payable.company,
            user=user,
            payment_date=payment_date,
            description=f"Kvittning {counterpart} mot {payable}",
            rows=[(account_a, offset_amount, side_a), (account_b, offset_amount, side_b)],
        )
        payment_model = payable.payments.model
        for document in (payable, counterpart):
            payment_model.objects.create(
                payable=document,
                transaction=txn,
                amount=offset_amount,
                write_off_amount=Decimal("0.00"),
                payment_date=payment_date,
                payment_account=None,
            )
            reapply_payment_state(document, payment_model)
        return txn


def unmark_payable_manually_paid(payable, user):
    """Undo a manual paid marking. Refuses when a real payment verification settled it."""
    if not payable.is_paid or payable.payment_transaction_id is not None:
        raise ValidationError(payable.PAYMENT_LABELS.not_manually_paid)

    model = type(payable)

    with db_transaction.atomic():
        payable.paid_amount = Decimal("0.00")
        payable.is_paid = False
        payable.paid_at = None
        payable.payment_date = None
        payable.save(update_fields=payment_state_update_fields(model))
        return payable
