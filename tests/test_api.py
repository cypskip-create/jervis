import os
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trading_platform import api
from trading_platform.auth import create_user
from trading_platform.models import AuditEvent, Base, Symbol


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = os.environ.get("TRADING_PLATFORM_AUTH_SIGNING_KEY")
        os.environ["TRADING_PLATFORM_AUTH_SIGNING_KEY"] = (
            "test-api-signing-key-has-at-least-32-bytes"
        )
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.original_factory = api.SessionFactory
        api.SessionFactory = self.factory
        with self.factory.begin() as session:
            create_user(session, "admin", "a strong password for local test 2026")
            session.add(Symbol(canonical="EURUSD", asset_class="forex", enabled=True))
        self.client = TestClient(api.app)

    def tearDown(self) -> None:
        self.client.close()
        api.SessionFactory = self.original_factory
        self.engine.dispose()
        if self.previous_key is None:
            os.environ.pop("TRADING_PLATFORM_AUTH_SIGNING_KEY", None)
        else:
            os.environ["TRADING_PLATFORM_AUTH_SIGNING_KEY"] = self.previous_key

    def test_authentication_roles_persistent_controls_and_audit(self) -> None:
        self.assertEqual(self.client.get("/api/v1/symbols").status_code, 401)
        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "a strong password for local test 2026"},
        )
        self.assertEqual(response.status_code, 200)
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        self.assertEqual(self.client.get("/api/v1/me", headers=headers).json()["role"], "admin")
        self.assertEqual(
            self.client.put(
                "/api/v1/controls/global", headers=headers, json={"enabled": True}
            ).status_code,
            200,
        )
        symbol = self.client.get("/api/v1/symbols", headers=headers).json()[0]
        class_row = self.client.get("/api/v1/asset-classes", headers=headers).json()[0]
        class_result = self.client.put(
            f"/api/v1/asset-classes/{class_row['name']}/control",
            headers=headers,
            json={"enabled": False},
        )
        self.assertEqual(class_result.status_code, 200)
        self.assertFalse(
            self.client.get("/api/v1/asset-classes", headers=headers).json()[0]["enabled"]
        )
        changed = self.client.put(
            f"/api/v1/symbols/{symbol['id']}/control",
            headers=headers,
            json={"enabled": False, "disable_policy": "disable_new"},
        )
        self.assertEqual(changed.status_code, 200)
        with self.factory() as session:
            self.assertEqual(session.get(Symbol, symbol["id"]).enabled, False)
            self.assertEqual(
                session.scalar(
                    select(AuditEvent).where(AuditEvent.action == "global_enabled_changed")
                ).actor,
                "admin",
            )
        self.assertEqual(
            self.client.post("/api/v1/controls/emergency-stop", headers=headers).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/controls/emergency-stop/reset",
                headers=headers,
                json={"confirm": False},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/controls/emergency-stop/reset",
                headers=headers,
                json={"confirm": True},
            ).status_code,
            200,
        )

    def test_close_policy_requires_confirmation_and_remains_unavailable(self) -> None:
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "a strong password for local test 2026"},
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        symbol = self.client.get("/api/v1/symbols", headers=headers).json()[0]
        route = f"/api/v1/symbols/{symbol['id']}/control"
        missing_confirmation = self.client.put(
            route,
            headers=headers,
            json={"enabled": False, "disable_policy": "disable_and_close"},
        )
        self.assertEqual(missing_confirmation.status_code, 422)
        unsupported = self.client.put(
            route,
            headers=headers,
            json={"enabled": False, "disable_policy": "disable_and_close", "confirm_close": True},
        )
        self.assertEqual(unsupported.status_code, 409)


if __name__ == "__main__":
    unittest.main()
