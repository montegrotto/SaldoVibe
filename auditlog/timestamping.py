"""RFC 3161 trusted timestamping client - external tamper-evidence anchor for
the audit hash chain.

Why: `reseal_audit_chain --apply` can recompute prev_hash/entry_hash for the
whole chain, which makes an internally-tampered chain self-consistent again.
Periodically sending the current chain tip's entry_hash to an external,
independent Time-Stamp Authority (TSA) and keeping the signed response gives
a record of what the hash actually was at a point in time, from a party
nobody running this server controls. If the chain is later resealed after
tampering, the recomputed hash for the anchored entry will no longer match
the value the TSA attested to - proving tampering even though the chain
looks internally consistent again.

Verification uses the `openssl ts -verify` reference implementation rather
than reimplementing CMS/PKCS7 signature verification in Python: the
`rfc3161ng` library's own `check_timestamp()` only supports RSA-signed
tokens (hardcodes PKCS1v15 padding) and raises on FreeTSA's ECDSA-signed
responses, so it's used here only to build/parse requests, never to verify.
"""

import subprocess
import tempfile
from pathlib import Path

import rfc3161ng
from django.conf import settings


class TimestampRequestError(Exception):
    """Raised when the TSA can't be reached or returns an unusable response."""


class TimestampVerificationError(Exception):
    """Raised when a stored timestamp token fails cryptographic verification."""


def _tsa_config():
    return {
        "url": settings.AUDIT_CHAIN_TSA_URL,
        "ca_cert": settings.AUDIT_CHAIN_TSA_CA_CERT,
        "hashname": settings.AUDIT_CHAIN_TSA_HASHNAME,
        "timeout": settings.AUDIT_CHAIN_TSA_TIMEOUT,
    }


def request_timestamp(data: bytes) -> bytes:
    """Ask the configured TSA to timestamp `data`. Returns the raw, DER-encoded
    TimeStampToken (with the signing certificate embedded, for self-contained
    offline verification later). Raises TimestampRequestError on any failure -
    this must never take down the caller's larger job (e.g. the monthly
    schedule); callers should catch it and log/report rather than crash."""

    config = _tsa_config()
    try:
        timestamper = rfc3161ng.RemoteTimestamper(
            config["url"],
            hashname=config["hashname"],
            include_tsa_certificate=True,
            timeout=config["timeout"],
        )
        return timestamper.timestamp(data=data)
    except Exception as exc:
        raise TimestampRequestError(f"Kunde inte hämta tidsstämpel från {config['url']}: {exc}") from exc


def get_asserted_time(token: bytes):
    """Return the datetime the TSA asserted in the token (its own claimed
    time - not itself a trust guarantee, that's what verify_timestamp_token
    checks; this is just for display)."""
    return rfc3161ng.get_timestamp(token, naive=False)


def verify_timestamp_token(token: bytes, data: bytes) -> None:
    """Verify that `token` is a validly-signed RFC 3161 timestamp (signed by
    a TSA chaining to our bundled CA cert) attesting to a hash of `data`.
    Raises TimestampVerificationError on any failure; returns None on success.
    """

    config = _tsa_config()
    ca_cert_path = Path(config["ca_cert"])
    if not ca_cert_path.exists():
        raise TimestampVerificationError(f"TSA CA-certifikat saknas: {ca_cert_path}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        token_path = Path(tmp_dir) / "token.tsr"
        data_path = Path(tmp_dir) / "anchored.data"
        token_path.write_bytes(token)
        data_path.write_bytes(data)

        result = subprocess.run(
            [
                "openssl",
                "ts",
                "-verify",
                "-in",
                str(token_path),
                "-token_in",
                "-data",
                str(data_path),
                "-CAfile",
                str(ca_cert_path),
            ],
            capture_output=True,
            text=True,
        )

    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        raise TimestampVerificationError(f"Tidsstämpeln kunde inte verifieras: {detail}")
