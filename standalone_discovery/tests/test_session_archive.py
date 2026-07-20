"""Unit-тесты discovery_api.session_archive (без HTTP / FastAPI)."""
from __future__ import annotations

import asyncio
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import pyzipper

from discovery_api.session_archive import (
    ArchiveSessionError,
    BundleInfo,
    bundle_to_telethon_session,
    detect_bundle,
    safe_extract_zip,
)


def _make_telethon_sqlite(path: str, *, dc_id: int = 5, auth_key: bytes | None = None) -> None:
    auth_key = auth_key or (b"\x01" * 256)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, "
            "auth_key BLOB, takeout_id INTEGER)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL)",
            (dc_id, "91.108.56.130", 443, auth_key),
        )
        conn.execute(
            "CREATE TABLE entities ("
            "id INTEGER PRIMARY KEY, hash INTEGER NOT NULL, username TEXT, "
            "phone TEXT, name TEXT, date INTEGER)"
        )
        conn.execute("CREATE TABLE version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO version VALUES (7)")
        conn.commit()
    finally:
        conn.close()


def _make_pyrogram_sqlite(path: str, *, dc_id: int = 5, auth_key: bytes | None = None) -> None:
    auth_key = auth_key or (b"\xAB" * 256)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "dc_id INTEGER PRIMARY KEY, api_id INTEGER, test_mode INTEGER, "
            "auth_key BLOB, date INTEGER NOT NULL, user_id INTEGER, is_bot INTEGER)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, 12345, 0, ?, 0, 1, 0)",
            (dc_id, auth_key),
        )
        conn.commit()
    finally:
        conn.close()


def _aes_zip_bytes(entries: dict[str, bytes], password: str) -> bytes:
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode("utf-8"))
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class SafeExtractZipTests(unittest.TestCase):
    def test_correct_password_extracts(self) -> None:
        data = _aes_zip_bytes({"hello.txt": b"world"}, "secret")
        with tempfile.TemporaryDirectory() as td:
            safe_extract_zip(data, "secret", td)
            self.assertTrue(os.path.isfile(os.path.join(td, "hello.txt")))
            with open(os.path.join(td, "hello.txt"), "rb") as f:
                self.assertEqual(f.read(), b"world")

    def test_wrong_password_raises_bad_password(self) -> None:
        data = _aes_zip_bytes({"hello.txt": b"world"}, "secret")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ArchiveSessionError) as ctx:
                safe_extract_zip(data, "wrong", td)
            self.assertEqual(ctx.exception.code, "bad_password")

    def test_zip_slip_rejected(self) -> None:
        # Craft via stdlib ZipInfo with traversal name (no encryption needed for path check).
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.txt", b"pwned")
        data = buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ArchiveSessionError) as ctx:
                safe_extract_zip(data, "", td)
            self.assertEqual(ctx.exception.code, "unsafe_path")
            self.assertFalse(os.path.isfile(os.path.join(td, "evil.txt")))
            parent_evil = os.path.abspath(os.path.join(td, "..", "evil.txt"))
            # Не обязан существовать; главное — не создали внутри dest через slip
            listing = []
            for root, _dirs, files in os.walk(td):
                listing.extend(files)
            self.assertEqual(listing, [])

    def test_too_many_entries_rejected(self) -> None:
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for i in range(10):
                zf.writestr(f"f{i}.txt", b"x")
        data = buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            with patch("discovery_api.session_archive.MAX_ARCHIVE_ENTRIES", 5):
                with self.assertRaises(ArchiveSessionError) as ctx:
                    safe_extract_zip(data, "", td)
            self.assertEqual(ctx.exception.code, "archive_too_large")

    def test_oversized_uncompressed_rejected(self) -> None:
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("big.bin", b"x" * 1000)
        data = buf.getvalue()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ArchiveSessionError) as ctx:
                safe_extract_zip(data, "", td, max_extracted_bytes=100)
            self.assertEqual(ctx.exception.code, "archive_too_large")


