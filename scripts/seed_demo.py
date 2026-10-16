"""Fyll en tom SaldoVibe-databas med demodata, t.ex. demoinstansen för App Store-granskningen.

Tre påhittade bolag med kunder, leverantörer, kund- och leverantörsfakturor (betalda, öppna och
förfallna), utlägg (utkast, bokförda, utbetalda), körrapporter, okopplade kvitton och löpande
verifikationer - plus en vanlig demoanvändare (inte superuser) med tillgång till alla tre.
Allt skrivs via samma formulär och tjänster som webben och appen använder.

Datumen räknas bakåt från i dag, så demon är aktuell när skriptet körs. Kör om det mot en tom
databas när året har slagit om (räkenskapsåret i översikten är det som täcker dagens datum).

Körs med manage.py shell, i webbcontainern som appuser (docker exec går förbi entrypointen och
landar som root, då blir bilagefilerna root-ägda):

    docker compose exec -T -e DEMO_EMAIL=appreview@saldovibe.se -e DEMO_PASSWORD='...' \
        web gosu appuser /opt/venv/bin/python /app/manage.py shell < seed_demo.py

Utan DEMO_PASSWORD slumpas ett lösenord som skrivs ut. Lokalt mot en engångsdatabas:

    SALDOVIBE_DATA_DIR=/tmp/demo .venv/bin/python manage.py migrate
    SALDOVIBE_DATA_DIR=/tmp/demo .venv/bin/python manage.py shell < scripts/seed_demo.py
"""

import os
import secrets
import sys
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import reportlab
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from api.auth import form_data
from attachments.models import TransactionAttachment
from attachments.services import save_attachment_with_thumbnail
from bookkeeping.bas_accounts import seed_bas_2026_accounts_for_company
from bookkeeping.models import AccountingYear, Company, CompanyMembership, Transaction, VoucherSeriesRule
from bookkeeping.payables import add_journal_entry, register_manual_payment
from expenses.forms import ExpenseClaimForm
from expenses.models import ExpenseCategory
from invoicing.models import Article, Customer, Invoice, InvoiceLine
from payroll.forms import MileageReportForm
from payroll.models import Employee, MileageReport
from supplier_invoices.models import Supplier, SupplierInvoice, SupplierInvoiceCostLine
from supplier_invoices.services import register_and_bookkeep_supplier_invoice

TODAY = timezone.localdate()
# Pillows inbyggda typsnitt saknar å/ä/ö; Vera följer med reportlab (via xhtml2pdf).
RECEIPT_FONT = str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")
VAT_RATE = Decimal("0.25")

