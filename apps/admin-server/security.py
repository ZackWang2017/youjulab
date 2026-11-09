import base64
from collections import defaultdict, deque
import hashlib
import hmac
import secrets
import time


def password_hash(password):
    if len(password) < 12:
        raise ValueError('管理员密码至少 12 个字符。')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    return 'pbkdf2_sha256$600000$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 2000000:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


class LoginLimiter:
    """Single-worker deployment: a rolling five failures per IP per ten minutes."""
    def __init__(self):
        self.failures = defaultdict(deque)

    def allowed(self, key):
        now = time.monotonic()
        for address in list(self.failures):
            queue = self.failures[address]
            while queue and queue[0] < now - 600:
                queue.popleft()
            if not queue:
                del self.failures[address]
        return len(self.failures.get(key, ())) < 5

    def failed(self, key):
        self.failures[key].append(time.monotonic())


if __name__ == '__main__':
    import getpass
    print(password_hash(getpass.getpass('管理员密码（至少 12 位）: ')))
