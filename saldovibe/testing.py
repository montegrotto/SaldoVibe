"""Shared building blocks for company-scoped tests.

Almost every test in this project needs the same things before it can assert
anything: a user, a :class:`~bookkeeping.models.Company` that user belongs to,
an :class:`~bookkeeping.models.AccountingYear`, a chart of accounts, and the
session key (``bookkeeping.company_scope.SESSION_COMPANY_KEY``) that makes the
company *active* for ``self.client``. These helpers build exactly that.

Use them composably:

* the plain ``create_*`` functions when a test builds its own fixtures (several
  companies, no user at all, a company created mid-test);
* :class:`CompanyTestCase` when a test just needs "a logged-in user looking at
  their company", which is the common case.

Whatever a single test needs *on top of* the baseline - extra accounts, a second
company, a different date range, a deliberately missing account - belongs in
that test's own ``setUp``, so the thing that makes it different stays visible
where it matters.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from bookkeeping.bas_accounts import seed_bas_2026_accounts_for_company
from bookkeeping.company_scope import SESSION_COMPANY_KEY
from bookkeeping.models import Account, AccountingYear, Company

DEFAULT_PASSWORD = "password123"
DEFAULT_YEAR_START = "2026-01-01"
DEFAULT_YEAR_END = "2026-12-31"


def create_user(email, *, password=DEFAULT_PASSWORD, **fields):
    """Create a user. Extra model fields (``is_staff=True``, names) pass through."""
    return get_user_model().objects.create_user(email=email, password=password, **fields)


def create_company(name, org_number="", *, users=(), **fields):
    """Create a company and attach ``users`` to it.

    ``Company.name`` is unique, so tests that create more than one company must
    keep giving them distinct names.
    """
    company = Company.objects.create(name=name, org_number=org_number, **fields)
    for user in users:
        company.users.add(user)
    return company


def create_accounting_year(company, start_date=DEFAULT_YEAR_START, end_date=DEFAULT_YEAR_END):
    return AccountingYear.objects.create(company=company, start_date=start_date, end_date=end_date)


def create_account(company, number, name, account_class, **fields):
    """Create one BAS account. ``vat_field_code``/``sru_code``/… pass through."""
    return Account.objects.create(
        company=company,
        number=number,
        name=name,
        account_class=account_class,
        **fields,
    )


def create_accounts(company, specs):
    """Create accounts from ``(number, name, account_class)`` triples.

    Returns them keyed by account number for the tests that reach back for one;
    tests that only need the accounts to exist can ignore the return value.
    """
    return {number: create_account(company, number, name, account_class) for number, name, account_class in specs}


def set_active_company(client, company):
    """Point a test client's session at ``company`` - what the views read.

    Must be called *after* ``force_login``, which starts a fresh session.
    """
    session = client.session
    session[SESSION_COMPANY_KEY] = company.pk
    session.save()


class CompanyTestCase(TestCase):
    """A logged-in user with an active company and an accounting year.

    Subclasses tune the baseline through the class attributes below and add
    whatever else they need in their own ``setUp`` (calling ``super().setUp()``
    first).
    """

    #: Login identity. ``user_fields`` takes extra model fields, e.g. ``{"is_staff": True}``.
    user_email = "test-user@example.com"
    user_fields = {}

    #: ``Company.name`` is unique across the database, so give each subclass its own.
    company_name = "Testbolaget AB"
    company_org_number = ""
    company_fields = {}

    #: ``(start_date, end_date)`` for the accounting year, or ``None`` for no year at all.
    accounting_year_dates = (DEFAULT_YEAR_START, DEFAULT_YEAR_END)

    #: Seed the full BAS 2026 chart of accounts instead of only what the test creates.
    seed_bas_accounts = False

    def setUp(self):
        super().setUp()
        self.user = create_user(self.user_email, **self.user_fields)
        self.client.force_login(self.user)

        self.company = create_company(
            self.company_name,
            self.company_org_number,
            users=[self.user],
            **self.company_fields,
        )
        set_active_company(self.client, self.company)

        self.year = (
            create_accounting_year(self.company, *self.accounting_year_dates) if self.accounting_year_dates else None
        )

        if self.seed_bas_accounts:
            seed_bas_2026_accounts_for_company(self.company)
