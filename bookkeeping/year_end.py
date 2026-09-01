"""Årsavslut/bokslut: förkontroller och bokslutsverifikationerna S1/S2.

Implementerar docs/compliance/aarsavslut/bokslutsflode-design.md. Två verifikationer:

  S1 (årets sista dag, stängda året): 8999 -> 2099 (AB) resp. 2019 (EF) - årets resultat.
  S2 (första dagen, nästa år): 2099 -> 2091 (AB) resp. hela 2011-2019-serien -> 2010 (EF).

Ett år räknas som avslutat när det har en okorrigerad YEAR_END-verifikation daterad på
årets sista dag (datumvillkoret skiljer S1 från nästa års S2) - eller saknar poster helt.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Sum

from .models import (
    Account,
    AccountClass,
    AccountingYear,
    Company,
    JournalEntry,
    PeriodLock,
    Transaction,
    TransactionSource,
)
from .period_locking import is_date_locked

BALANCE_SHEET_CLASSES = [AccountClass.ASSET, AccountClass.EQUITY_LIABILITY]

ZERO = Decimal("0.00")


def annual_result(year):
    """Årets resultat (kredit - debet över klass 3-10), samma konvention som
    build_income_statement_context."""
    totals = (
        JournalEntry.objects.filter(
            transaction__accounting_year=year,
            account__is_active=True,
        )
        .exclude(account__account_class__in=BALANCE_SHEET_CLASSES)
        .aggregate(d=Sum("debit"), c=Sum("credit"))
    )
    return (totals["c"] or ZERO) - (totals["d"] or ZERO)


def balance_difference(year):
    """Balansräkningens differens (debet - kredit över klass 1-2, kumulativt t.o.m.
    årets sista dag), samma konvention som build_balance_sheet_context."""
    totals = JournalEntry.objects.filter(
        account__company=year.company,
        account__is_active=True,
        account__account_class__in=BALANCE_SHEET_CLASSES,
        transaction__date__lte=year.end_date,
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (totals["d"] or ZERO) - (totals["c"] or ZERO)


def year_end_voucher(year):
    """S1 för året, eller None. Datumvillkoret (årets sista dag) utesluter nästa års S2."""
    return year.transactions.filter(
        source=TransactionSource.YEAR_END,
        date=year.end_date,
        correction_of__isnull=True,
        corrections__isnull=True,
    ).first()


def is_year_closed(year):
    return year_end_voucher(year) is not None or not year.transactions.exists()


def locked_through(year):
    """Sista dag som täcks av sammanhängande lås från årets start, eller None."""
    intervals = (
        PeriodLock.objects.filter(accounting_year=year, is_locked=True)
        .order_by("period_start")
        .values_list("period_start", "period_end")
    )
    cursor = year.start_date
    for period_start, period_end in intervals:
        if period_start > cursor:
            break
        cursor = max(cursor, period_end + timedelta(days=1))
    if cursor == year.start_date:
        return None
    return cursor - timedelta(days=1)


def precheck_errors(company, year, next_year):
    """Blockerande fel innan bokslutsverifikationerna kan bokföras. Tom lista = klart."""
    errors = []

    if not company.legal_form:
        errors.append("Bolagsform saknas – ange den i företagsinställningarna.")

    for earlier in AccountingYear.objects.filter(company=company, start_date__lt=year.start_date).order_by(
        "start_date"
    ):
        if not is_year_closed(earlier):
            errors.append(f"Räkenskapsåret {earlier.name} måste avslutas först (år stängs i kronologisk ordning).")

    if is_date_locked(company, year.end_date):
        errors.append(
            "Årets sista dag ligger i en låst period – bokslutsverifikationen kan inte bokföras. "
            "Lås upp årets sista period; helårslåsningen görs som sista steg."
        )
    else:
        # Allt utom årets sista kalendermånad måste vara sammanhängande låst från årets start.
        required_through = year.end_date.replace(day=1) - timedelta(days=1)
        covered = locked_through(year)
        if covered is None or covered < required_through:
            errors.append(
                f"Perioderna fram till {required_through} måste vara låsta innan bokslutet "
                "(alla utom årets sista period)."
            )

    if next_year is not None and is_date_locked(company, next_year.start_date):
        errors.append("Nästa räkenskapsårs första dag ligger i en låst period – omföringen (S2) kan inte bokföras.")

    diff = balance_difference(year)
    result = annual_result(year)
    if diff != result:
        errors.append(
            f"Balanskontrollen stämmer inte: balansräkningens differens är {diff} kr men årets "
            f"resultat är {result} kr. Kontrollera bokföringen innan bokslutet."
        )

    return errors


def _required_account(company, number):
    account = Account.objects.filter(company=company, number=number, is_active=True).first()
    if account is None:
        raise ValidationError(f"Konto {number} saknas i kontoplanen – lägg till det innan bokslutet.")
    return account


def _cumulative_balance(account, upto_date):
    totals = JournalEntry.objects.filter(account=account, transaction__date__lte=upto_date).aggregate(
        d=Sum("debit"), c=Sum("credit")
    )
    return (totals["d"] or ZERO) - (totals["c"] or ZERO)


def _add_zeroing_entry(txn, account, balance):
    """Nollställ ``account``s saldo (debet - kredit) med en motsatt rad."""
    if balance > 0:
        JournalEntry.objects.create(transaction=txn, account=account, credit=balance)
    else:
        JournalEntry.objects.create(transaction=txn, account=account, debit=-balance)


def create_year_end_vouchers(company, user, year, next_year):
    """Bokför S1 (resultatöverföring) och S2 (omföring). Returnerar (s1, s2);
    s2 är None om det inte fanns något att omföra."""
    if year_end_voucher(year) is not None:
        raise ValidationError("Året har redan en bokslutsverifikation.")
    if next_year is None:
        raise ValidationError("Nästa räkenskapsår saknas – skapa det först (S2 bokförs på dess första dag).")

    is_ab = company.legal_form == Company.LegalForm.AKTIEBOLAG
    account_8999 = _required_account(company, "8999")
    result_account = _required_account(company, "2099" if is_ab else "2019")
    equity_account = _required_account(company, "2091" if is_ab else "2010")

    result = annual_result(year)

    with db_transaction.atomic():
        s1 = Transaction.objects.create(
            accounting_year=year,
            date=year.end_date,
            description=f"Årets resultat {year.name}",
            source=TransactionSource.YEAR_END,
            created_by=user,
        )
        if result >= 0:
            JournalEntry.objects.create(transaction=s1, account=account_8999, debit=result)
            JournalEntry.objects.create(transaction=s1, account=result_account, credit=result)
        else:
            JournalEntry.objects.create(transaction=s1, account=result_account, debit=-result)
            JournalEntry.objects.create(transaction=s1, account=account_8999, credit=-result)
        s1.validate_balanced()

        if is_ab:
            flow_accounts = [result_account]
        else:
            flow_accounts = list(
                Account.objects.filter(
                    company=company, is_active=True, number__gte="2011", number__lte="2019"
                ).order_by("number")
            )
        balances = [(acc, _cumulative_balance(acc, year.end_date)) for acc in flow_accounts]
        balances = [(acc, bal) for acc, bal in balances if bal != 0]

        s2 = None
        if balances:
            s2 = Transaction.objects.create(
                accounting_year=next_year,
                date=next_year.start_date,
                description=f"Omföring årets resultat {year.name}",
                source=TransactionSource.YEAR_END,
                created_by=user,
            )
            for acc, bal in balances:
                _add_zeroing_entry(s2, acc, bal)
            net = sum(bal for _, bal in balances)
            if net != 0:
                _add_zeroing_entry(s2, equity_account, -net)
            s2.validate_balanced()

    return s1, s2
