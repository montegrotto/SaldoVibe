"""GDPR G-009: mailkredentialer ska vara oläsbara i en kopierad databasfil."""

from django.db import connection

from saldovibe.testing import CompanyTestCase


class CredentialEncryptionTests(CompanyTestCase):
    user_email = "kreditera@example.com"
    company_name = "Kredentialbolaget AB"

    def test_round_trip_and_no_plaintext_in_db(self):
        self.company.email_fetch_password = "app-losenord-123"
        self.company.email_fetch_oauth_client_secret = "oauth-secret-456"
        self.company.email_send_smtp_password = "smtp-losenord-789"
        self.company.email_notify_smtp_password = "notis-losenord-012"
        self.company.save()

        self.company.refresh_from_db()
        self.assertEqual(self.company.email_fetch_password, "app-losenord-123")
        self.assertEqual(self.company.email_fetch_oauth_client_secret, "oauth-secret-456")
        self.assertEqual(self.company.email_send_smtp_password, "smtp-losenord-789")
        self.assertEqual(self.company.email_notify_smtp_password, "notis-losenord-012")

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email_fetch_password, email_fetch_oauth_client_secret, "
                "email_send_smtp_password, email_notify_smtp_password "
                "FROM bookkeeping_company WHERE id = %s",
                [self.company.pk],
            )
            raw_values = cursor.fetchone()
        for raw in raw_values:
            self.assertTrue(raw.startswith("gAAAAA"))

    def test_blank_credentials_stay_blank(self):
        self.company.refresh_from_db()
        self.assertEqual(self.company.email_fetch_password, "")
        self.assertEqual(self.company.email_send_smtp_password, "")
