from django.db.models.signals import post_save

from bookkeeping.models import Account, Company

from .services import ensure_tax_account_for_company


def _ensure_tax_account(sender, instance, **kwargs):
    ensure_tax_account_for_company(instance)


post_save.connect(_ensure_tax_account, sender=Company, dispatch_uid="banking_company_ensure_tax_account")


def _ensure_tax_account_when_1630_created(sender, instance, **kwargs):
    if instance.number != "1630":
        return
    ensure_tax_account_for_company(instance.company)


post_save.connect(
    _ensure_tax_account_when_1630_created,
    sender=Account,
    dispatch_uid="banking_account_1630_ensure_tax_account",
)
