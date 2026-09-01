---
paths:
  - "bookkeeping/payables.py"
  - "invoicing/**"
  - "supplier_invoices/**"
  - "expenses/**"
  - "banking/**"
---

### Payables (`bookkeeping/payables.py`)

Customer invoices (`invoicing.Invoice`), supplier invoices (`supplier_invoices.SupplierInvoice`)
and expense claims (`expenses.ExpenseClaim`) are the same thing on the money side: a total, a
running paid amount, an öresavrundning write-off tolerance, and a manual "mark as paid" escape
hatch. That shared behaviour lives in one place — don't reimplement it per app.

- `PayableMixin` — the write-off constants (`PAYMENT_ROUNDING_WRITE_OFF_LIMIT` = 1,00 kr,
  `PAYMENT_ROUNDING_ACCOUNT_NUMBER` = 3740), `remaining_amount`, `settled_total`,
  `is_partially_paid`, `get_payment_write_off_difference/_account`. Contributes **no fields**: the
  concrete models keep their own `is_paid`/`paid_amount`/… declarations because verbose names
  differ ("Betald" vs "Utbetald").
- `AbstractPayment` — the per-payment history rows (`InvoicePayment`, `SupplierInvoicePayment`,
  `ExpenseClaimPayment`). All three name their FK to the settled document `payable`, which is what
  lets `banking.services._payable_type_registry()` resolve model and payment model together via
  `_meta.get_field("payable").related_model`. Keep the name identical across all three if you add a
  fourth — and note that `auditlog.TRACKED_MODELS` reaches the company through it
  (`company_path: "payable.company"`), as does `CHILD_PARENT_RELATIONS`. `InvoiceLine.invoice` and
  `SupplierInvoiceCostLine.invoice` are unrelated and keep their own name.
- `mark_payable_manually_paid` / `unmark_payable_manually_paid` + `payment_state_update_fields` —
  one implementation for all three. Each model supplies its own wording via `PAYMENT_LABELS`
  (`PayableLabels`) and names its ledger flag via `BOOKKEPT_FIELD`, because `Invoice` uses
  `is_booked`/`booked_at`/`booked_transaction` while the purchase-side documents use
  `is_registered`/`registered_at`/`registered_transaction`.

Both base classes are abstract and field-compatible with what the concrete models already had, so
adding to them is schema-neutral — verify with `makemigrations --check --dry-run`.
