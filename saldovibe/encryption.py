"""Fältkryptering at rest (GDPR G-004, återanvänds för G-009).

Nyckeln läses från SALDOVIBE_FIELD_ENCRYPTION_KEY (en Fernet-nyckel, generera med
``Fernet.generate_key()``); utan den härleds en nyckel ur SECRET_KEY. Sätt
variabeln uttryckligen i produktion — byts SECRET_KEY utan att den är satt blir
krypterade fält oläsbara.
"""

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models

# Version-byten 0x80 base64-kodad — alla Fernet-tokens börjar så. Värden utan
# prefixet är okrypterad legacy-data från före krypteringsmigrationen.
_FERNET_PREFIX = "gAAAAA"


@lru_cache(maxsize=1)
def _fernet():
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


def encrypt_value(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(value):
    if not value.startswith(_FERNET_PREFIX):
        return value
    return _fernet().decrypt(value.encode()).decode()


def blind_index(value):
    """HMAC-index för unikhets-/exaktsökning på krypterade fält.

    Nyckelbaserad hash för att personnummerrymden är liten nog att brute-forca
    en osaltad sha256 ur en läckt databasfil.
    """
    normalized = (value or "").replace("-", "").strip()
    return hmac.new(settings.FIELD_ENCRYPTION_KEY.encode(), normalized.encode(), hashlib.sha256).hexdigest()


class EncryptedTextField(models.TextField):
    """TextField som Fernet-krypterar på väg in i databasen och dekrypterar på väg ut.

    Krypteringen är icke-deterministisk: filter/order_by på kolumnen jämför
    ciphertext, inte klartext — använd ett blind_index-fält för uppslag.
    """

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt_value(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt_value(str(value))
