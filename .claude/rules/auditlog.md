---
paths:
  - "auditlog/**"
  - "**/models.py"
---

### Audit log (`auditlog`)

- `TRACKED_MODELS` in `auditlog/services.py` registers every audited model (display name,
  `company_path`, `sensitive_fields`). New auditable model → register there (and in
  `CHILD_PARENT_RELATIONS` for line-item children).
- `auditlog/signals.py` snapshots before/after on save/delete; each `AuditLogEntry` hash-chains to
  the previous per company (`chain_key` = company pk, frozen at write time; `hash_version=1`
  entries predate the split and verify as one frozen global legacy chain). A per-chain
  `AuditChainTip` lock row serializes writers — don't replace it with a lock on the last entry,
  that's racy under READ COMMITTED. `auditlog/timestamping.py` anchors every chain tip via
  RFC 3161 (`AUDIT_CHAIN_TSA_URL`).
- `AuditUserMiddleware` binds the request user into context for the signal handlers.
- `auditlog.verify_chain` is `finance_admin`-gated — don't bypass or weaken the chain when
  refactoring audited models.
