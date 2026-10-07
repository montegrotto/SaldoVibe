from django.db import migrations


def seed(apps, schema_editor):
    from expenses.models import DEFAULT_EXPENSE_CATEGORIES

    Company = apps.get_model("bookkeeping", "Company")
    Account = apps.get_model("bookkeeping", "Account")
    ExpenseCategory = apps.get_model("expenses", "ExpenseCategory")
    numbers = [number for _, number in DEFAULT_EXPENSE_CATEGORIES]
    for company in Company.objects.all():
        if ExpenseCategory.objects.filter(company=company).exists():
            continue
        accounts = {a.number: a for a in Account.objects.filter(company=company, number__in=numbers)}
        for name, number in DEFAULT_EXPENSE_CATEGORIES:
            if number in accounts:
                ExpenseCategory.objects.create(company=company, name=name, account=accounts[number])


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0003_expense_category"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
