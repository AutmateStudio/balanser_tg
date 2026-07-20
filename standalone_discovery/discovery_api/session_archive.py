"""Извлечение и конвертация retriv-style ZIP-бандлов в Telethon `.session`.

HTTP-агностичный слой: безопасная распаковка AES/ZipCrypto ZIP,
детект telethon / pyrogram / tdata, конвертация в Telethon sqlite.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# Лимиты zip-bomb (до распаковки; размеры видны в central directory).
MAX_ARCHIVE_ENTRIES = 5000
MAX_EXTRACTED_BYTES_DEFAULT = 100 * 1024 * 1024  # 100 MiB uncompressed
# Retriv часто кладёт AES-ZIP внутрь обычного ZIP-обёртки (1 уровень).
MAX_NESTED_ZIP_DEPTH = 3

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Production DC endpoints (pyrogram не хранит адрес в sqlite).
_TELETHON_DC_MAP: dict[int, tuple[str, int]] = {
    1: ("149.154.175.53", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),
    5: ("91.108.56.130", 443),
}

_TDATA_KEY_MARKERS = ("key_datas",)
_TDATA_MAP_PREFIX = "D877F783D5D3EF8C"


class ArchiveSessionError(Exception):
    """Ошибка обработки архива сессии с машиночитаемым кодом."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class BundleInfo:
    kind: str  # telethon | pyrogram | tdata
    path: str  # путь к .session или каталогу tdata
    suggested_session_name: Optional[str] = None
    phone: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)


def validate_session_name(name: str) -> str:
    """Проверяет имя сессии тем же regex, что QR/auth."""
    if not name or not _SESSION_NAME_RE.fullmatch(name):
        raise ArchiveSessionError(
            "ambiguous_session_name",
            "session_name должен содержать только латинские буквы, цифры, '_' и '-' (длина 1-64)",
        )
    return name


