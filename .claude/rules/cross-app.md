---
paths:
  - "supplier_invoices/**"
  - "invoicing/**"
  - "expenses/**"
  - "banking/**"
  - "bookkeeping/**"
  - "attachments/**"
  - "static/js/**"
  - "templates/**"
---

### Cross-app change checklist

Attachment handling, `return_to` redirects, and payment/booking logic are mirrored across
`supplier_invoices`, `invoicing`, `expenses`, `banking`, and `bookkeeping` manual verifications
(plus report/VAT templates). When changing a shared pattern, grep all of them and fix every
occurrence in the same pass.

Already consolidated — fix once, not per app:

- Payment state: `bookkeeping/payables.py`.
- Picker JS: `static/js/attachment-picker.js`.
- Settlement rows: `banking.services.build_payable_booking_rows` (the three `build_*_booking_rows`
  are thin wrappers picking only the settlement account and side; the write-off row always faces it).
- Attachment plumbing: `attachments/view_helpers.py` (`add_attachments`/`remove_attachment` back
  all eight add/remove views; `attachment_panel_context`, `selectable_attachments`). An
  attachment-touching view should be four or five lines — fetch the document, delegate.