# Fakturor: (kund, beskrivning, antal, dagar sedan fakturadatum, betald)
# Leverantörsfakturor: (leverantör, kostnadskonto, belopp exkl. moms, dagar sedan, betald)
# Utlägg: (beskrivning, kategori, belopp inkl. moms, dagar sedan, "draft" | "booked" | "paid")
# Körrapporter: (rutt, syfte, km, dagar sedan)
# Kvitton (okopplade, appens Kvitton-flik): (butik, belopp inkl. moms, dagar sedan)
COMPANIES = [
    {
        "name": "Fjällbris Konsult AB",
        "legal_form": Company.LegalForm.AKTIEBOLAG,
        "vat_reporting_period": Company.VatReportingPeriod.QUARTERLY,
        "city": "Umeå",
        "equity": ("2081", "50000.00", "Insatt aktiekapital"),
        "rent": "9500.00",
        "article": ("Konsulttimme", "1150.00", "tim", "3001", "25.00"),
        "employee": ("Sara", "Lind", "48000.00", "199103152386"),
        "customers": ["Havsörn Media AB", "Gröna Gårdar AB", "Norrsken Logistik AB"],
        "invoices": [
            ("Havsörn Media AB", "Systemutveckling", 32, 230, True),
            ("Gröna Gårdar AB", "Webbplats, etapp 1", 24, 180, True),
            ("Norrsken Logistik AB", "Integration lagersystem", 40, 120, True),
            ("Havsörn Media AB", "Systemutveckling", 28, 75, True),
            ("Gröna Gårdar AB", "Webbplats, etapp 2", 18, 50, False),
            ("Norrsken Logistik AB", "Support och förvaltning", 12, 12, False),
        ],
        "suppliers": [
            ("Molnfabriken AB", "5420", "1890.00", 160, True),
            ("Nätkraft Telekom AB", "6212", "649.00", 95, True),
            ("Kontorsvaruhuset i Umeå AB", "6110", "1240.00", 45, False),
            ("Nätkraft Telekom AB", "6212", "649.00", 8, False),
        ],
        "expenses": [
            ("Hotell kundbesök Luleå", "Hotell", "1595.00", 140, "paid"),
            ("Tåg Umeå-Stockholm", "Biljetter och taxi", "1148.00", 60, "paid"),
            ("Lunch med kund", "Representation", "486.00", 20, "booked"),
            ("USB-C-hubb", "Förbrukningsinventarier", "549.00", 4, "draft"),
        ],
        "trips": [
            ("Umeå–Skellefteå t/r", "Kundmöte Norrsken Logistik", "276", 30),
            ("Umeå–Örnsköldsvik t/r", "Workshop Gröna Gårdar", "224", 6),
        ],
        "receipts": [("Parkering Umeå C", "60.00", 3), ("Kontorsvaruhuset", "189.00", 1)],
    },
    {
        "name": "Solgläntan Café AB",
        "legal_form": Company.LegalForm.AKTIEBOLAG,
        "vat_reporting_period": Company.VatReportingPeriod.MONTHLY,
        "city": "Göteborg",
        "equity": ("2081", "25000.00", "Insatt aktiekapital"),
        "rent": "14500.00",
        "till_sales": ("3002", "2621", "12", "32000.00"),  # månadens kassaförsäljning inkl. moms
        "article": ("Catering per person", "165.00", "st", "3002", "12.00"),
        "employee": ("Ali", "Haddad", "29500.00", "198807224417"),
        "customers": ["Kvarnbyns Förening", "Västkust Event AB"],
        "invoices": [
            ("Västkust Event AB", "Catering konferens", 60, 150, True),
            ("Kvarnbyns Förening", "Catering årsmöte", 35, 90, True),
            ("Västkust Event AB", "Catering mässa", 120, 40, False),
            ("Kvarnbyns Förening", "Fika styrelsemöte", 12, 5, False),
        ],
        "suppliers": [
            ("Grossistlagret Väst AB", "4010", "18450.00", 70, True),
            ("Grossistlagret Väst AB", "4010", "16980.00", 35, False),
            ("Kaffekompaniet i Majorna AB", "4010", "4320.00", 10, False),
        ],
        "expenses": [
            ("Ägg och mjölk, akutinköp", "Övrigt", "312.00", 25, "paid"),
            ("Parkering grossist", "Parkering", "45.00", 9, "booked"),
            ("Diskborstar", "Förbrukningsinventarier", "219.00", 2, "draft"),
        ],
        "trips": [("Göteborg–Kungälv t/r", "Leverans catering", "62", 40)],
        "receipts": [("Livsmedelsbutiken", "428.00", 2)],
    },
    {
        "name": "Ekbacken Snickeri",
        "legal_form": Company.LegalForm.ENSKILD_FIRMA,
        "vat_reporting_period": Company.VatReportingPeriod.ANNUAL,
        "city": "Falun",
        "equity": ("2010", "15000.00", "Egen insättning"),
        "rent": "4200.00",
        "article": ("Snickeriarbete", "650.00", "tim", "3001", "25.00"),
        "employee": ("Lina", "Berg", "32000.00", "199602184426"),
        "customers": ["Brf Kopparhatten", "Dalarnas Fastighetsservice AB"],
        "invoices": [
            ("Brf Kopparhatten", "Renovering trapphus", 48, 200, True),
            ("Dalarnas Fastighetsservice AB", "Platsbyggd förvaring", 30, 100, True),
            ("Brf Kopparhatten", "Byte av entrédörrar", 22, 45, False),
        ],
        "suppliers": [
            ("Virkesboden i Falun AB", "4010", "7820.00", 110, True),
            ("Virkesboden i Falun AB", "4010", "3460.00", 18, False),
        ],
        "expenses": [
            ("Diesel servicebil", "Bränsle", "812.00", 15, "booked"),
            ("Skruv och beslag", "Förbrukningsinventarier", "356.00", 3, "draft"),
        ],
        "trips": [("Falun–Borlänge t/r", "Platsbesök Dalarnas Fastighetsservice", "54", 12)],
        "receipts": [("Järnhandeln Falun", "245.00", 1)],
    },
]


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def ago(days):
    return TODAY - timedelta(days=days)


