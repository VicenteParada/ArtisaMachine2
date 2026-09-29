"""Password hashing for the single local administrator account."""

import hashlib
import hmac
import secrets


ITERATIONS = 600_000


def make_password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256:{ITERATIONS}:{salt.hex()}:{digest.hex()}"


def valid_password_hash(stored):
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
        count = int(rounds)
    except (AttributeError, ValueError):
        return False
    return (algorithm == "pbkdf2_sha256" and ITERATIONS <= count <= 2_000_000
            and len(salt) >= 16 and len(digest) == 32)


def verify_password(password, stored):
    if not valid_password_hash(stored):
        return False
    _, rounds, salt_hex, digest_hex = stored.split(":")
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
    )
    return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
