import html
import re
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from saldovibe.testing import CompanyTestCase

from .models import ApiToken


class RegistrationTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()

    def _register(self, email):
        return self.client.post(
            reverse("accounts:register"),
            {
                "email": email,
                "first_name": "Test",
                "last_name": "Person",
                "password1": "safe-password-123",
                "password2": "safe-password-123",
            },
        )

    def test_first_registered_user_becomes_admin(self):
        response = self._register("first@example.com")

        self.assertEqual(response.status_code, 302)
        user = self.user_model.objects.get(email="first@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_subsequent_registered_users_are_not_admins(self):
        self._register("first@example.com")
        self.client.logout()
        self._register("second@example.com")

        user = self.user_model.objects.get(email="second@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_first_user_can_access_django_admin(self):
        self._register("first@example.com")

        response = self.client.get("/admin/", follow=False)
        self.assertEqual(response.status_code, 200)


class PasswordValidationTests(TestCase):
    def test_registration_rejects_weak_password(self):
        response = self.client.post(
            reverse("accounts:register"),
            {"email": "weak@example.com", "first_name": "A", "last_name": "B", "password1": "123", "password2": "123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(email="weak@example.com").exists())


class PasswordResetTests(TestCase):
    def test_reset_link_sets_new_password(self):
        user = get_user_model().objects.create_user("anna@example.com", "gammalt-losen-123")

        response = self.client.post(reverse("accounts:password_reset"), {"email": "anna@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["anna@example.com"])
        link = re.search(r"https?://\S+/nytt-losenord/\S+/", mail.outbox[0].body).group(0)
        path = link.split("://", 1)[1].split("/", 1)[1]

        response = self.client.get("/" + path)  # redirects to the session-token URL
        response = self.client.post(
            response.url,
            {"new_password1": "nytt-safe-losen-456", "new_password2": "nytt-safe-losen-456"},
        )
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("nytt-safe-losen-456"))

    def test_unknown_email_is_silent(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": "ingen@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_login_page_links_to_reset(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:password_reset"))


class LoginRedirectTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user("anna@example.com", "losen-123-abc")

    def _login(self, next_url):
        return self.client.post(
            reverse("accounts:login") + "?next=" + next_url,
            {"username": "anna@example.com", "password": "losen-123-abc"},
        )

    def test_local_next_is_followed(self):
        response = self._login("/hjalp/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/hjalp/")

    def test_external_next_is_ignored(self):
        for evil in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
            with self.subTest(evil=evil):
                self.client.logout()
                response = self._login(evil)
                self.assertRedirects(response, reverse("bookkeeping:dashboard"), fetch_redirect_response=False)


class AppPageTests(CompanyTestCase):
    """/konton/appen/: the one-time QR login for the mobile app and the token list."""

    user_email = "appsida@example.com"
    company_name = "Appsidan AB"

    def setUp(self):
        super().setUp()
        self.url = reverse("accounts:app")

    def _login_link_query(self, html_text):
        match = re.search(r'href="(saldovibe://login\?[^"]+)"', html_text)
        self.assertIsNotNone(match, "QR-sidan saknar inloggningslänk")
        return parse_qs(urlsplit(html.unescape(match.group(1))).query)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={self.url}", fetch_redirect_response=False)

    def test_page_without_tokens_says_the_app_is_not_logged_in(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Appen är inte inloggad")
        self.assertNotContains(response, "<svg")

    def test_post_shows_a_one_time_qr_whose_token_logs_the_app_in(self):
        response = self.client.post(self.url, {})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(ApiToken.objects.get(user=self.user).name, "QR-kod")

        page = self.client.get(self.url)
        self.assertContains(page, "<svg")
        query = self._login_link_query(page.content.decode())
        self.assertEqual(query["server"], ["http://testserver/"])
        me = Client().get("/api/v1/me/", headers={"Authorization": f"Bearer {query['token'][0]}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["email"], self.user_email)

        again = self.client.get(self.url)
        self.assertNotContains(again, "<svg")
        self.assertContains(again, "QR-kod")

    def test_revoke_deletes_the_token_and_logs_the_app_out(self):
        raw_token = ApiToken.issue(self.user, name="iPhone")
        token = ApiToken.objects.get(user=self.user)

        response = self.client.post(self.url, {"revoke": str(token.pk)})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse(ApiToken.objects.filter(pk=token.pk).exists())
        me = Client().get("/api/v1/me/", headers={"Authorization": f"Bearer {raw_token}"})
        self.assertEqual(me.status_code, 401)