def receipt_image(title, lines, total):
    """A PNG that looks enough like a receipt for the attachment list and preview."""
    font = ImageFont.truetype(RECEIPT_FONT, 30)
    image = Image.new("RGB", (620, 160 + 46 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), title, fill="black", font=ImageFont.truetype(RECEIPT_FONT, 40))
    for i, line in enumerate(lines):
        draw.text((40, 110 + 46 * i), line, fill="black", font=font)
    draw.text((40, 110 + 46 * len(lines)), f"Totalt {total} kr", fill="black", font=font)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def attachment(company, user, title, day, total):
    vat = money(Decimal(total) * VAT_RATE / (1 + VAT_RATE))
    png = receipt_image(title, [day.isoformat(), f"varav moms 25 %: {vat} kr"], total)
    file = ContentFile(png, name=f"kvitto-{day.isoformat()}.png")
    att = TransactionAttachment(company=company, uploaded_by=user, file=file)
    save_attachment_with_thumbnail(att)
    return att


def year_for(company, day):
    return company.accounting_years.get(start_date__lte=day, end_date__gte=day)


def voucher(company, user, day, description, rows):
    txn = Transaction.objects.create(
        accounting_year=year_for(company, day), date=day, description=description, created_by=user
    )
    for number, debit, credit in rows:
        add_journal_entry(
            transaction=txn,
            account=company.accounts.get(number=number),
            debit=money(debit),
            credit=money(credit),
            description=description,
        )
    txn.validate_balanced()


def pay(document, user, day):
    if day <= TODAY:
        bank = document.company.accounts.get(number="1930")
        register_manual_payment(
            document, user, payment_date=day, amount=document.remaining_amount, payment_account=bank
        )


def valid_form(form):
    if not form.is_valid():
        raise SystemExit(f"{type(form).__name__}: {form.errors.as_json()}")
    return form


