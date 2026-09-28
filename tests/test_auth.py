import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_platform.auth import (
    AuthenticationError,
    authenticate,
    create_user,
    issue_token,
    verify_token,
)
from trading_platform.models import Base


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = __import__("os").environ.get("TRADING_PLATFORM_AUTH_SIGNING_KEY")
        __import__("os").environ["TRADING_PLATFORM_AUTH_SIGNING_KEY"] = (
            "unit-test-only-signing-key-32-bytes-minimum"
        )
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        if self.previous_key is None:
            __import__("os").environ.pop("TRADING_PLATFORM_AUTH_SIGNING_KEY", None)
        else:
            __import__("os").environ["TRADING_PLATFORM_AUTH_SIGNING_KEY"] = self.previous_key

    def test_admin_password_hash_token_and_disabled_account(self) -> None:
        user = create_user(self.session, "Operator", "correct horse battery 42!")
        self.session.commit()
        self.assertNotIn("correct horse", user.password_hash)
        principal = authenticate(self.session, "operator", "correct horse battery 42!")
        token = issue_token(principal)
        self.assertEqual(verify_token(token).username, "operator")
        user.enabled = False
        self.session.commit()
        with self.assertRaises(AuthenticationError):
            authenticate(self.session, "operator", "correct horse battery 42!")

    def test_short_password_and_tampered_token_fail(self) -> None:
        with self.assertRaises(ValueError):
            create_user(self.session, "x", "short")
        user = create_user(self.session, "operator", "a sufficiently long test password")
        self.session.commit()
        token = issue_token(
            authenticate(self.session, user.username, "a sufficiently long test password")
        )
        with self.assertRaises(AuthenticationError):
            verify_token(token + "tampered")


if __name__ == "__main__":
    unittest.main()
