"""Seed a throwaway SaldoVibe database for manual/browser verification.

Writes into whatever SALDOVIBE_DATA_DIR points at, so it never touches the dev
db.sqlite3. Run it against a freshly migrated disposable data dir — see SKILL.md.

Creates: a superuser, one company with the full BAS chart and a 2026 accounting year,
a customer + article (customer-invoice form needs both), a bank account with one
unbooked transaction (bank-booking form), and two PNG attachments (attachment picker).
"""

import os
import sys
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "saldovibe.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.base import ContentFile  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from attachments.models import TransactionAttachment  # noqa: E402
from banking.models import BankAccount, BankAccountType, BankTransaction  # noqa: E402
from bookkeeping.bas_accounts import seed_bas_2026_accounts_for_company  # noqa: E402
from bookkeeping.models import AccountingYear, Company  # noqa: E402
from invoicing.models import Article, Customer  # noqa: E402

EMAIL = "smoke-run@example.test"
PASSWORD = "smoke-pass-123"
COMPANY = "Run Skill Demo AB"
ORG_NUMBER = "556000-0042"

if Company.objects.filter(org_number=ORG_NUMBER).exists():
    sys.exit(f"{COMPANY} already seeded — use a fresh SALDOVIBE_DATA_DIR instead of re-running.")

User = get_user_model()
user = User.objects.create_user(email=EMAIL, password=PASSWORD)
user.is_staff = True
user.is_superuser = True
user.save()

company = Company.objects.create(name=COMPANY, org_number=ORG_NUMBER)
company.users.add(user)
seed_bas_2026_accounts_for_company(company)
AccountingYear.objects.create(company=company, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))

Customer.objects.create(company=company, name="Demokund AB", is_active=True)
income_account = company.accounts.filter(number__in=["3001", "3010"]).order_by("number").first()
Article.objects.create(
    company=company,
    name="Konsulttimme",
    unit_price=Decimal("1000.00"),
    vat_rate=Decimal("25.00"),
    income_account=income_account,
    is_active=True,
)

bank_account = BankAccount.objects.create(
    company=company,
    name="Företagskonto",
    account_number="1111-2222",
    account_type=BankAccountType.BANK,
    bookkeeping_account=company.accounts.filter(number="1930").first(),
    is_active=True,
)
bank_tx = BankTransaction.objects.create(
    company=company,
    bank_account=bank_account,
    date=date(2026, 8, 1),
    description="Inbetalning kund",
    amount=Decimal("2500.00"),
)


def make_png(label, color):
    image = Image.new("RGB", (400, 300), color)
    ImageDraw.Draw(image).text((20, 140), label, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


for label, color in [("Kvitto A", "#ffe9b0"), ("Kvitto B", "#c9e4ff")]:
    attachment = TransactionAttachment(company=company, uploaded_by=user)
    attachment.file.save(f"{label.replace(' ', '_').lower()}.png", ContentFile(make_png(label, color)), save=False)
    attachment.save()

print(f"login:        {EMAIL} / {PASSWORD}")
print(f"company:      {company.name} (id {company.pk}, {company.accounts.count()} konton)")
print(f"bank_tx id:   {bank_tx.pk}   -> /banking/transaktioner/{bank_tx.pk}/bokfor/")
print(f"attachments:  {TransactionAttachment.objects.filter(company=company).count()}")
