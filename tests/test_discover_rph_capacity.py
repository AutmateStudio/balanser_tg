"""Проверка ёмкости telegram_discover: 120 задач/ч на аккаунт при пороге 20%."""
from __future__ import annotations

import math

from app_balance.queue.ops_catalog_verify import effective_rph

# units_per_execution для telegram_discover (A9 task_type_ops)
TELEGRAM_DISCOVER_OPS: tuple[tuple[str, int], ...] = (
    ("contacts.Search", 10),
    ("messages.SearchGlobal", 10),
    ("channels.GetChannelRecommendations", 5),
    ("get_input_entity", 2),
    ("channels.GetFullChannel", 15),
    ("channels.GetParticipants", 10),
    ("iter_messages", 10),
)

DISCOVER_TARGET_PER_HOUR = 120
DISCOVER_THRESHOLD_PERCENT = 20
RESERVE_PERCENT = 10


def min_rph_limit_for_discover(
    units_per_execution: int,
    *,
    target_per_hour: int = DISCOVER_TARGET_PER_HOUR,
    threshold_percent: int = DISCOVER_THRESHOLD_PERCENT,
    reserve_percent: int = RESERVE_PERCENT,
) -> int:
    """Минимальный rph_limit, чтобы вместить target discover/ч при пороге threshold."""
    min_effective = math.ceil(
        target_per_hour * units_per_execution * 100 / (100 - threshold_percent)
    )
    return math.ceil(min_effective / ((100 - reserve_percent) / 100))


def max_discover_tasks_at_threshold(
    rph_limit: int,
    units_per_execution: int,
    *,
    threshold_percent: int = DISCOVER_THRESHOLD_PERCENT,
    reserve_percent: int = RESERVE_PERCENT,
) -> int:
    """Сколько discover можно запустить за час, пока остаток >= threshold%."""
    eff = effective_rph(rph_limit, reserve_percent=reserve_percent)
    max_used = eff * (100 - threshold_percent) / 100
    return int(max_used // units_per_execution)


def test_discover_seed_rph_supports_120_per_hour() -> None:
    """A17: каждый op discover выдерживает ≥120 задач/ч при min_available 20%."""
    seed_limits = {
        "contacts.Search": 1670,
        "messages.SearchGlobal": 1670,
        "channels.GetChannelRecommendations": 840,
        "get_input_entity": 340,
        "channels.GetFullChannel": 2500,
        "channels.GetParticipants": 2500,
        "iter_messages": 2250,
    }
    for op_code, units in TELEGRAM_DISCOVER_OPS:
        rph_limit = seed_limits[op_code]
        capacity = max_discover_tasks_at_threshold(rph_limit, units)
        assert capacity >= DISCOVER_TARGET_PER_HOUR, (
            f"{op_code}: rph_limit={rph_limit} units={units} → {capacity}/ч < "
            f"{DISCOVER_TARGET_PER_HOUR}"
        )


def test_min_rph_limit_formula_matches_a17() -> None:
    assert min_rph_limit_for_discover(15) == 2500  # GetFullChannel — bottleneck
    assert min_rph_limit_for_discover(10) == 1667
    assert min_rph_limit_for_discover(5) == 834
    assert min_rph_limit_for_discover(2) == 334
    # A17 seed округляет вверх для запаса
    assert max_discover_tasks_at_threshold(1670, 10) >= DISCOVER_TARGET_PER_HOUR


def test_120th_discover_passes_20_percent_threshold() -> None:
    """120-я задача ещё проходит порог 20%; 121-я — нет."""
    eff = effective_rph(2500)
    units = 15
    used_after_119 = 119 * units
    avail_pct_before_120 = (eff - used_after_119) / eff * 100
    assert avail_pct_before_120 >= 20

    used_after_120 = 120 * units
    avail_pct_after_120 = (eff - used_after_120) / eff * 100
    assert avail_pct_after_120 >= 20

    used_after_121 = 121 * units
    avail_pct_after_121 = (eff - used_after_121) / eff * 100
    assert avail_pct_after_121 < 20
