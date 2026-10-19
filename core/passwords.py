import hashlib
import hmac
import secrets

PREFIX = 'pbkdf2_sha256$'


def hash_password(password):
    salt = secrets.token_hex(16)
    iterations = 200000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), iterations).hex()
    return f'{PREFIX}{iterations}${salt}${digest}'


def verify_password(password, stored):
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    if not stored.startswith(PREFIX):
        return hmac.compare_digest(password.encode('utf-8'), stored.encode('utf-8'))
    try:
        _, iterations, salt, digest = stored.split('$')
        iterations = int(iterations)
        if not 1 <= iterations <= 2000000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), iterations).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False
