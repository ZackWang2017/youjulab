"""表格口令加密：正确解密、错密、错格式。不访问数据库。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sheet_crypto import (  # noqa: E402
    FORMAT,
    SheetCryptoError,
    WRONG_PASSWORD,
    decrypt_sheet,
    encrypt_sheet,
    is_encrypted,
)

FAST = {"n": 16, "r": 8, "p": 1, "dklen": 32}
PLAIN = {"id": "s1", "name": "机密", "sheetOrder": ["a"], "sheets": {"a": {"name": "Sheet1"}}}


class SheetCryptoTests(unittest.TestCase):
    def test_round_trip(self):
        envelope = encrypt_sheet(PLAIN, "secret1", asset_id="asset-1", kdf_params=FAST)
        self.assertTrue(is_encrypted(envelope))
        self.assertEqual(envelope["format"], FORMAT)
        self.assertNotIn("sheets", envelope)
        self.assertEqual(decrypt_sheet(envelope, "secret1", asset_id="asset-1"), PLAIN)

    def test_wrong_password(self):
        envelope = encrypt_sheet(PLAIN, "secret1", asset_id="asset-1", kdf_params=FAST)
        with self.assertRaises(SheetCryptoError) as ctx:
            decrypt_sheet(envelope, "secret2", asset_id="asset-1")
        self.assertEqual(str(ctx.exception), WRONG_PASSWORD)

    def test_wrong_asset_id_fails(self):
        envelope = encrypt_sheet(PLAIN, "secret1", asset_id="asset-1", kdf_params=FAST)
        with self.assertRaises(SheetCryptoError):
            decrypt_sheet(envelope, "secret1", asset_id="asset-2")

    def test_plain_content_is_not_encrypted(self):
        self.assertFalse(is_encrypted(PLAIN))
        self.assertFalse(is_encrypted(None))
        with self.assertRaises(SheetCryptoError) as ctx:
            decrypt_sheet(PLAIN, "secret1")
        self.assertEqual(str(ctx.exception), "表格未加密。")

    def test_short_password_rejected(self):
        with self.assertRaises(SheetCryptoError):
            encrypt_sheet(PLAIN, "12345", kdf_params=FAST)

    def test_corrupt_ciphertext(self):
        envelope = encrypt_sheet(PLAIN, "secret1", asset_id="a", kdf_params=FAST)
        envelope["ciphertext"] = envelope["ciphertext"][:-4] + "xxxx"
        with self.assertRaises(SheetCryptoError):
            decrypt_sheet(envelope, "secret1", asset_id="a")


if __name__ == "__main__":
    unittest.main()
