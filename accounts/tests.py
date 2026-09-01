from django.contrib.auth import get_user_model
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
