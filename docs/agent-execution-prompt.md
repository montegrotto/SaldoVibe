# Agent Execution Prompt: Rebuild SaldoVibe-Equivalent System

Use this prompt with another coding agent to implement a functionally equivalent system.

You are tasked with implementing a Swedish accounting platform functionally equivalent to SaldoVibe.

Primary spec to follow:
- docs/system-replication-spec.md

Mandatory constraints:
- Preserve Swedish domain terminology in routes and UI labels.
- Use Django 4.2+ architecture with app boundaries per domain.
- Implement all domain entities and business rules described in the spec.
- Enforce accounting integrity: balanced journal entries, voucher sequencing, period lock checks.
- Implement audit logging with hash chaining (prev_hash + entry_hash).
- Implement exports/reports: SIE4, SRU, VAT export, AGI evidence package.
- Implement attachment handling with thumbnail generation and soft delete + legal hold fields.
- Implement payroll flow with Skatteverket tax calculation integration and evidence lock flow.

Execution plan:
1. Read docs/system-replication-spec.md fully.
2. Create/update architecture docs summarizing module boundaries and data model.
3. Implement Phase 1 from the spec (core ledger) completely, including tests.
4. Implement Phase 2 (invoicing + attachments), including tests.
5. Implement Phase 3 (banking + VAT), including tests.
6. Implement Phase 4 (payroll + fixed assets), including tests.
7. Implement Phase 5 (compliance hardening), including tests and management commands.
8. Verify all Definition of Done criteria in the spec and produce evidence.

Output artifacts required:
- Updated source code and migrations.
- tests/ coverage for all critical paths listed in the spec.
- A report file at docs/implementation-report.md containing:
  - completed scope
  - unresolved gaps (if any)
  - test results summary
  - mapping from each acceptance criterion to proof (test name or command output)

Quality gates before completion:
- No unbalanced transaction can be posted.
- Period lock blocks all posting flows (manual, import, invoicing, payroll).
- VAT close snapshot is persisted with deterministic source fingerprint.
- Payroll report evidence includes stable payload hash and immutable reported marker behavior.
- Audit hash chain verification command passes.

Suggested validation commands:
- python manage.py makemigrations --check
- python manage.py migrate
- python manage.py test
- python manage.py verify_audit_chain

Delivery rule:
Do not stop at planning. Implement end-to-end and only finish when all feasible acceptance criteria are satisfied or explicit blockers are documented in docs/implementation-report.md.