class DetectBundleTests(unittest.TestCase):
    def test_telethon_kind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "247542045_telethon.session")
            _make_telethon_sqlite(path)
            with open(os.path.join(td, "247542045.json"), "w", encoding="utf-8") as f:
                f.write('{"phone": "+100"}')
            info = detect_bundle(td)
            self.assertEqual(info.kind, "telethon")
            self.assertEqual(info.suggested_session_name, "247542045")
            self.assertEqual(info.phone, "+100")

    def test_pyrogram_kind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "acc1_pyrogram.session")
            _make_pyrogram_sqlite(path)
            info = detect_bundle(td)
            self.assertEqual(info.kind, "pyrogram")
            self.assertEqual(info.suggested_session_name, "acc1")

    def test_tdata_kind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdata = os.path.join(td, "999001", "tdata")
            os.makedirs(tdata)
            open(os.path.join(tdata, "key_datas"), "wb").close()
            info = detect_bundle(td)
            self.assertEqual(info.kind, "tdata")
            self.assertEqual(info.suggested_session_name, "999001")

    def test_no_session_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "readme.txt"), "w", encoding="utf-8") as f:
                f.write("x")
            with self.assertRaises(ArchiveSessionError) as ctx:
                detect_bundle(td)
            self.assertEqual(ctx.exception.code, "no_session_found")

    def test_ambiguous_multiple_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _make_telethon_sqlite(os.path.join(td, "aaa_telethon.session"))
            _make_telethon_sqlite(os.path.join(td, "bbb_telethon.session"))
            with self.assertRaises(ArchiveSessionError) as ctx:
                detect_bundle(td)
            self.assertEqual(ctx.exception.code, "ambiguous_session_name")


class BundleToTelethonTests(unittest.TestCase):
    def test_telethon_byte_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src_telethon.session")
            auth = b"\x42" * 256
            _make_telethon_sqlite(src, dc_id=5, auth_key=auth)
            dest = os.path.join(td, "out.session")
            bundle = BundleInfo(kind="telethon", path=src, suggested_session_name="src")
            result = asyncio.run(bundle_to_telethon_session(bundle, dest))
            self.assertTrue(os.path.isfile(result))
            conn = sqlite3.connect(result)
            try:
                row = conn.execute(
                    "SELECT dc_id, auth_key, server_address FROM sessions"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], 5)
            self.assertEqual(bytes(row[1]), auth)
            self.assertEqual(row[2], "91.108.56.130")

    def test_pyrogram_transplant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "p_pyrogram.session")
            auth = b"\xCD" * 256
            _make_pyrogram_sqlite(src, dc_id=5, auth_key=auth)
            dest = os.path.join(td, "out.session")
            bundle = BundleInfo(kind="pyrogram", path=src, suggested_session_name="p")
            with patch.dict(os.environ, {"API_ID": "1", "API_HASH": "hash"}):
                result = asyncio.run(bundle_to_telethon_session(bundle, dest))
            conn = sqlite3.connect(result)
            try:
                row = conn.execute(
                    "SELECT dc_id, auth_key, server_address, port FROM sessions"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], 5)
            self.assertEqual(bytes(row[1]), auth)
            self.assertEqual(row[2], "91.108.56.130")
            self.assertEqual(row[3], 443)

    def test_tdata_calls_opentele_usecurrentsession(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdata = os.path.join(td, "tdata")
            os.makedirs(tdata)
            open(os.path.join(tdata, "key_datas"), "wb").close()
            dest = os.path.join(td, "out.session")

            use_current = object()
            api_desktop = object()
            fake_tdesk = MagicMock()
            fake_tdesk.isLoaded.return_value = True

            async def _to_telethon(*, session, flag, api, password=None, **kwargs):
                self.assertIs(flag, use_current)
                self.assertIs(api, api_desktop)
                base = session if not str(session).endswith(".session") else session[: -len(".session")]
                with open(base + ".session", "wb") as f:
                    f.write(b"ok")
                return MagicMock()

            fake_tdesk.ToTelethon = AsyncMock(side_effect=_to_telethon)

            td_mod = MagicMock()
            td_mod.TDesktop = MagicMock(return_value=fake_tdesk)
            api_mod = MagicMock()
            api_mod.UseCurrentSession = use_current
            api_mod.API = MagicMock(TelegramDesktop=api_desktop)

            with patch.dict(
                sys.modules,
                {
                    "opentele2": MagicMock(),
                    "opentele2.td": td_mod,
                    "opentele2.api": api_mod,
                },
            ):
                from discovery_api.session_archive import _tdata_to_telethon

                asyncio.run(_tdata_to_telethon(tdata, dest))

            self.assertTrue(os.path.isfile(dest))
            fake_tdesk.ToTelethon.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
