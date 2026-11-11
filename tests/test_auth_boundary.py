"""Regression tests for TITAN's protected mesh/event perimeter."""
import asyncio
import base64
import json
import unittest

import httpx

from src import garcar_api_bank as api_bank
from src.full_stack_platform import GarcarPlatform


class AuthBankTests(unittest.TestCase):
    def test_default_or_blank_secret_cannot_issue_or_accept_tokens(self):
        for secret in ("", "change-me-in-production"):
            bank = api_bank.AuthBank(secret=secret)
            with self.assertRaises(RuntimeError):
                bank.issue_token("titan")
            self.assertIsNone(bank.verify_token("forged.token"))

    def test_fallback_signature_and_claims_are_verified(self):
        original = api_bank.jwt
        api_bank.jwt = None
        try:
            bank = api_bank.AuthBank(secret="test-only-long-nonproduction-signing-secret")
            good = bank.issue_token("titan", roles=["service"])
            self.assertEqual(bank.verify_token(good)["sub"], "titan")
            payload, sig = good.split(".")
            self.assertIsNone(bank.verify_token(payload + "." + "0" * 64))
            decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            decoded["roles"] = ["admin"]
            altered = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode()
            self.assertIsNone(bank.verify_token(altered + "." + sig))
        finally:
            api_bank.jwt = original


class ProtectedRoutes(unittest.TestCase):
    def test_mesh_events_and_registry_reject_missing_or_invalid_auth(self):
        async def case():
            api_bank.GarcarConfig.JWT_SECRET = "test-only-long-nonproduction-signing-secret"
            platform = GarcarPlatform(system_id="titan-business-empire", domain="finance")
            platform.bank.auth.secret = api_bank.GarcarConfig.JWT_SECRET
            transport = httpx.ASGITransport(app=platform.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                for path in ("/status", "/registry", "/events"):
                    opts = {"json": {"type": "mesh.health.observed.v1", "payload": {}}} if path == "/events" else {}
                    method = client.post if path == "/events" else client.get
                    unauthorized = await method(path, **opts)
                    self.assertEqual(unauthorized.status_code, 401, path)
                    malformed = await method(path, headers={"Authorization": "Bearer invalid"}, **opts)
                    self.assertEqual(malformed.status_code, 401, path)
                self.assertEqual((await client.get("/health")).status_code, 200)
                self.assertEqual((await client.get("/ready")).status_code, 200)
        asyncio.run(case())


if __name__ == "__main__":
    unittest.main()
