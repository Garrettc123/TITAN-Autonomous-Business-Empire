"""Offline tests: node heartbeat remains disabled by default and is HMAC scoped."""
import hashlib
import hmac
import importlib
import json
import unittest
from unittest.mock import patch

mod = importlib.import_module("src.mesh_heartbeat")


class FakeResponse:
    status = 200

    def __init__(self, request):
        self.request = request

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        data = json.loads(self.request.data)
        return json.dumps({
            "event_id": data["event_id"],
            "signed_node": mod.NODE_ID,
            "action_authority": "none",
            "status": "published",
        }).encode()


class FakeOpener:
    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return FakeResponse(request)


class MeshHeartbeatTests(unittest.TestCase):
    def test_disabled_never_opens_network(self):
        with patch.object(mod, "build_opener", side_effect=AssertionError("network forbidden")):
            self.assertEqual(
                mod.send_heartbeat(environ={}),
                {"state": "disabled", "node": mod.NODE_ID},
            )

    def test_explicitly_enabled_status_is_signed_and_receipt_checked(self):
        opener = FakeOpener()
        env = {
            "GARCAR_MESH_PUBLISH_ENABLED": "true",
            "GARCAR_MESH_NODE_KEY": "unit-test-only-secret-00000000000000001",
            "GARCAR_SYNC_CORE_ORIGIN": "https://sync.example.com",
            "GARCAR_RELEASE_SHA": "head-123",
        }
        with patch.object(mod, "build_opener", return_value=opener):
            result = mod.send_heartbeat(status="degraded", environ=env)
        self.assertEqual(result["state"], "acknowledged")
        self.assertFalse(result["sync_delivery_verified"])
        event = json.loads(opener.request.data)
        self.assertEqual(event["source"], mod.NODE_ID)
        self.assertEqual(event["type"], "mesh.heartbeat.v1")
        self.assertEqual(event["payload"], {"status": "degraded", "revision": "head-123"})
        self.assertNotIn("command", event)
        header = opener.request.headers
        signed = hmac.new(
            env["GARCAR_MESH_NODE_KEY"].encode(),
            header["X-mesh-time"].encode() + b"." + opener.request.data,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(header["X-mesh-signature"], signed)
        self.assertEqual(header["X-mesh-node"], mod.NODE_ID)
        self.assertEqual(opener.timeout, 4)

    def test_invalid_destination_is_rejected_before_network(self):
        base = {
            "GARCAR_MESH_PUBLISH_ENABLED": "true",
            "GARCAR_MESH_NODE_KEY": "unit-test-only-secret-00000000000000001",
        }
        for bad in ("http://sync.example.com", "https://127.0.0.1",
                    "https://sync.example.com/path", "https://user:pass@sync.example.com"):
            with self.subTest(bad=bad):
                with patch.object(mod, "build_opener", side_effect=AssertionError("network forbidden")):
                    with self.assertRaises(ValueError):
                        mod.send_heartbeat(environ={**base, "GARCAR_SYNC_CORE_ORIGIN": bad})

    def test_missing_signer_is_rejected_before_network(self):
        with patch.object(mod, "build_opener", side_effect=AssertionError("network forbidden")):
            with self.assertRaises(RuntimeError):
                mod.send_heartbeat(environ={
                    "GARCAR_MESH_PUBLISH_ENABLED": "true",
                    "GARCAR_MESH_NODE_KEY": "short",
                    "GARCAR_SYNC_CORE_ORIGIN": "https://sync.example.com",
                })


if __name__ == "__main__":
    unittest.main()
