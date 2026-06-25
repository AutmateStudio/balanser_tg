"""G6 — детектор повторяющихся ошибок per-op и auto-RPH (ТЗ §26, §0.5)."""
from __future__ import annotations

import logging
import math
from typing import Literal

from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.monitoring.alert_rules import Alert
from app_balance.queue.monitoring.config import AlertConfig, ErrorDetectorConfig
from app_balance.queue.monitoring.error_detector_repo import (
    AdjustmentPlan,
    ErrorDetectorRepo,
    RecurringErrorRow,
    Severity,
)
from app_balance.queue.monitoring.notify import AlertNotifier, send_telegram_dev

logger = logging.getLogger(__name__)

_TARGET_ERROR_CODES = frozenset(
    {
        ErrorCode.FLOOD_WAIT,
        ErrorCode.PEER_FLOOD,
    }
)


def _scope_key(plan: AdjustmentPlan) -> str:
    return f"{plan.error_code}:{plan.op_code}"


def _notify_message(plan: AdjustmentPlan) -> str:
    if plan.action == "disable_op":
        return (
            f"G6: {plan.error_code} ×{plan.error_count} на op {plan.op_code} "
            f"→ op отключён (is_enabled=false)"
        )
    if plan.new_rph_limit is not None:
        suffix = " + cooldown аккаунта" if plan.apply_cooldown else ""
        return (
            f"G6: {plan.error_code} ×{plan.error_count} на op {plan.op_code} "
            f"→ RPH {plan.old_rph_limit}→{plan.new_rph_limit}{suffix}"
        )
    return (
        f"G6: {plan.error_code} ×{plan.error_count} на op {plan.op_code} "
        f"→ коррекция RPH"
    )


def calc_reduced_rph(old_rph: int, *, factor: float, min_rph: int) -> int:
    return max(math.floor(old_rph * factor), min_rph)


def evaluate_adjustments(
    rows: list[RecurringErrorRow],
    *,
    config: ErrorDetectorConfig,
    adjustment_counts_24h: dict[tuple[str, str], int],
    debounced_pairs: frozenset[tuple[str, str]],
) -> list[AdjustmentPlan]:
    """Pure rules: flood_wait / peer_flood → reduce или disable при повторе."""
    plans: list[AdjustmentPlan] = []
    for row in rows:
        key = (row.error_code, row.op_code)
        if row.error_code not in _TARGET_ERROR_CODES:
            continue
        if key in debounced_pairs:
            continue

        prior = adjustment_counts_24h.get(key, 0)
        if prior >= 1:
            plans.append(
                AdjustmentPlan(
                    error_code=row.error_code,
                    op_code=row.op_code,
                    op_type_id=row.op_type_id,
                    action="disable_op",
                    old_rph_limit=row.current_rph_limit,
                    new_rph_limit=None,
                    error_count=row.error_count,
                    account_id=row.last_account_id,
                    apply_cooldown=False,
                    severity="CRITICAL",
                )
            )
            continue

        new_rph = calc_reduced_rph(
            row.current_rph_limit,
            factor=config.rph_factor,
            min_rph=config.min_rph,
        )
        apply_cooldown = row.error_code == ErrorCode.PEER_FLOOD
        plans.append(
            AdjustmentPlan(
                error_code=row.error_code,
                op_code=row.op_code,
                op_type_id=row.op_type_id,
                action="reduce_rph",
                old_rph_limit=row.current_rph_limit,
                new_rph_limit=new_rph,
                error_count=row.error_count,
                account_id=row.last_account_id,
                apply_cooldown=apply_cooldown,
                severity="WARNING",
            )
        )
    return plans


async def run_detector_tick(
    repo: ErrorDetectorRepo,
    config: ErrorDetectorConfig,
    notifier: AlertNotifier | None = None,
    alert_config: AlertConfig | None = None,
) -> int:
    """Один tick G6: паттерны → коррекция → notify. Возвращает число применённых action."""
    if not config.enabled:
        return 0

    rows = await repo.fetch_recurring_errors(config)
    if not rows:
        logger.debug("detector: повторяющихся ошибок нет")
        return 0

    adjustment_counts: dict[tuple[str, str], int] = {}
    debounced: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.error_code, row.op_code)
        adjustment_counts[key] = await repo.count_adjustments(
            row.error_code,
            row.op_code,
            config.repeat_window_seconds,
        )
        if await repo.has_adjustment_in_window(
            row.error_code, row.op_code, config.window_seconds
        ):
            debounced.add(key)

    plans = evaluate_adjustments(
        rows,
        config=config,
        adjustment_counts_24h=adjustment_counts,
        debounced_pairs=frozenset(debounced),
    )

    applied = 0
    for plan in plans:
        await repo.apply_plan(plan, config)
        applied += 1
        logger.warning(
            "detector: %s",
            _notify_message(plan),
            extra={
                "error_code": plan.error_code,
                "op_code": plan.op_code,
                "severity": plan.severity,
            },
        )
        await _notify_plan(plan, config, notifier, alert_config)

    if plans:
        logger.info(
            "detector: tick — паттернов=%d применено=%d debounce=%d",
            len(rows),
            applied,
            len(debounced),
        )
    return applied


async def _notify_plan(
    plan: AdjustmentPlan,
    config: ErrorDetectorConfig,
    notifier: AlertNotifier | None,
    alert_config: AlertConfig | None,
) -> None:
    message = _notify_message(plan)
    await send_telegram_dev(
        message,
        chat_id=config.telegram_chat_id,
        bot_token=config.bot_token,
    )

    if notifier is None or alert_config is None:
        return

    alert = Alert(
        code="error_detector_rph" if plan.action == "reduce_rph" else "error_detector_disable_op",
        severity=plan.severity,
        message=message,
        scope_key=_scope_key(plan),
        metrics_snapshot={
            "error_code": plan.error_code,
            "op_code": plan.op_code,
            "error_count": plan.error_count,
            "old_rph_limit": plan.old_rph_limit,
            "new_rph_limit": plan.new_rph_limit,
            "action": plan.action,
        },
    )
    await notifier.emit(alert)
