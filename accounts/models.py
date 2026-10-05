import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("E-postadress krävs")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    class Meta:
        verbose_name = "Användare"
        verbose_name_plural = "Användare"

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email


class ApiToken(models.Model):
    """Mobilappens inloggning (api/). Bara SHA-256 av token sparas; klartexten lämnar
    servern en gång - i svaret på inloggningen eller i QR-koden - och lever sedan i
    appens nyckelring. En token som aldrig använts förfaller efter UNUSED_LIFETIME, så
    en visad men aldrig skannad QR-kod inte blir en evig bakdörr."""

    UNUSED_LIFETIME = timedelta(minutes=15)
    LAST_USED_RESOLUTION = timedelta(hours=1)

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField("Enhet", max_length=100, blank=True)
    token_hash = models.CharField("Tokenhash", max_length=64, unique=True)
    created_at = models.DateTimeField("Skapad", auto_now_add=True)
    last_used_at = models.DateTimeField("Senast använd", null=True, blank=True)

    class Meta:
        verbose_name = "Appinloggning"
        verbose_name_plural = "Appinloggningar"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name or 'App'} ({self.user})"

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @classmethod
    def issue(cls, user, name=""):
        """Create a token for `user` and return it in clear text - the only time it exists."""
        raw_token = secrets.token_urlsafe(32)
        cls.objects.create(user=user, name=name[:100], token_hash=cls.hash_token(raw_token))
        return raw_token

    @classmethod
    def resolve(cls, raw_token):
        """The token row for a presented token, or None if it is unknown, revoked, expired
        unused, or belongs to a deactivated user. Stamps last_used_at (at most hourly)."""
        if not raw_token:
            return None
        token = cls.objects.select_related("user").filter(token_hash=cls.hash_token(raw_token)).first()
        if token is None or not token.user.is_active:
            return None
        now = timezone.now()
        if token.last_used_at is None and now - token.created_at > cls.UNUSED_LIFETIME:
            token.delete()
            return None
        if token.last_used_at is None or now - token.last_used_at > cls.LAST_USED_RESOLUTION:
            token.last_used_at = now
            token.save(update_fields=["last_used_at"])
        return token
