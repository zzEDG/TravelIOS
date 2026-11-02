import unittest
from app.services.passwords import hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_password_verification_and_salt(self):
        password = "Test-passphrase-2026"
        first, second = hash_password(password), hash_password(password)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("$argon2id$"))
        self.assertTrue(verify_password(first, password))
        self.assertFalse(verify_password(first, "wrong-password"))
        self.assertFalse(verify_password(None, password))
        self.assertFalse(verify_password("invalid-hash", password))
