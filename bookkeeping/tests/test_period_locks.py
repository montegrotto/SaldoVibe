from datetime import date

from django.contrib.messages import get_messages
from django.urls import reverse

from auditlog.models import AuditLogEntry
from bookkeeping.models import AccountingYear, PeriodLock
from bookkeeping.period_locking import is_date_locked
from saldovibe.testing import CompanyTestCase, create_user, set_active_company


class PeriodLockManagementTests(CompanyTestCase):
    user_email = "finance-admin@example.com"
    user_fields = {"is_staff": True}
    company_name = "Låsbolaget AB"
    company_org_number = "556677-2233"
    # `date` objects, not the shared ISO strings: the tests compare a saved
    # PeriodLock's dates straight against `self.year.start_date`/`end_date`.
    accounting_year_dates = (date(2026, 1, 1), date(2026, 12, 31))

    def test_period_lock_create_requires_reason(self):
        response = self.client.post(
            f"{reverse('bookkeeping:period_lock_create')}?year={self.year.pk}",
            {
                "accounting_year": self.year.pk,
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "reason": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PeriodLock.objects.filter(company=self.company).exists())

    def test_period_lock_create_locks_period(self):
        response = self.client.post(
            f"{reverse('bookkeeping:period_lock_create')}?year={self.year.pk}",
            {
                "accounting_year": self.year.pk,
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "reason": "Månadsavstämning januari",
            },
        )

        self.assertEqual(response.status_code, 302)
        lock = PeriodLock.objects.get(company=self.company)
        self.assertTrue(lock.is_locked)
        self.assertEqual(lock.locked_by, self.user)
        self.assertTrue(is_date_locked(self.company, date(2026, 1, 15)))
        self.assertFalse(is_date_locked(self.company, date(2026, 2, 1)))

    def test_transaction_cannot_be_created_in_a_locked_period(self):
        from django.core.exceptions import ValidationError

        from bookkeeping.models import Transaction

        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Januari stängd",
            locked_by=self.user,
        )

        # The model-level safety net: even a posting path without its own
        # is_date_locked check must not be able to create a voucher here.
        with self.assertRaisesMessage(ValidationError, "låst"):
            Transaction.objects.create(
                accounting_year=self.year,
                date=date(2026, 1, 15),
                description="Smitväg",
                created_by=self.user,
            )
        self.assertFalse(Transaction.objects.exists())

        Transaction.objects.create(
            accounting_year=self.year,
            date=date(2026, 2, 1),
            description="Öppen period",
            created_by=self.user,
        )

    def test_period_lock_create_rejects_range_outside_accounting_year(self):
        response = self.client.post(
            f"{reverse('bookkeeping:period_lock_create')}?year={self.year.pk}",
            {
                "accounting_year": self.year.pk,
                "period_start": "2025-12-01",
                "period_end": "2026-01-31",
                "reason": "Utanför räkenskapsåret",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PeriodLock.objects.filter(company=self.company).exists())

    def test_period_lock_create_rejects_overlapping_range(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Januari",
            locked_by=self.user,
        )

        response = self.client.post(
            f"{reverse('bookkeeping:period_lock_create')}?year={self.year.pk}",
            {
                "accounting_year": self.year.pk,
                "period_start": "2026-01-15",
                "period_end": "2026-02-15",
                "reason": "Överlappande period",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PeriodLock.objects.filter(company=self.company).count(), 1)

    def test_lock_whole_year_creates_full_range_lock(self):
        response = self.client.post(reverse("bookkeeping:period_lock_lock_year", args=[self.year.pk]))

        self.assertEqual(response.status_code, 302)
        lock = PeriodLock.objects.get(company=self.company)
        self.assertEqual(lock.period_start, self.year.start_date)
        self.assertEqual(lock.period_end, self.year.end_date)
        self.assertTrue(lock.is_locked)
        self.assertTrue(is_date_locked(self.company, date(2026, 6, 15)))

    def test_period_lock_reopen_requires_reason(self):
        lock = PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Januari",
            locked_by=self.user,
        )

        response = self.client.post(reverse("bookkeeping:period_lock_reopen", args=[lock.pk]), {"reopened_reason": ""})

        self.assertEqual(response.status_code, 302)
        lock.refresh_from_db()
        self.assertTrue(lock.is_locked)

    def test_period_lock_reopen_then_relock_round_trip(self):
        lock = PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Januari",
            locked_by=self.user,
        )

        reopen_response = self.client.post(
            reverse("bookkeeping:period_lock_reopen", args=[lock.pk]),
            {"reopened_reason": "Rättelse av felbokning"},
        )
        self.assertEqual(reopen_response.status_code, 302)
        lock.refresh_from_db()
        self.assertFalse(lock.is_locked)
        self.assertEqual(lock.reopened_reason, "Rättelse av felbokning")
        self.assertEqual(lock.reopened_by, self.user)
        self.assertIsNotNone(lock.reopened_at)
        self.assertFalse(is_date_locked(self.company, date(2026, 1, 15)))

        relock_response = self.client.post(
            reverse("bookkeeping:period_lock_relock", args=[lock.pk]),
            {"reason": "Rättelse klar, låser igen"},
        )
        self.assertEqual(relock_response.status_code, 302)
        lock.refresh_from_db()
        self.assertTrue(lock.is_locked)
        self.assertEqual(lock.reason, "Rättelse klar, låser igen")
        self.assertEqual(lock.reopened_reason, "")
        self.assertIsNone(lock.reopened_by)
        self.assertIsNone(lock.reopened_at)
        self.assertTrue(is_date_locked(self.company, date(2026, 1, 15)))

    def test_period_lock_actions_require_finance_admin_role(self):
        regular_user = create_user("regular@example.com")
        self.company.users.add(regular_user)
        self.client.force_login(regular_user)
        set_active_company(self.client, self.company)

        response = self.client.post(
            f"{reverse('bookkeeping:period_lock_create')}?year={self.year.pk}",
            {
                "accounting_year": self.year.pk,
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "reason": "Ska nekas",
            },
            follow=True,
        )

        self.assertFalse(PeriodLock.objects.filter(company=self.company).exists())
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("behörighet" in m for m in messages))

    def test_period_lock_changes_are_audit_logged(self):
        self.client.post(
            f"{reverse('bookkeeping:period_lock_create')}?year={self.year.pk}",
            {
                "accounting_year": self.year.pk,
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "reason": "Månadsavstämning januari",
            },
        )
        lock = PeriodLock.objects.get(company=self.company)

        self.client.post(
            reverse("bookkeeping:period_lock_reopen", args=[lock.pk]),
            {"reopened_reason": "Rättelse"},
        )

        entries = AuditLogEntry.objects.filter(model_label="bookkeeping.periodlock", object_pk=str(lock.pk)).order_by(
            "id"
        )
        self.assertEqual(
            list(entries.values_list("action", flat=True)),
            [
                AuditLogEntry.Action.CREATE,
                AuditLogEntry.Action.UPDATE,
            ],
        )

    def test_suggest_monthly_periods_excludes_current_and_future_months(self):
        from bookkeeping.period_locking import suggest_monthly_periods

        periods = suggest_monthly_periods(self.year, today=date(2026, 3, 10))

        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0]["start_date"], date(2026, 1, 1))
        self.assertEqual(periods[0]["end_date"], date(2026, 1, 31))
        self.assertFalse(periods[0]["is_locked"])
        self.assertEqual(periods[1]["start_date"], date(2026, 2, 1))
        self.assertEqual(periods[1]["end_date"], date(2026, 2, 28))

    def test_suggest_monthly_periods_defaults_to_the_real_today(self):
        """Every other test pins `today`, but the views call this with no argument,
        so the default branch is the one that actually runs in production."""
        from bookkeeping.period_locking import suggest_monthly_periods

        past_year = AccountingYear.objects.create(
            company=self.company,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
        )

        periods = suggest_monthly_periods(past_year)

        # A year that ended long ago has all twelve months elapsed under any "today".
        self.assertEqual(len(periods), 12)
        self.assertEqual(periods[0]["start_date"], date(2020, 1, 1))
        self.assertEqual(periods[-1]["end_date"], date(2020, 12, 31))

    def test_suggest_monthly_periods_marks_locked_months(self):
        from bookkeeping.period_locking import suggest_monthly_periods

        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Januari",
            locked_by=self.user,
        )

        periods = suggest_monthly_periods(self.year, today=date(2026, 3, 10))

        self.assertTrue(periods[0]["is_locked"])
        self.assertFalse(periods[1]["is_locked"])

    def _create_lock(self, period_start, period_end, is_locked=True):
        return PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=period_start,
            period_end=period_end,
            is_locked=is_locked,
            reason="Test",
            locked_by=self.user,
        )

    def test_year_lock_status_open_without_locks(self):
        from bookkeeping.period_locking import year_lock_status

        self.assertEqual(year_lock_status(self.year), "open")

    def test_year_lock_status_ignores_reopened_locks(self):
        from bookkeeping.period_locking import year_lock_status

        self._create_lock(date(2026, 1, 1), date(2026, 12, 31), is_locked=False)

        self.assertEqual(year_lock_status(self.year), "open")

    def test_year_lock_status_partial_with_one_locked_month(self):
        from bookkeeping.period_locking import year_lock_status

        self._create_lock(date(2026, 1, 1), date(2026, 1, 31))

        self.assertEqual(year_lock_status(self.year), "partial")

    def test_year_lock_status_partial_with_gap_between_locks(self):
        from bookkeeping.period_locking import year_lock_status

        self._create_lock(date(2026, 1, 1), date(2026, 1, 31))
        self._create_lock(date(2026, 3, 1), date(2026, 12, 31))

        self.assertEqual(year_lock_status(self.year), "partial")

    def test_year_lock_status_locked_by_adjacent_locks(self):
        from bookkeeping.period_locking import year_lock_status

        self._create_lock(date(2026, 1, 1), date(2026, 6, 30))
        self._create_lock(date(2026, 7, 1), date(2026, 12, 31))

        self.assertEqual(year_lock_status(self.year), "locked")

    def test_accounting_year_list_shows_lock_status(self):
        response = self.client.get(reverse("bookkeeping:accounting_year_list"))
        self.assertContains(response, "Öppet")

        self._create_lock(date(2026, 1, 1), date(2026, 1, 31))
        response = self.client.get(reverse("bookkeeping:accounting_year_list"))
        self.assertContains(response, "Delvis öppet")

        self._create_lock(date(2026, 2, 1), date(2026, 12, 31))
        response = self.client.get(reverse("bookkeeping:accounting_year_list"))
        self.assertContains(response, "Låst")
