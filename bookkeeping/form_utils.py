from django import forms


def _normalize_decimal_value(value):
    if not isinstance(value, str):
        return value
    return value.replace("\xa0", "").replace(" ", "").replace(",", ".")


def normalize_decimal_fields(form):
    """Normalize bound DecimalField input so both comma and dot are accepted."""
    if not form.is_bound:
        return

    data = form.data.copy()
    changed = False

    for name, field in form.fields.items():
        if not isinstance(field, forms.DecimalField):
            continue

        key = form.add_prefix(name)
        values = data.getlist(key)
        if not values:
            continue

        normalized = [_normalize_decimal_value(value) for value in values]
        if normalized != values:
            data.setlist(key, normalized)
            changed = True

    if changed:
        form.data = data
