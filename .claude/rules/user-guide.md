---
paths:
  - "docs/user-guide/**"
  - "bookkeeping/help_docs.py"
  - "templates/**"
---

### User documentation (`docs/user-guide/`)

The Swedish end-user manual, rendered live at `/hjalp/` (`bookkeeping/help_docs.py` reads the same
files — no separate copy). Chapters are verified against actual code (screens, field names, exact
error messages), not against `docs/system-replication-spec.md` (aspirational rebuild spec).

- A change that alters what a chapter describes (field, flow, error message, page, nav,
  validation/permission rule) updates the chapter **in the same change**.
- Heading anchors are auto-slugified but cross-linked by hand
  (`01-komma-igang.md#byta-aktivt-företag`) — grep other chapters for `#<old-slug>` before renaming
  a heading.
- Don't confuse with "Systemdokumentation" (`bookkeeping:system_documentation`) — that's the BFNAR
  2013:2 report generated from live data, not the manual.
