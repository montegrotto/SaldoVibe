"""Shared account balance lookup for account-picker widgets (Select2 fields
that show each account's current saldo next to its name).

Resultatkonton (P&L accounts, BAS-klass 3-10) are flow accounts that reset to
zero each räkenskapsår, so their balance must be scoped to the accounting year
containing the transaction being entered. Balanskonton (klass 1-2) carry a
balance forward across years, so they use the all-time sum.

Both are also capped at `reference_date`: this is meant to show the balance as
of the transaction being entered (e.g. an old, backdated bank transaction
booked out of a backlog today), not today's balance regardless of how old the
transaction is — otherwise the preview includes journal entries dated after
the transaction and can't be reconciled against a historical bank statement.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Account, AccountClass, AccountingYear, JournalEntry

RESULT_ACCOUNT_CLASSES = {
    AccountClass.REVENUE,
    AccountClass.COST_OF_GOODS,
    AccountClass.OTHER_EXTERNAL,
    AccountClass.OTHER_EXTERNAL_2,
    AccountClass.PERSONNEL,
    AccountClass.FINANCIAL,
    AccountClass.RESULTS_AND_TAX,
    AccountClass.YEAR_END,
}


def build_account_balances(company, reference_date=None):
    """Return {str(account_id): str(balance)} for every account in `company`, as of `reference_date`.

    `reference_date` selects the räkenskapsår used for resultatkonto balances and
    excludes journal entries dated after it from both resultat- and balanskonto sums.
    Defaults to today, matching the date a new transaction is initially proposed with.
    """
    if reference_date is None:
        reference_date = timezone.localdate()

    accounting_year = (
        AccountingYear.objects.filter(
            company=company,
            start_date__lte=reference_date,
            end_date__gte=reference_date,
        )
        .order_by("-start_date", "-id")
        .first()
    )

    all_time_balance_rows = (
        JournalEntry.objects.filter(account__company=company, transaction__date__lte=reference_date)
        .values("account_id")
        .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    )
    all_time_balances = {
        row["account_id"]: (row["total_debit"] or Decimal("0")) - (row["total_credit"] or Decimal("0"))
        for row in all_time_balance_rows
    }

    year_balances = {}
    if accounting_year is not None:
        year_balance_rows = (
            JournalEntry.objects.filter(
                account__company=company,
                transaction__accounting_year=accounting_year,
                transaction__date__lte=reference_date,
            )
            .values("account_id")
            .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
        )
        year_balances = {
            row["account_id"]: (row["total_debit"] or Decimal("0")) - (row["total_credit"] or Decimal("0"))
            for row in year_balance_rows
        }

    account_balances = {}
    for account in Account.objects.filter(company=company).only("id", "account_class"):
        if account.account_class in RESULT_ACCOUNT_CLASSES:
            balance = year_balances.get(account.id, Decimal("0"))
        else:
            balance = all_time_balances.get(account.id, Decimal("0"))
        account_balances[str(account.id)] = str(balance)

    return account_balances