def _sanitize_member_path(member_name: str, dest_dir: str) -> str:
    """Возвращает абсолютный путь назначения или поднимает unsafe_path."""
    # Нормализуем разделители и отбрасываем абсолютные/traversal пути.
    name = member_name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:/", name):
        raise ArchiveSessionError(
            "unsafe_path",
            f"Небезопасный путь в архиве: {member_name}",
        )
    parts = [p for p in name.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ArchiveSessionError(
            "unsafe_path",
            f"Небезопасный путь в архиве: {member_name}",
        )
    dest_root = os.path.realpath(dest_dir)
    target = os.path.realpath(os.path.join(dest_root, *parts))
    if not (target == dest_root or target.startswith(dest_root + os.sep)):
        raise ArchiveSessionError(
            "unsafe_path",
            f"Небезопасный путь в архиве: {member_name}",
        )
    return target


def _open_zip(data: bytes):
    """AES (pyzipper) с fallback на stdlib zipfile."""
    try:
        import pyzipper

        return pyzipper.AESZipFile(io.BytesIO(data))
    except ImportError:
        return zipfile.ZipFile(io.BytesIO(data))


def safe_extract_zip(
    data: bytes,
    password: str,
    dest_dir: str,
    *,
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES_DEFAULT,
) -> None:
    """Безопасно распаковывает ZIP в dest_dir.

    Проверяет zip-bomb (число записей / суммарный uncompressed size) и zip-slip
    до записи файлов. Неверный пароль → ArchiveSessionError(bad_password).
    """
    os.makedirs(dest_dir, exist_ok=True)
    try:
        zf = _open_zip(data)
    except zipfile.BadZipFile as e:
        raise ArchiveSessionError("no_session_found", f"Некорректный ZIP: {e}") from e

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ArchiveSessionError(
                "archive_too_large",
                f"Слишком много файлов в архиве: {len(infos)} > {MAX_ARCHIVE_ENTRIES}",
            )
        total_uncomp = sum(max(0, int(i.file_size or 0)) for i in infos)
        if total_uncomp > max_extracted_bytes:
            raise ArchiveSessionError(
                "archive_too_large",
                f"Архив слишком большой после распаковки: {total_uncomp} байт",
            )

        # Предварительная валидация путей (до пароля — defense in depth).
        for info in infos:
            if info.is_dir() or info.filename.endswith("/"):
                _sanitize_member_path(info.filename.rstrip("/"), dest_dir)
            else:
                _sanitize_member_path(info.filename, dest_dir)

        pwd = password.encode("utf-8") if password else None
        for info in infos:
            if info.is_dir() or info.filename.endswith("/"):
                target_dir = _sanitize_member_path(info.filename.rstrip("/"), dest_dir)
                os.makedirs(target_dir, exist_ok=True)
                continue
            target = _sanitize_member_path(info.filename, dest_dir)
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                with zf.open(info, pwd=pwd) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            except RuntimeError as e:
                msg = str(e).lower()
                if "password" in msg or "bad password" in msg or "encrypt" in msg:
                    raise ArchiveSessionError(
                        "bad_password",
                        "Неверный пароль архива",
                    ) from e
                raise ArchiveSessionError(
                    "conversion_failed",
                    f"Ошибка распаковки: {e}",
                ) from e
            except Exception as e:
                msg = str(e).lower()
                if "password" in msg or "bad password" in msg:
                    raise ArchiveSessionError(
                        "bad_password",
                        "Неверный пароль архива",
                    ) from e
                # pyzipper иногда кидает NotImplementedError / BadZipFile на неверный ключ
                if "compress" in msg or "decrypt" in msg or "crc" in msg:
                    raise ArchiveSessionError(
                        "bad_password",
                        "Неверный пароль архива",
                    ) from e
                raise
    # Пустой архив после распаковки без ошибок — ок; detect_bundle разберётся.


def _dir_total_bytes(root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def _iter_nested_zip_files(root: str) -> list[str]:
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".zip"):
                found.append(os.path.join(dirpath, name))
    return found


def extract_session_archive(
    data: bytes,
    password: str,
    dest_dir: str,
    *,
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES_DEFAULT,
    max_nested_depth: int = MAX_NESTED_ZIP_DEPTH,
) -> None:
    """Распаковывает ZIP и разворачивает вложенные ZIP (layout retriv).

    Типичный случай: внешний незашифрованный ZIP содержит один AES-ZIP
    с `*_telethon.session` / tdata. Пароль применяется на каждом уровне
    (для незашифрованных членов игнорируется).
    """
    safe_extract_zip(
        data,
        password,
        dest_dir,
        max_extracted_bytes=max_extracted_bytes,
    )
    for _depth in range(max(0, int(max_nested_depth))):
        nested_zips = _iter_nested_zip_files(dest_dir)
        if not nested_zips:
            break
        for zip_path in nested_zips:
            remaining = max_extracted_bytes - _dir_total_bytes(dest_dir)
            # Сам .zip ещё лежит на диске — его размер не должен съедать бюджет
            # распаковки вложенного содержимого целиком, но если remaining уже 0 —
            # дальше некуда.
            try:
                zip_size = os.path.getsize(zip_path)
            except OSError:
                zip_size = 0
            remaining_for_nested = remaining + zip_size
            if remaining_for_nested <= 0:
                raise ArchiveSessionError(
                    "archive_too_large",
                    "Архив слишком большой после распаковки вложенных ZIP",
                )
            pending = zip_path + ".extracting"
            try:
                os.replace(zip_path, pending)
            except OSError as e:
                raise ArchiveSessionError(
                    "conversion_failed",
                    f"Не удалось подготовить вложенный ZIP: {e}",
                ) from e
            try:
                with open(pending, "rb") as f:
                    nested_data = f.read()
                parent = os.path.dirname(pending) or dest_dir
                safe_extract_zip(
                    nested_data,
                    password,
                    parent,
                    max_extracted_bytes=remaining_for_nested,
                )
            finally:
                try:
                    os.remove(pending)
                except FileNotFoundError:
                    pass


def _sqlite_table_columns(path: str, table: str) -> Optional[set[str]]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cur.fetchall()}
        return cols or None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _is_telethon_session(path: str) -> bool:
    cols = _sqlite_table_columns(path, "sessions")
    if not cols:
        return False
    return "server_address" in cols and "auth_key" in cols and "dc_id" in cols


def _is_pyrogram_session(path: str) -> bool:
    cols = _sqlite_table_columns(path, "sessions")
    if not cols:
        return False
    # pyrogram: dc_id, api_id, auth_key, user_id — без server_address
    return (
        "auth_key" in cols
        and "dc_id" in cols
        and "server_address" not in cols
        and ("api_id" in cols or "user_id" in cols)
    )


