import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse


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
