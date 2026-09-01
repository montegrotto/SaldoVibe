from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from bookkeeping.company_scope import require_company

from .models import AuditLogEntry
from .services import CHILD_PARENT_RELATIONS, TRACKED_MODELS

company_required = require_company(error_message=None)


def _is_unresolvable(value):
    # Covers both the legacy "[redacted]" marker and the keyed "[redacted:…]" form.
    return value in (None, "") or (isinstance(value, str) and value.startswith("[redacted"))


def _resolve_relation_display(entry, field_name, value):
    if _is_unresolvable(value):
        return value

    try:
        model = apps.get_model(entry.model_label)
    except (LookupError, ValueError):
        return value

    try:
        field = model._meta.get_field(field_name)
    except Exception:
        return value

    if not getattr(field, "is_relation", False) or not getattr(field, "many_to_one", False):
        return value

    remote_model = field.remote_field.model
    try:
        related_object = remote_model._default_manager.filter(pk=value).first()
    except Exception:
        return value

    return str(related_object) if related_object is not None else value


def _get_main_model_choices():
    child_labels = set(CHILD_PARENT_RELATIONS.keys())
    return [
        {"value": key, "label": config["name"]}
        for key, config in sorted(TRACKED_MODELS.items(), key=lambda item: item[1]["name"])
        if key not in child_labels
    ]


def _coerce_object_pk(value):
    if _is_unresolvable(value):
        return None
    return str(value)


def _get_parent_reference(entry):
    relation_config = CHILD_PARENT_RELATIONS.get(entry.model_label)
    if relation_config is None:
        return {
            "model_label": entry.model_label,
            "object_pk": entry.object_pk,
            "object_repr": entry.object_repr,
        }

    metadata_parent = (entry.metadata or {}).get("parent") or {}
    if metadata_parent.get("model_label") and metadata_parent.get("object_pk"):
        return metadata_parent

    field_name = relation_config["field"]
    field_change = (entry.changes or {}).get(field_name, {})
    parent_pk = _coerce_object_pk(field_change.get("after")) or _coerce_object_pk(field_change.get("before"))
    parent_repr = ""

    try:
        model = apps.get_model(entry.model_label)
        related_field = model._meta.get_field(field_name)
    except Exception:
        related_field = None

    if related_field is not None and parent_pk is not None:
        try:
            related_object = related_field.remote_field.model._default_manager.filter(pk=parent_pk).first()
        except Exception:
            related_object = None
        if related_object is not None:
            parent_repr = str(related_object)

    return {
        "model_label": relation_config["parent_label"],
        "object_pk": parent_pk or entry.object_pk,
        "object_repr": parent_repr,
    }


def _build_change_rows(entry):
    field_labels = (entry.metadata or {}).get("field_labels", {})
    rows = []
    for field_name, values in (entry.changes or {}).items():
        before = _resolve_relation_display(entry, field_name, values.get("before"))
        after = _resolve_relation_display(entry, field_name, values.get("after"))
        rows.append(
            {
                "field_name": field_name,
                "field_label": field_labels.get(field_name, field_name),
                "before": before,
                "after": after,
                "value": after if entry.action == AuditLogEntry.Action.CREATE else before,
            }
        )
    rows.sort(key=lambda row: row["field_label"])
    return rows


def _build_report_groups(entries):
    groups = []
    groups_by_key = {}

    for entry in entries:
        parent_ref = _get_parent_reference(entry)
        key = (parent_ref["model_label"], str(parent_ref["object_pk"]))
        group = groups_by_key.get(key)
        if group is None:
            group = {
                "group_key": key,
                "parent_model_label": parent_ref["model_label"],
                "parent_object_pk": str(parent_ref["object_pk"]),
                "parent_object_repr": parent_ref.get("object_repr") or entry.object_repr,
                "primary_entry": None,
                "parent_entries": [],
                "child_entries": [],
                "latest_entry": entry,
            }
            groups_by_key[key] = group
            groups.append(group)

        if (entry.occurred_at, entry.id) > (group["latest_entry"].occurred_at, group["latest_entry"].id):
            group["latest_entry"] = entry

        if entry.model_label == group["parent_model_label"]:
            group["parent_entries"].append(entry)
            if group["primary_entry"] is None:
                group["primary_entry"] = entry
            if not group["parent_object_repr"] or group["parent_object_repr"] == entry.object_repr:
                group["parent_object_repr"] = entry.object_repr
        else:
            group["child_entries"].append(entry)

    for group in groups:
        if group["primary_entry"] is None:
            group["primary_entry"] = group["child_entries"][0]
        group["parent_entries"].sort(key=lambda item: (item.occurred_at, item.id), reverse=True)
        group["child_entries"].sort(key=lambda item: (item.occurred_at, item.id), reverse=True)

    groups.sort(key=lambda item: (item["latest_entry"].occurred_at, item["latest_entry"].id), reverse=True)
    return groups


@login_required
@company_required
def audit_log_report(request, company):

    action = (request.GET.get("action") or "").strip()
    model_label = (request.GET.get("model") or "").strip()
    search_query = (request.GET.get("q") or "").strip()
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()

    entries = AuditLogEntry.objects.filter(company=company).select_related("actor", "company")

    model_filter_labels = None
    if model_label:
        model_filter_labels = {model_label}
        for child_label, relation in CHILD_PARENT_RELATIONS.items():
            if relation["parent_label"] == model_label:
                model_filter_labels.add(child_label)

    if action:
        entries = entries.filter(action=action)
    if model_filter_labels:
        entries = entries.filter(model_label__in=model_filter_labels)
    if search_query:
        entries = entries.filter(
            Q(summary__icontains=search_query)
            | Q(object_repr__icontains=search_query)
            | Q(actor_display__icontains=search_query)
        )
    if date_from:
        entries = entries.filter(occurred_at__date__gte=date_from)
    if date_to:
        entries = entries.filter(occurred_at__date__lte=date_to)

    entry_list = list(entries.order_by("-occurred_at", "-id"))
    for entry in entry_list:
        entry.change_rows = _build_change_rows(entry)

    report_groups = _build_report_groups(entry_list)

    paginator = Paginator(report_groups, 100)
    page_obj = paginator.get_page(request.GET.get("page"))

    model_choices = _get_main_model_choices()

    return render(
        request,
        "auditlog/report.html",
        {
            "company": company,
            "page_obj": page_obj,
            "groups": list(page_obj.object_list),
            "action_choices": AuditLogEntry.Action.choices,
            "model_choices": model_choices,
            "selected_action": action,
            "selected_model": model_label,
            "search_query": search_query,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
