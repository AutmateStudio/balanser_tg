"""HTTP-тесты POST /{parser_id}/enroll-session-from-archive."""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import pyzipper
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_telethon_sqlite(path: str, *, auth_key: bytes | None = None) -> None:
    auth_key = auth_key or (b"\x11" * 256)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, "
            "auth_key BLOB, takeout_id INTEGER)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (5, '91.108.56.130', 443, ?, NULL)",
            (auth_key,),
        )
        conn.commit()
    finally:
        conn.close()


def _aes_zip_with_telethon(password: str, session_id: str = "247542045") -> bytes:
    with tempfile.TemporaryDirectory() as td:
        sess = os.path.join(td, f"{session_id}_telethon.session")
        _make_telethon_sqlite(sess)
        with open(sess, "rb") as f:
            sess_bytes = f.read()
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(f"{session_id}_telethon.session", sess_bytes)
        zf.writestr(f"{session_id}.json", b'{"phone":null}')
    return buf.getvalue()


class EnrollFromArchiveEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        os.environ["ACCOUNT_STORE_PATH"] = os.path.join(self._tmpdir, "acc.db")
        os.environ["SESSIONS_DIR"] = self._tmpdir
        os.environ["API_ID"] = "1"
        os.environ["API_HASH"] = "hash"
        os.environ["SESSION_ARCHIVE_MAX_MB"] = "25"
        from discovery_api.account_store import reset_account_db_for_tests
        from discovery_api.parser_router import _jobs, parser_router

        reset_account_db_for_tests()
        _jobs.clear()
        app = FastAPI()
        app.include_router(parser_router)
        self.client = TestClient(app)
        self._jobs = _jobs
        self._tmpdir_before = set(os.listdir(tempfile.gettempdir()))

    def tearDown(self) -> None:
        self._jobs.clear()
        for key in (
            "PARSER_PERSISTENCE_ENABLED",
            "ACCOUNT_STORE_PATH",
            "SESSIONS_DIR",
            "API_ID",
            "API_HASH",
            "SESSION_ARCHIVE_MAX_MB",
        ):
            os.environ.pop(key, None)

    def _running_clump(self, parser_id: str = "pid"):
        from discovery_api.parser_router import _ClumpJob
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s0"], "c", webhook_url="http://h")
        self._jobs[parser_id] = _ClumpJob(clump=clump, parser_id=parser_id)
        return clump

    def _assert_no_leftover_extract_dirs(self) -> None:
        after = set(os.listdir(tempfile.gettempdir()))
        new = after - self._tmpdir_before
        leftovers = [
            n
            for n in new
            if n.startswith("session_archive_") or n.startswith("session_convert_")
        ]
        self.assertEqual(
            leftovers,
            [],
            f"Остались temp-каталоги распаковки: {leftovers}",
        )

    def test_happy_path(self) -> None:
        from discovery_api.session_dialogs import AccountMembershipSnapshot

        clump = self._running_clump()
        zip_bytes = _aes_zip_with_telethon("jam")
        with (
            patch.object(clump, "start", new_callable=AsyncMock),
            patch(
                "discovery_api.session_dialogs.scan_account_channel_membership",
                new_callable=AsyncMock,
                return_value=AccountMembershipSnapshot(1, 0, 0),
            ),
            patch("discovery_api.parser_router._persist_clump_state"),
            patch(
                "discovery_api.parser_router._sync_accounts_pg",
                new_callable=AsyncMock,
            ),
            patch(
                "discovery_api.session_archive.probe_session_authorized",
                new_callable=AsyncMock,
            ),
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/enroll-session-from-archive",
                files={"file": ("retriv.zip", zip_bytes, "application/zip")},
                data={"password": "jam"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["session_name"], "247542045")
        self.assertTrue(data["in_clump"])
        self.assertTrue(
            os.path.isfile(os.path.join(self._tmpdir, "247542045.session"))
        )
        self._assert_no_leftover_extract_dirs()

    def test_wrong_password(self) -> None:
        self._running_clump()
        zip_bytes = _aes_zip_with_telethon("jam")
        resp = self.client.post(
            "/discovery-api/parser/pid/enroll-session-from-archive",
            files={"file": ("retriv.zip", zip_bytes, "application/zip")},
            data={"password": "nope"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(
            os.path.isfile(os.path.join(self._tmpdir, "247542045.session"))
        )
        self._assert_no_leftover_extract_dirs()

    def test_parser_missing_404(self) -> None:
        zip_bytes = _aes_zip_with_telethon("jam")
        resp = self.client.post(
            "/discovery-api/parser/missing/enroll-session-from-archive",
            files={"file": ("retriv.zip", zip_bytes, "application/zip")},
            data={"password": "jam"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_clump_stopped_409(self) -> None:
        from discovery_api.parser_router import _ClumpJob
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s0"], "c", webhook_url="http://h")
        job = _ClumpJob(clump=clump, parser_id="pid")
        job.finished = True
        self._jobs["pid"] = job
        zip_bytes = _aes_zip_with_telethon("jam")
        resp = self.client.post(
            "/discovery-api/parser/pid/enroll-session-from-archive",
            files={"file": ("retriv.zip", zip_bytes, "application/zip")},
            data={"password": "jam"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_exists_without_overwrite_409(self) -> None:
        self._running_clump()
        open(os.path.join(self._tmpdir, "247542045.session"), "wb").close()
        zip_bytes = _aes_zip_with_telethon("jam")
        with patch(
            "discovery_api.session_archive.probe_session_authorized",
            new_callable=AsyncMock,
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/enroll-session-from-archive",
                files={"file": ("retriv.zip", zip_bytes, "application/zip")},
                data={"password": "jam", "overwrite": "false"},
            )
        self.assertEqual(resp.status_code, 409)

    def test_overwrite_replaces(self) -> None:
        from discovery_api.session_dialogs import AccountMembershipSnapshot

        clump = self._running_clump()
        old = os.path.join(self._tmpdir, "247542045.session")
        with open(old, "wb") as f:
            f.write(b"OLD")
        zip_bytes = _aes_zip_with_telethon("jam")
        with (
            patch.object(clump, "start", new_callable=AsyncMock),
            patch(
                "discovery_api.session_dialogs.scan_account_channel_membership",
                new_callable=AsyncMock,
                return_value=AccountMembershipSnapshot(0, 0, 0),
            ),
            patch("discovery_api.parser_router._persist_clump_state"),
            patch(
                "discovery_api.parser_router._sync_accounts_pg",
                new_callable=AsyncMock,
            ),
            patch(
                "discovery_api.session_archive.probe_session_authorized",
                new_callable=AsyncMock,
            ),
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/enroll-session-from-archive",
                files={"file": ("retriv.zip", zip_bytes, "application/zip")},
                data={"password": "jam", "overwrite": "true"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        with open(old, "rb") as f:
            self.assertNotEqual(f.read(), b"OLD")
        self.assertFalse(os.path.isfile(old + ".bak"))
        self._assert_no_leftover_extract_dirs()

    def test_auth_failed_no_file_left(self) -> None:
        from discovery_api.session_archive import ArchiveSessionError

        self._running_clump()
        zip_bytes = _aes_zip_with_telethon("jam")
        with patch(
            "discovery_api.session_archive.probe_session_authorized",
            new_callable=AsyncMock,
            side_effect=ArchiveSessionError("auth_failed", "Сессия не авторизована"),
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/enroll-session-from-archive",
                files={"file": ("retriv.zip", zip_bytes, "application/zip")},
                data={"password": "jam"},
            )
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(
            os.path.isfile(os.path.join(self._tmpdir, "247542045.session"))
        )
        self._assert_no_leftover_extract_dirs()

    def test_oversized_upload_413(self) -> None:
        self._running_clump()
        os.environ["SESSION_ARCHIVE_MAX_MB"] = "1"
        # 1.5 MiB payload
        big = b"x" * (1536 * 1024)
        resp = self.client.post(
            "/discovery-api/parser/pid/enroll-session-from-archive",
            files={"file": ("big.zip", big, "application/zip")},
            data={"password": "x"},
        )
        self.assertEqual(resp.status_code, 413)
        self._assert_no_leftover_extract_dirs()


if __name__ == "__main__":
    unittest.main()