def seed_company(spec, user):
    company = Company.objects.create(
        name=spec["name"],
        legal_form=spec["legal_form"],
        vat_reporting_period=spec["vat_reporting_period"],
        city=spec["city"],
    )
    # Som company_create + company_members: konton, serier, kategorier, medlemskap.
    CompanyMembership.objects.create(company=company, user=user, role=CompanyMembership.Role.EDITOR)
    seed_bas_2026_accounts_for_company(company)
    VoucherSeriesRule.seed_defaults_for_company(company)
    ExpenseCategory.seed_defaults_for_company(company)
    for year in (TODAY.year - 1, TODAY.year):
        AccountingYear.objects.create(
            company=company,
            start_date=TODAY.replace(year=year, month=1, day=1),
            end_date=TODAY.replace(year=year, month=12, day=31),
        )
    account = company.accounts.get

    # Kapital in på banken i början av förra året; hyra, bankavgift (och kassaförsäljning) varje månad i år.
    equity_account, equity_amount, equity_text = spec["equity"]
    start = TODAY.replace(year=TODAY.year - 1, month=1, day=2)
    voucher(company, user, start, equity_text, [("1930", equity_amount, "0"), (equity_account, "0", equity_amount)])
    for month in range(1, TODAY.month + 1):
        voucher(
            company,
            user,
            TODAY.replace(month=month, day=1),
            "Lokalhyra",
            [("5010", spec["rent"], "0"), ("1930", "0", spec["rent"])],
        )
        voucher(
            company, user, TODAY.replace(month=month, day=1), "Bankavgift", [("6570", "95", "0"), ("1930", "0", "95")]
        )
        if "till_sales" in spec:
            sales_account, vat_account, rate, gross = spec["till_sales"]
            net = money(Decimal(gross) / (1 + Decimal(rate) / 100))
            voucher(
                company,
                user,
                min(TODAY, TODAY.replace(month=month, day=28)),
                "Kassaförsäljning",
                [("1930", gross, "0"), (sales_account, "0", net), (vat_account, "0", money(gross) - net)],
            )

    name, price, unit, income_account, vat_rate = spec["article"]
    article = Article.objects.create(
        company=company, name=name, unit_price=money(price), unit=unit, income_account=account(number=income_account)
    )
    customers = {
        customer_name: Customer.objects.create(company=company, name=customer_name, city=spec["city"])
        for customer_name in spec["customers"]
    }
    for customer_name, description, quantity, days, paid in spec["invoices"]:
        invoice = Invoice.objects.create(
            company=company, customer=customers[customer_name], invoice_date=ago(days), due_date=ago(days - 30)
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description=description,
            quantity=Decimal(quantity),
            unit=unit,
            unit_price=money(price),
            vat_rate=Decimal(vat_rate),
        )
        invoice.bookkeep(user)
        if paid:
            pay(invoice, user, ago(days - 25))

    suppliers = {}
    for number, (supplier_name, cost_account, ex_vat, days, paid) in enumerate(spec["suppliers"], start=1):
        supplier = suppliers.get(supplier_name) or Supplier.objects.create(company=company, name=supplier_name)
        suppliers[supplier_name] = supplier
        ex_vat = money(ex_vat)
        vat = money(ex_vat * VAT_RATE)
        invoice = SupplierInvoice.objects.create(
            company=company,
            accounting_year=year_for(company, ago(days)),
            supplier=supplier,
            supplier_name=supplier_name,
            invoice_number=f"{ago(days):%y%m}-{number:03d}",
            invoice_date=ago(days),
            due_date=ago(days - 30),
            amount_ex_vat=ex_vat,
            vat_amount=vat,
            total_amount=ex_vat + vat,
            payable_account=account(number="2440"),
            vat_account=account(number="2640"),
        )
        SupplierInvoiceCostLine.objects.create(
            invoice=invoice, expense_account=account(number=cost_account), debit=ex_vat, credit=Decimal("0")
        )
        register_and_bookkeep_supplier_invoice(invoice, user)
        if paid:
            pay(invoice, user, ago(days - 20))

    first_name, last_name, salary, personal_number = spec["employee"]
    employee = Employee.objects.create(
        company=company,
        first_name=first_name,
        last_name=last_name,
        personal_identity_number=personal_number,
        monthly_salary=money(salary),
    )
    categories = {category.name: category.pk for category in company.expense_categories.all()}
    for description, category, total, days, status in spec["expenses"]:
        total = money(total)
        form = valid_form(
            ExpenseClaimForm(
                form_data(
                    {
                        "description": description,
                        "expense_date": ago(days).isoformat(),
                        "total_amount": total,
                        "vat_amount": money(total * VAT_RATE / (1 + VAT_RATE)),
                        "category": categories[category],
                        "employee": employee.pk,
                    }
                ),
                company=company,
            )
        )
        claim = form.save(commit=False)
        claim.company = company
        claim.created_by = user
        claim.person_name = str(employee)
        claim.save()
        claim.attachments.add(attachment(company, user, description, ago(days), total))
        if status in ("booked", "paid"):
            claim.register_and_bookkeep(user)
        if status == "paid":
            pay(claim, user, ago(days - 5))

    for route, purpose, km, days in spec["trips"]:
        form = valid_form(
            MileageReportForm(
                form_data(
                    {
                        "employee": employee.pk,
                        "trip_date": ago(days).isoformat(),
                        "route": route,
                        "purpose": purpose,
                        "distance_km": km,
                        "rate_per_mil": MileageReport.DEFAULT_RATE_PER_MIL,
                    }
                ),
                company=company,
            )
        )
        report = form.save(commit=False)
        report.company = company
        report.submit(user, register=False)

    for shop, total, days in spec["receipts"]:
        attachment(company, user, shop, ago(days), total)


def main():
    names = [spec["name"] for spec in COMPANIES]
    if Company.objects.filter(name__in=names).exists():
        sys.exit("Demobolagen finns redan - kör mot en tom databas.")

    email = os.environ.get("DEMO_EMAIL", "appreview@saldovibe.se")
    password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(12)
    User = get_user_model()
    if User.objects.filter(email=email).exists():
        sys.exit(f"{email} finns redan - välj en annan DEMO_EMAIL.")

    with db_transaction.atomic():
        user = User.objects.create_user(email=email, password=password, first_name="App", last_name="Review")
        for spec in COMPANIES:
            seed_company(spec, user)

    print(f"Skapade {', '.join(names)}.")
    print(f"Demoanvändare: {email}")
    if "DEMO_PASSWORD" not in os.environ:
        print(f"Lösenord: {password}")


main()
