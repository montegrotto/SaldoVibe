from django.apps import apps
from django.db.models.signals import post_save, pre_delete, pre_save

from .models import AuditLogEntry
from .services import create_audit_log, get_tracked_models, snapshot_instance


def remember_previous_state(sender, instance, **kwargs):
    if instance.pk is None:
        instance._auditlog_before = None
        instance._auditlog_field_labels = None
        return

    previous = sender.objects.filter(pk=instance.pk).first()
    if previous is None:
        instance._auditlog_before = None
        instance._auditlog_field_labels = None
        return

    before_snapshot, field_labels = snapshot_instance(previous)
    instance._auditlog_before = before_snapshot
    instance._auditlog_field_labels = field_labels


def log_save(sender, instance, created, **kwargs):
    after_snapshot, field_labels = snapshot_instance(instance)
    before_snapshot = getattr(instance, "_auditlog_before", None)

    if created:
        create_audit_log(
            instance,
            AuditLogEntry.Action.CREATE,
            after=after_snapshot,
            field_labels=field_labels,
        )
        return

    create_audit_log(
        instance,
        AuditLogEntry.Action.UPDATE,
        before=before_snapshot,
        after=after_snapshot,
        field_labels=field_labels,
    )


def log_delete(sender, instance, **kwargs):
    before_snapshot, field_labels = snapshot_instance(instance)
    create_audit_log(
        instance,
        AuditLogEntry.Action.DELETE,
        before=before_snapshot,
        field_labels=field_labels,
    )


for model_label in get_tracked_models():
    model = apps.get_model(model_label)
    pre_save.connect(remember_previous_state, sender=model, dispatch_uid=f"auditlog_pre_save_{model_label}")
    post_save.connect(log_save, sender=model, dispatch_uid=f"auditlog_post_save_{model_label}")
    pre_delete.connect(log_delete, sender=model, dispatch_uid=f"auditlog_pre_delete_{model_label}")
