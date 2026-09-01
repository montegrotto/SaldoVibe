from urllib.parse import parse_qs, urlparse, urlunparse

from django.utils.http import url_has_allowed_host_and_scheme, urlencode


def is_safe_return_to(value):
    """Only allow local paths as a return_to target, guarding against open redirects."""
    # url_has_allowed_host_and_scheme rejects //host, /\host and scheme tricks a bare
    # startswith("/") lets through.
    return (
        isinstance(value, str) and value.startswith("/") and url_has_allowed_host_and_scheme(value, allowed_hosts=None)
    )


def replace_query_param(path, key, value):
    """Return path with the given query param set to value (or removed if falsy)."""
    parsed = urlparse(path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if value:
        query[key] = [value]
    else:
        query.pop(key, None)
    new_query = urlencode(query, doseq=True)
    return urlunparse(("", "", parsed.path or path, "", new_query, ""))


def parse_attachment_ids(raw_value):
    """Parse a comma-separated string of attachment IDs into a de-duplicated, ordered list of ints."""
    if not raw_value:
        return []
    ids = []
    for part in str(raw_value).split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return list(dict.fromkeys(ids))


def exclude_used_attachments(queryset):
    """Exclude attachments already linked to a supplier invoice, sales invoice, verifikation, or expense claim.

    Chained as separate .exclude() calls (not one filter with Q-ORs) so each M2M reverse
    relation gets its own subquery and can't fan out into a cross-join with the others.
    """
    return (
        queryset.exclude(supplier_invoices__isnull=False)
        .exclude(sales_invoices__isnull=False)
        .exclude(transactions__isnull=False)
        .exclude(expense_claims__isnull=False)
    )
