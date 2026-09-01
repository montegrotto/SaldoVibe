from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from banking.models import BankAccount
from bookkeeping.bas_accounts import load_bas_2026_accounts
from bookkeeping.company_scope import SESSION_COMPANY_KEY
from bookkeeping.compliance_policy import is_action_allowed
from bookkeeping.models import Account, AccountClass, AccountingYear, Company, JournalEntry, Transaction
from fixed_assets.models import FixedAssetType
from saldovibe.testing import create_account, create_company, create_user, set_active_company


class CompanyAccessTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()

    def _grant_add_company(self, user):
        user.user_permissions.add(Permission.objects.get(codename="add_company", content_type__app_label="bookkeeping"))

    def test_superuser_is_allowed_for_sensitive_action(self):
        user = create_user("root@example.com", is_superuser=True)
        self.assertTrue(is_action_allowed(user, "company.delete"))

    def test_staff_user_maps_to_finance_operator(self):
        user = create_user("staff@example.com", is_staff=True)
        self.assertTrue(is_action_allowed(user, "export.sie4"))

    def test_group_member_can_run_finance_admin_action(self):
        user = create_user("finance-admin@example.com")
        group = Group.objects.create(name="finance_admin")
        user.groups.add(group)
        self.assertTrue(is_action_allowed(user, "export.sru"))

    def test_regular_user_is_blocked_for_sensitive_action(self):
        user = create_user("user@example.com")
        self.assertFalse(is_action_allowed(user, "restore.dry_run"))

    def test_user_without_permission_cannot_create_company(self):
        user = create_user("no-perm@example.com")
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_create"),
            {
                "name": "Otillåtet AB",
                "org_number": "999888-7776",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:no_company_access"))
        self.assertFalse(Company.objects.filter(name="Otillåtet AB").exists())

    def test_dashboard_redirects_to_company_create_when_user_can_create(self):
        user = create_user("creator@example.com")
        self._grant_add_company(user)
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertRedirects(response, reverse("bookkeeping:company_create"))

    def test_dashboard_redirects_superuser_without_company_to_company_create(self):
        user = create_user("super@example.com", is_superuser=True)
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertRedirects(response, reverse("bookkeeping:company_create"))

    def test_dashboard_redirects_to_no_company_access_without_permission(self):
        user = create_user("locked-out@example.com")
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertRedirects(response, reverse("bookkeeping:no_company_access"))
        response = self.client.get(reverse("bookkeeping:no_company_access"))
        self.assertContains(response, "Kontakta administratör")

    def test_no_company_access_redirects_user_who_can_create_company(self):
        user = create_user("can-create@example.com")
        self._grant_add_company(user)
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:no_company_access"))

        self.assertRedirects(response, reverse("bookkeeping:company_create"))

    def test_no_company_access_redirects_user_with_company_to_dashboard(self):
        user = create_user("member@example.com")
        company = create_company("Medlem AB", "777666-5554", users=[user])
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:no_company_access"))

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))

    def test_creator_gets_access_to_created_company(self):
        user = create_user("user@example.com")
        self._grant_add_company(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_create"),
            {
                "name": "Mitt Bolag AB",
                "org_number": "556677-8899",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))

        company = Company.objects.get(name="Mitt Bolag AB")
        self.assertTrue(company.users.filter(pk=user.pk).exists())

        session = self.client.session
        self.assertEqual(session.get(SESSION_COMPANY_KEY), company.pk)

    def test_company_create_seeds_complete_bas_2026_accounts(self):
        user = create_user("seed@example.com")
        self._grant_add_company(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_create"),
            {
                "name": "Seeded BAS AB",
                "org_number": "998877-6655",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))

        company = Company.objects.get(name="Seeded BAS AB")
        expected_numbers = {row["number"] for row in load_bas_2026_accounts()}
        actual_numbers = set(Account.objects.filter(company=company).values_list("number", flat=True))
        self.assertSetEqual(actual_numbers, expected_numbers)

        output_vat = Account.objects.get(company=company, number="2611")
        input_vat = Account.objects.get(company=company, number="2641")
        asset_account = Account.objects.get(company=company, number="1011")

        self.assertEqual(output_vat.vat_field_code, "10")
        self.assertEqual(input_vat.vat_field_code, "48")
        self.assertEqual(asset_account.sru_code, "7201")

    def test_non_staff_creator_cannot_create_inactive_company(self):
        user = create_user("nonstaff@example.com", is_staff=False)
        self._grant_add_company(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_create"),
            {
                "name": "Aktivt Standardbolag",
                "org_number": "112233-4455",
                "is_active": False,
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))

        company = Company.objects.get(name="Aktivt Standardbolag")
        self.assertTrue(company.is_active)
        self.assertTrue(company.users.filter(pk=user.pk).exists())

    def test_company_member_can_update_company(self):
        user = create_user("editor@example.com")
        company = create_company("Gamma AB", "100200-3004", users=[user])
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_update", args=[company.pk]),
            {
                "name": "Gamma Holding AB",
                "org_number": "100200-3004",
                "address": "Storgatan 1, 111 22 Stockholm",
                "phone_number": "08-123 45 67",
                "email": "hej@gamma.se",
                "bankgiro": "123-4567",
                "plusgiro": "98 76 54-3",
                "is_active": False,
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        company.refresh_from_db()
        self.assertEqual(company.name, "Gamma Holding AB")
        self.assertEqual(company.address, "Storgatan 1, 111 22 Stockholm")
        self.assertEqual(company.phone_number, "08-123 45 67")
        self.assertEqual(company.email, "hej@gamma.se")
        self.assertEqual(company.bankgiro, "123-4567")
        self.assertEqual(company.plusgiro, "98 76 54-3")
        self.assertTrue(company.is_active)

    def test_company_member_can_delete_company(self):
        user = create_user("deleter@example.com", is_staff=True)
        company_to_delete = create_company("Delete AB", "111222-3334")
        replacement_company = create_company("Keep AB", "555666-7778")
        company_to_delete.users.add(user)
        replacement_company.users.add(user)
        self.client.force_login(user)

        set_active_company(self.client, company_to_delete)

        response = self.client.post(reverse("bookkeeping:company_delete", args=[company_to_delete.pk]))

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        self.assertFalse(Company.objects.filter(pk=company_to_delete.pk).exists())
        session = self.client.session
        self.assertEqual(session.get(SESSION_COMPANY_KEY), replacement_company.pk)

    def test_company_delete_forbidden_for_non_member(self):
        owner = create_user("owner@example.com")
        other_user = create_user("other@example.com", is_staff=True)
        company = create_company("Private AB", "121212-1212", users=[owner])
        self.client.force_login(other_user)

        response = self.client.post(reverse("bookkeeping:company_delete", args=[company.pk]))

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        self.assertTrue(Company.objects.filter(pk=company.pk).exists())

    def test_company_delete_requires_post(self):
        user = create_user("method@example.com")
        company = create_company("Method AB", "131313-1313", users=[user])
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:company_delete", args=[company.pk]))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Company.objects.filter(pk=company.pk).exists())

    def test_company_delete_succeeds_with_fixed_asset_type_account_links(self):
        user = create_user("fixed-asset-delete@example.com", is_staff=True)
        company = create_company("Assets Delete AB", "141414-1414", users=[user])
        self.client.force_login(user)

        expense_account = create_account(company, "7830", "Avskrivningar maskiner", AccountClass.COST_OF_GOODS)
        accumulated_account = create_account(company, "1219", "Ack avskr maskiner", AccountClass.ASSET)
        FixedAssetType.objects.create(
            company=company,
            key="machinery",
            name="Maskiner",
            depreciation_expense_account=expense_account,
            accumulated_depreciation_account=accumulated_account,
        )

        response = self.client.post(reverse("bookkeeping:company_delete", args=[company.pk]))

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        self.assertFalse(Company.objects.filter(pk=company.pk).exists())

    def test_company_delete_is_blocked_with_transactions_and_journal_entries(self):
        user = create_user("txn-delete@example.com", is_staff=True)
        company = create_company("Txn Delete AB", "151515-1515", users=[user])
        self.client.force_login(user)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        debit_account = create_account(company, "1930", "Företagskonto", AccountClass.ASSET)
        credit_account = create_account(company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)
        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-06-01",
            description="Testverifikation",
            reference="T-1",
            created_by=user,
        )
        JournalEntry.objects.create(transaction=txn, account=debit_account, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=credit_account, debit="0.00", credit="100.00")

        response = self.client.post(reverse("bookkeeping:company_delete", args=[company.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:company_update", args=[company.pk]))
        self.assertTrue(Company.objects.filter(pk=company.pk).exists())

    def test_company_delete_succeeds_with_bank_account_linked_to_bookkeeping_account(self):
        user = create_user("bank-delete@example.com", is_staff=True)
        company = create_company("Bank Delete AB", "161616-1616", users=[user])
        self.client.force_login(user)

        bookkeeping_account = create_account(company, "1930", "Företagskonto", AccountClass.ASSET)
        BankAccount.objects.create(
            company=company,
            name="SEB Företagskonto",
            account_number="1234-567890",
            account_type="bank",
            bookkeeping_account=bookkeeping_account,
        )

        response = self.client.post(reverse("bookkeeping:company_delete", args=[company.pk]))

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        self.assertFalse(Company.objects.filter(pk=company.pk).exists())

    def test_company_update_saves_vat_reporting_period(self):
        user = create_user("vat-editor@example.com")
        company = create_company("VAT AB", "123456-7890", users=[user])
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_update", args=[company.pk]),
            {
                "name": "VAT AB",
                "org_number": "123456-7890",
                "address": "",
                "phone_number": "",
                "email": "",
                "bankgiro": "",
                "plusgiro": "",
                "vat_reporting_period": "quarterly",
                "vat_start_date": "2026-04-01",
                "email_fetch_enabled": "",
                "email_fetch_provider": "",
                "email_fetch_address": "",
                "email_fetch_password": "",
                "email_fetch_oauth_tenant_id": "",
                "email_fetch_oauth_client_id": "",
                "email_fetch_oauth_client_secret": "",
                "email_fetch_folder": "INBOX",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        company.refresh_from_db()
        self.assertEqual(company.vat_reporting_period, "quarterly")
        self.assertEqual(str(company.vat_start_date), "2026-04-01")

    def test_company_update_keeps_existing_email_fetch_password_when_blank(self):
        user = create_user("editor2@example.com")
        company = create_company(
            "Delta AB",
            "200300-4005",
            users=[user],
            email_fetch_enabled=True,
            email_fetch_provider="gmail",
            email_fetch_address="finance@delta.se",
            email_fetch_password="existing-secret",
            email_fetch_folder="INBOX",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_update", args=[company.pk]),
            {
                "name": "Delta AB",
                "org_number": "200300-4005",
                "address": "",
                "phone_number": "",
                "email": "",
                "bankgiro": "",
                "plusgiro": "",
                "email_fetch_enabled": "on",
                "email_fetch_provider": "gmail",
                "email_fetch_address": "finance@delta.se",
                "email_fetch_password": "",
                "email_fetch_folder": "INBOX",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        company.refresh_from_db()
        self.assertEqual(company.email_fetch_password, "existing-secret")

    def test_company_update_keeps_existing_smtp_password_when_blank(self):
        user = create_user("smtp@example.com")
        company = create_company(
            "Epsilon AB",
            "200300-4006",
            users=[user],
            email_send_provider="smtp",
            email_send_from="faktura@epsilon.se",
            email_send_smtp_host="smtp.epsilon.se",
            email_send_smtp_password="existing-smtp-secret",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_update", args=[company.pk]),
            {
                "name": "Epsilon AB",
                "org_number": "200300-4006",
                "email_send_provider": "smtp",
                "email_send_from": "faktura@epsilon.se",
                "email_send_smtp_host": "smtp.epsilon.se",
                "email_send_smtp_password": "",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        company.refresh_from_db()
        self.assertEqual(company.email_send_smtp_password, "existing-smtp-secret")
        self.assertEqual(company.email_send_smtp_port, 587)

    def test_dashboard_redirects_to_select_company_when_multiple_available(self):
        user = create_user("multi@example.com")
        first_company = create_company("Alfa AB", "100000-0001")
        second_company = create_company("Beta AB", "100000-0002")
        first_company.users.add(user)
        second_company.users.add(user)
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertRedirects(response, reverse("bookkeeping:select_company"))
        session = self.client.session
        self.assertNotIn(SESSION_COMPANY_KEY, session)

    def test_select_company_lists_available_companies_and_sets_session(self):
        user = create_user("picker@example.com")
        first_company = create_company("Alfa AB", "100000-0003")
        second_company = create_company("Beta AB", "100000-0004")
        first_company.users.add(user)
        second_company.users.add(user)
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:select_company"))
        self.assertContains(response, "Alfa AB")
        self.assertContains(response, "Beta AB")

        response = self.client.post(reverse("bookkeeping:select_company"), {"company_id": second_company.pk})

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))
        session = self.client.session
        self.assertEqual(session.get(SESSION_COMPANY_KEY), second_company.pk)

    def test_dashboard_auto_selects_single_company_without_prompting(self):
        user = create_user("single@example.com")
        company = create_company("Solo AB", "100000-0005", users=[user])
        self.client.force_login(user)

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertEqual(session.get(SESSION_COMPANY_KEY), company.pk)

    def test_switch_company_always_redirects_to_dashboard(self):
        user = create_user("switcher@example.com")
        first_company = create_company("Alfa AB", "100000-0006")
        second_company = create_company("Beta AB", "100000-0007")
        first_company.users.add(user)
        second_company.users.add(user)
        self.client.force_login(user)

        set_active_company(self.client, first_company)

        response = self.client.post(
            reverse("bookkeeping:switch_company"),
            {"company_id": second_company.pk, "next": reverse("bookkeeping:company_list")},
        )

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))
        session = self.client.session
        self.assertEqual(session.get(SESSION_COMPANY_KEY), second_company.pk)

    def test_company_update_keeps_existing_oauth_client_secret_when_blank(self):
        user = create_user("editor3@example.com")
        company = create_company(
            "Epsilon AB",
            "300400-5006",
            users=[user],
            email_fetch_enabled=True,
            email_fetch_provider="outlook",
            email_fetch_address="finance@epsilon.se",
            email_fetch_password="",
            email_fetch_oauth_tenant_id="tenant-id",
            email_fetch_oauth_client_id="client-id",
            email_fetch_oauth_client_secret="existing-oauth-secret",
            email_fetch_folder="INBOX",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("bookkeeping:company_update", args=[company.pk]),
            {
                "name": "Epsilon AB",
                "org_number": "300400-5006",
                "address": "",
                "phone_number": "",
                "email": "",
                "bankgiro": "",
                "plusgiro": "",
                "email_fetch_enabled": "on",
                "email_fetch_provider": "outlook",
                "email_fetch_address": "finance@epsilon.se",
                "email_fetch_password": "",
                "email_fetch_oauth_tenant_id": "tenant-id",
                "email_fetch_oauth_client_id": "client-id",
                "email_fetch_oauth_client_secret": "",
                "email_fetch_folder": "INBOX",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        company.refresh_from_db()
        self.assertEqual(company.email_fetch_oauth_client_secret, "existing-oauth-secret")
