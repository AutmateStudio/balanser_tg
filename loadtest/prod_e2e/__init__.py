"""Пакет loadtest.prod_e2e — 2-часовой E2E нагрузочный тест."""

__all__ = ["main"]


def main() -> None:
    from .main import main as _main

    _main()
