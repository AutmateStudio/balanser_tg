#!/usr/bin/env python3
"""
Проверка GET /health по разным вариантам URL (hostname, IP, порты, http/https).

Запуск из каталога standalone_discovery:
  python check_health_urls.py
  python check_health_urls.py --host vps-108.web.oboyma.ai --ip 213.219.248.2
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


DEFAULT_HOST = "vps-108.web.oboyma.ai"
DEFAULT_IP = "213.219.248.2"
HEALTH_PATH = "/health"
TIMEOUT_SEC = 5


@dataclass
class ProbeResult:
    url: str
    ok: bool
    status: Optional[int]
    detail: str
    elapsed_ms: float
    body_preview: str = ""


def _build_urls(host: str, ip: str) -> list[str]:
    """Матрица типичных вариантов обращения к API."""
    bases: list[tuple[str, str]] = [
        ("http", host),
        ("https", host),
        ("http", ip),
        ("https", ip),
    ]
    # None = порт по умолчанию для схемы; явные 80/443/8100
    ports: list[Optional[int]] = [None, 80, 443, 8100]

    urls: list[str] = []
    seen: set[str] = set()
    for scheme, authority in bases:
        for port in ports:
            if port is None:
                netloc = authority
            else:
                default = 80 if scheme == "http" else 443
                if port == default:
                    netloc = authority
                else:
                    netloc = f"{authority}:{port}"
            url = f"{scheme}://{netloc}{HEALTH_PATH}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _resolve_host(host: str) -> str:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        addrs = sorted({info[4][0] for info in infos})
        return ", ".join(addrs) if addrs else "не удалось разрешить"
    except socket.gaierror as e:
        return f"ошибка DNS: {e}"


def _health_body_ok(status: int, body: str) -> bool:
    if status != 200:
        return False
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return "в порядке" in body or "ok" in body.lower()
    status_val = data.get("status")
    if isinstance(status_val, str):
        return status_val.lower() in ("в порядке", "ok", "healthy")
    return bool(data)


def _probe(url: str, timeout: float) -> ProbeResult:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "standalone_discovery/check_health_urls"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.perf_counter() - started) * 1000
            status = int(getattr(resp, "status", 200))
            final_url = resp.geturl()
            body = resp.read(4096).decode("utf-8", errors="replace")
            ok = _health_body_ok(status, body)
            detail = f"HTTP {status}"
            if final_url != url:
                detail += f", после редиректа -> {final_url}"
            if not ok and status == 200:
                detail += " (тело не похоже на /health API)"
            return ProbeResult(
                url=url,
                ok=ok,
                status=status,
                detail=detail,
                elapsed_ms=elapsed_ms,
                body_preview=body[:200].replace("\n", " "),
            )
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        body = ""
        try:
            body = e.read(4096).decode("utf-8", errors="replace")
        except Exception:
            pass
        ok = _health_body_ok(int(e.code), body)
        return ProbeResult(
            url=url,
            ok=ok,
            status=int(e.code),
            detail=f"HTTP {e.code}: {e.reason}",
            elapsed_ms=elapsed_ms,
            body_preview=body[:200].replace("\n", " "),
        )
    except urllib.error.URLError as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        reason = e.reason
        if isinstance(reason, ssl.SSLError):
            detail = f"SSL: {reason}"
        elif isinstance(reason, TimeoutError):
            detail = "таймаут"
        elif isinstance(reason, OSError):
            detail = str(reason) or repr(reason)
        else:
            detail = str(e)
        return ProbeResult(
            url=url,
            ok=False,
            status=None,
            detail=detail,
            elapsed_ms=elapsed_ms,
        )
    except TimeoutError:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return ProbeResult(
            url=url,
            ok=False,
            status=None,
            detail="таймаут",
            elapsed_ms=elapsed_ms,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка /health по разным URL")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Hostname сервера")
    parser.add_argument("--ip", default=DEFAULT_IP, help="IP сервера (для сравнения)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SEC)
    args = parser.parse_args()

    host: str = args.host.strip()
    ip: str = args.ip.strip()

    print(f"Hostname: {host}")
    print(f"DNS A/AAAA: {_resolve_host(host)}")
    print(f"IP для проверки: {ip}")
    print(f"Таймаут: {args.timeout}s")
    print("-" * 72)

    urls = _build_urls(host, ip)
    results: list[ProbeResult] = []

    for url in urls:
        r = _probe(url, args.timeout)
        results.append(r)
        mark = "OK" if r.ok else "—"
        status_part = str(r.status) if r.status is not None else "—"
        print(f"[{mark:2}] {url}")
        print(f"     {r.detail} | {r.elapsed_ms:.0f} ms | HTTP {status_part}")
        if r.body_preview:
            print(f"     тело: {r.body_preview!r}")

    working = [r for r in results if r.ok]
    print("-" * 72)
    if working:
        print("Рабочие варианты (рекомендуемый DISCOVERY_BASE_URL — без /health):")
        for r in working:
            base = urlparse(r.url)._replace(path="", params="", query="", fragment="").geturl()
            print(f"  • {base}")
        fastest = min(working, key=lambda x: x.elapsed_ms)
        best_base = urlparse(fastest.url)._replace(
            path="", params="", query="", fragment=""
        ).geturl()
        print()
        print(f"Самый быстрый из успешных: {best_base} ({fastest.elapsed_ms:.0f} ms)")
    else:
        print("Ни один вариант не вернул ожидаемый /health (HTTP 200 + status «в порядке»).")
        print("Проверьте, что контейнер запущен и порт 8100 открыт с вашей сети.")

    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
