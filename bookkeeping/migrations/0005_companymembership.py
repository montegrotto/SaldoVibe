from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Gör Company.users-tabellen till en explicit through-modell och lägger till rollkolumnen.
    Tabellen bookkeeping_company_users behålls som den är – bara `role` tillkommer."""

    dependencies = [
        ("bookkeeping", "0004_sentemail_salary_purpose"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="CompanyMembership",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        (
                            "company",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="memberships",
                                to="bookkeeping.company",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                db_column="customuser_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="company_memberships",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bookkeeping_company_users",
                        "verbose_name": "Företagsanvändare",
                        "verbose_name_plural": "Företagsanvändare",
                        "unique_together": {("company", "user")},
                    },
                ),
                migrations.AlterField(
                    model_name="company",
                    name="users",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="companies",
                        through="bookkeeping.CompanyMembership",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Användare",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="companymembership",
            name="role",
            field=models.CharField(
                choices=[("editor", "Full behörighet"), ("viewer", "Endast läsa")],
                default="editor",
                max_length=10,
                verbose_name="Roll",
            ),
        ),
    ]
