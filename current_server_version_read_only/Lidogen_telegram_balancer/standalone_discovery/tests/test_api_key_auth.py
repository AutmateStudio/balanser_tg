import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient


class ApiKeyAuthTests(unittest.TestCase):
    def test_health_is_open(self) -> None:
        from discovery_api.main import app

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/health")
        self.assertEqual(r.status_code, 200, r.text)

    def test_protected_endpoint_503_when_api_key_missing(self) -> None:
        from discovery_api.main import app

        with patch.dict(os.environ, {"API_KEY": ""}, clear=False):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/discovery-api/auth/qr")
            self.assertEqual(r.status_code, 503, r.text)

    def test_protected_endpoint_401_without_x_api_key(self) -> None:
        from discovery_api.main import app

        with patch.dict(os.environ, {"API_KEY": "in-key-1"}, clear=False):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/discovery-api/auth/qr")
            self.assertEqual(r.status_code, 401, r.text)

    def test_protected_endpoint_401_with_wrong_x_api_key(self) -> None:
        from discovery_api.main import app

        with patch.dict(os.environ, {"API_KEY": "in-key-1"}, clear=False):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/discovery-api/auth/qr", headers={"X-API-Key": "wrong"})
            self.assertEqual(r.status_code, 401, r.text)


if __name__ == "__main__":
    unittest.main()

