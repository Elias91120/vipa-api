import hashlib
import hmac


def verify_hmac_sha256(*, secret: str, body: bytes, signature_header: str | None) -> bool:
    """Validate X-Vipa-Signature: sha256=<hex> (or bare hex)."""
    if not secret or not signature_header:
        return False

    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1].strip()

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
