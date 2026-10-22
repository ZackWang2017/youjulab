"""账号 JWT：签发、过期与校验，不访问数据库。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "account-service" / "service"))

import account_jwt  # noqa: E402


class JwtTests(unittest.TestCase):
    def test_round_trip(self):
        token = account_jwt.issue("user-1", "a@example.com")
        claims = account_jwt.verify(token)
        self.assertIsNotNone(claims)
        self.assertEqual(claims["sub"], "user-1")
        self.assertEqual(claims["email"], "a@example.com")

    def test_rejects_tamper_and_expiry(self):
        token = account_jwt.issue("user-1", "a@example.com", days=-1)
        self.assertIsNone(account_jwt.verify(token))
        fresh = account_jwt.issue("user-1", "a@example.com")
        self.assertIsNone(account_jwt.verify(fresh + "x"))
        self.assertGreater(account_jwt.verify(fresh)["exp"], int(time.time()))


if __name__ == "__main__":
    unittest.main()