def _looks_like_tdata_dir(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    names = set(os.listdir(path))
    if any(m in names for m in _TDATA_KEY_MARKERS):
        return True
    return any(n.startswith(_TDATA_MAP_PREFIX) for n in names)


def _find_tdata_roots(root: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        base = os.path.basename(dirpath)
        if base == "tdata" and _looks_like_tdata_dir(dirpath):
            found.append(dirpath)
            dirnames.clear()  # не спускаемся внутрь tdata
            continue
        if _looks_like_tdata_dir(dirpath):
            found.append(dirpath)
            dirnames.clear()
    return found


def _prefix_from_session_filename(filename: str) -> Optional[str]:
    """`247542045_telethon.session` → `247542045`; `acc.session` → `acc`."""
    base = filename
    if base.endswith(".session"):
        base = base[: -len(".session")]
    for suffix in ("_telethon", "_pyrogram"):
        if base.endswith(suffix):
            return base[: -len(suffix)] or None
    return base or None


def _load_sidecar_json(root: str, prefix: str) -> dict[str, Any]:
    candidate = os.path.join(root, f"{prefix}.json")
    if not os.path.isfile(candidate):
        # иногда json лежит рядом с вложенной папкой
        for dirpath, _dns, filenames in os.walk(root):
            if f"{prefix}.json" in filenames:
                candidate = os.path.join(dirpath, f"{prefix}.json")
                break
        else:
            return {}
    try:
        with open(candidate, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def detect_bundle(dest_dir: str) -> BundleInfo:
    """Определяет тип бандла в распакованном каталоге.

    Приоритет: telethon → pyrogram → tdata.
    Несколько независимых id без явного выбора → ambiguous_session_name.
    """
    telethon_files: list[str] = []
    pyrogram_files: list[str] = []

    for dirpath, _dirnames, filenames in os.walk(dest_dir):
        for name in filenames:
            if not name.endswith(".session"):
                continue
            full = os.path.join(dirpath, name)
            if _is_telethon_session(full):
                telethon_files.append(full)
            elif _is_pyrogram_session(full):
                pyrogram_files.append(full)

    tdata_roots = _find_tdata_roots(dest_dir)

    prefixes: set[str] = set()
    for path in telethon_files + pyrogram_files:
        p = _prefix_from_session_filename(os.path.basename(path))
        if p:
            prefixes.add(p)
    for tdata in tdata_roots:
        # типичный layout retriv: `<id>/tdata/...`
        parent = os.path.basename(os.path.dirname(tdata))
        if os.path.basename(tdata) == "tdata" and parent and parent != os.path.basename(
            dest_dir.rstrip("/\\")
        ):
            prefixes.add(parent)

    if len(prefixes) > 1:
        raise ArchiveSessionError(
            "ambiguous_session_name",
            "В архиве несколько аккаунтов; укажите session_name явно: "
            + ", ".join(sorted(prefixes)),
        )

    suggested = next(iter(prefixes), None)
    meta: dict[str, Any] = {}
    phone: Optional[str] = None
    if suggested:
        meta = _load_sidecar_json(dest_dir, suggested)
        phone_val = meta.get("phone")
        if phone_val is not None:
            phone = str(phone_val)

    if telethon_files:
        return BundleInfo(
            kind="telethon",
            path=telethon_files[0],
            suggested_session_name=suggested,
            phone=phone,
            metadata=meta,
            candidate_ids=sorted(prefixes),
        )
    if pyrogram_files:
        return BundleInfo(
            kind="pyrogram",
            path=pyrogram_files[0],
            suggested_session_name=suggested,
            phone=phone,
            metadata=meta,
            candidate_ids=sorted(prefixes),
        )
    if tdata_roots:
        return BundleInfo(
            kind="tdata",
            path=tdata_roots[0],
            suggested_session_name=suggested,
            phone=phone,
            metadata=meta,
            candidate_ids=sorted(prefixes),
        )

    raise ArchiveSessionError(
        "no_session_found",
        "В архиве не найдена Telethon/Pyrogram сессия или tdata",
    )


def _copy_telethon_session(src: str, target_path: str) -> None:
    """Копирует Telethon sqlite в target_path (с суффиксом .session если нужно)."""
    dest = target_path if target_path.endswith(".session") else target_path + ".session"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if os.path.abspath(src) != os.path.abspath(dest):
        shutil.copy2(src, dest)


def _pyrogram_to_telethon(src: str, target_path: str) -> None:
    """Читает dc_id+auth_key из pyrogram sqlite и пишет Telethon session."""
    from telethon import TelegramClient
    from telethon.crypto import AuthKey

    from discovery_api.config import get_api_hash, get_api_id

    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT dc_id, auth_key FROM sessions ORDER BY dc_id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row or row[1] is None:
        raise ArchiveSessionError(
            "conversion_failed",
            "В Pyrogram-сессии нет dc_id/auth_key",
        )
    dc_id = int(row[0])
    auth_key = bytes(row[1])
    if dc_id not in _TELETHON_DC_MAP:
        raise ArchiveSessionError(
            "conversion_failed",
            f"Неизвестный dc_id={dc_id} в Pyrogram-сессии",
        )
    server, port = _TELETHON_DC_MAP[dc_id]

    base = target_path[: -len(".session")] if target_path.endswith(".session") else target_path
    sqlite_path = base + ".session"
    os.makedirs(os.path.dirname(sqlite_path) or ".", exist_ok=True)
    try:
        os.remove(sqlite_path)
    except FileNotFoundError:
        pass

    client = TelegramClient(base, get_api_id(), get_api_hash())
    try:
        client.session.set_dc(dc_id, server, port)
        client.session.auth_key = AuthKey(data=auth_key)
        client.session.save()
    finally:
        try:
            client.session.close()
        except Exception:
            pass


async def _tdata_to_telethon(tdata_dir: str, target_path: str) -> None:
    """Конвертирует tdata → Telethon через opentele2 (UseCurrentSession)."""
    try:
        from opentele2.api import API, UseCurrentSession
        from opentele2.td import TDesktop
    except ImportError as e:
        raise ArchiveSessionError(
            "conversion_failed",
            f"opentele2 не установлен: {e}",
        ) from e

    base = target_path[: -len(".session")] if target_path.endswith(".session") else target_path
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    sqlite_path = base + ".session"
    try:
        os.remove(sqlite_path)
    except FileNotFoundError:
        pass

    try:
        tdesk = TDesktop(tdata_dir)
        if not tdesk.isLoaded():
            raise ArchiveSessionError(
                "conversion_failed",
                "Не удалось загрузить tdata",
            )
        await tdesk.ToTelethon(
            session=base,
            flag=UseCurrentSession,
            api=API.TelegramDesktop,
        )
    except ArchiveSessionError:
        raise
    except Exception as e:
        raise ArchiveSessionError(
            "conversion_failed",
            f"Конвертация tdata не удалась: {e}",
        ) from e

    if not os.path.isfile(sqlite_path):
        raise ArchiveSessionError(
            "conversion_failed",
            "opentele2 не создал файл Telethon-сессии",
        )


async def bundle_to_telethon_session(bundle: BundleInfo, target_path: str) -> str:
    """Конвертирует бандл в Telethon `.session` по пути target_path.

    Возвращает абсолютный путь к созданному `.session`-файлу.
    """
    dest = target_path if target_path.endswith(".session") else target_path + ".session"
    if bundle.kind == "telethon":
        _copy_telethon_session(bundle.path, dest)
    elif bundle.kind == "pyrogram":
        _pyrogram_to_telethon(bundle.path, dest)
    elif bundle.kind == "tdata":
        await _tdata_to_telethon(bundle.path, dest)
    else:
        raise ArchiveSessionError(
            "conversion_failed",
            f"Неизвестный тип бандла: {bundle.kind}",
        )
    if not os.path.isfile(dest):
        raise ArchiveSessionError(
            "conversion_failed",
            "Файл сессии не был создан после конвертации",
        )
    return os.path.abspath(dest)


async def probe_session_authorized(session_sqlite_path: str) -> None:
    """Кратко подключается к Telegram и проверяет авторизацию сессии.

    При unauthorized/banned/flood поднимает ArchiveSessionError(conversion_failed)
    с деталями; вызывающий слой мапит на HTTP 409.
    """
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError

    from discovery_api.config import get_api_hash, get_api_id
    from discovery_api.session_health import classify_telethon_error

    base = (
        session_sqlite_path[: -len(".session")]
        if session_sqlite_path.endswith(".session")
        else session_sqlite_path
    )
    client = TelegramClient(base, get_api_id(), get_api_hash())
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ArchiveSessionError(
                "auth_failed",
                "Сессия не авторизована в Telegram",
            )
    except ArchiveSessionError:
        raise
    except FloodWaitError as e:
        raise ArchiveSessionError(
            "auth_failed",
            f"FloodWait: {getattr(e, 'seconds', '?')}с",
        ) from e
    except Exception as e:
        kind, seconds = classify_telethon_error(e)
        if kind in ("unauthorized", "banned", "flood"):
            detail = {
                "unauthorized": "Сессия не авторизована в Telegram",
                "banned": f"Аккаунт заблокирован: {e}",
                "flood": f"FloodWait: {seconds}с",
            }[kind]
            raise ArchiveSessionError("auth_failed", detail) from e
        raise ArchiveSessionError(
            "conversion_failed",
            f"Проверка сессии не удалась: {e}",
        ) from e
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        try:
            client.session.close()
        except Exception:
            pass
