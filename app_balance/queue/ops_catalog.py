"""E7 — канонический каталог op-кодов и per-op пайплайнов."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskOpAccountRole = Literal["primary", "source", "target"]


@dataclass(frozen=True, slots=True)
class ResourceOpDefinition:
    code: str
    name: str
    rph_limit: int
    is_enabled: bool = True


@dataclass(frozen=True, slots=True)
class TaskOpDefinition:
    op_code: str
    units_per_execution: int = 1
    account_role: TaskOpAccountRole = "primary"


PARSER_ADD_CHANNEL = "parser_add_channel"
MOVE_CHANNEL = "move_channel"
COLLECT_EXTRA_DATA = "collect_extra_data"
UPDATE_CHANNEL = "update_channel"
PARSER_REMOVE_CHANNEL = "parser_remove_channel"

# F6/F7: типы задач, чья adapter-ветка сама ведёт пошаговый учёт ресурса через
# execute_multi_op_pipeline (record_op per op). Для них dispatch НЕ вызывает
# record_for_task, иначе ресурс спишется дважды. Остальные типы — single-call,
# списываются разом до RPC (record_for_task, инвариант D5 §7.3).
MULTI_OP_TASK_TYPES = frozenset({COLLECT_EXTRA_DATA, UPDATE_CHANNEL})


RESOURCE_OPS: dict[str, ResourceOpDefinition] = {
    "auth.qr_login": ResourceOpDefinition(
        code="auth.qr_login",
        name="QR: qr_login + wait + recreate + get_me + save",
        rph_limit=15,
    ),
    "connect_disconnect": ResourceOpDefinition(
        code="connect_disconnect",
        name="Connect / disconnect сессии",
        rph_limit=150,
    ),
    "get_me": ResourceOpDefinition(
        code="get_me",
        name="Текущий пользователь (валидация сессии)",
        rph_limit=150,
    ),
    "is_user_authorized": ResourceOpDefinition(
        code="is_user_authorized",
        name="Проверка авторизации",
        rph_limit=150,
    ),
    "get_entity": ResourceOpDefinition(
        code="get_entity",
        name="Resolve username / ссылки / peer",
        # 20 кан/ч: 2 units/канал, threshold 80% → effective_rph ≥ 200 → rph_limit 223
        rph_limit=223,
    ),
    "get_input_entity": ResourceOpDefinition(
        code="get_input_entity",
        name="get_input_entity() для InputPeer",
        rph_limit=35,
    ),
    "contacts.Search": ResourceOpDefinition(
        code="contacts.Search",
        name="Поиск контактов / каналов",
        rph_limit=10,
    ),
    "messages.SearchGlobal": ResourceOpDefinition(
        code="messages.SearchGlobal",
        name="Глобальный поиск сообщений",
        rph_limit=600,
    ),
    "channels.GetChannelRecommendations": ResourceOpDefinition(
        code="channels.GetChannelRecommendations",
        name="Рекомендации каналов",
        rph_limit=150,
    ),
    "channels.GetFullChannel": ResourceOpDefinition(
        code="channels.GetFullChannel",
        name="Полные данные канала",
        # 1 unit/канал, threshold 80% → effective_rph ≥ 100 → rph_limit 112
        rph_limit=112,
    ),
    "channels.JoinChannel": ResourceOpDefinition(
        code="channels.JoinChannel",
        name="Подписка / join канала или discussion",
        rph_limit=223,
    ),
    "channels.LeaveChannel": ResourceOpDefinition(
        code="channels.LeaveChannel",
        name="Выход из канала или discussion",
        rph_limit=150,
    ),
    "channels.GetParticipant": ResourceOpDefinition(
        code="channels.GetParticipant",
        name="Проверка участника (InputPeerSelf)",
        rph_limit=30000,
    ),
    "channels.GetParticipants": ResourceOpDefinition(
        code="channels.GetParticipants",
        name="Список участников (megagroup / lidgen)",
        rph_limit=2500,
    ),
    "get_permissions": ResourceOpDefinition(
        code="get_permissions",
        name="get_permissions() для legacy Chat",
        rph_limit=150,
    ),
    "iter_messages": ResourceOpDefinition(
        code="iter_messages",
        name="Итерация сообщений (скоринг / collect)",
        rph_limit=2250,
    ),
    "users.GetFullUser": ResourceOpDefinition(
        code="users.GetFullUser",
        name="Полные данные пользователя (NewMessage sender)",
        rph_limit=7500,
    ),
    "bot.send_message": ResourceOpDefinition(
        code="bot.send_message",
        name="Bot API: send_message",
        rph_limit=5000,
    ),
    "bot.send_photo": ResourceOpDefinition(
        code="bot.send_photo",
        name="Bot API: send_photo",
        rph_limit=2500,
    ),
}


TASK_TYPE_OPS: dict[str, tuple[TaskOpDefinition, ...]] = {
    PARSER_ADD_CHANNEL: (
        TaskOpDefinition(op_code="get_entity", units_per_execution=2),
        TaskOpDefinition(op_code="channels.JoinChannel", units_per_execution=2),
        TaskOpDefinition(op_code="channels.GetFullChannel", units_per_execution=1),
        TaskOpDefinition(op_code="channels.GetParticipant", units_per_execution=1),
    ),
    MOVE_CHANNEL: (
        TaskOpDefinition(
            op_code="channels.GetParticipant",
            units_per_execution=1,
            account_role="source",
        ),
        TaskOpDefinition(
            op_code="get_entity",
            units_per_execution=2,
            account_role="target",
        ),
        TaskOpDefinition(
            op_code="channels.JoinChannel",
            units_per_execution=2,
            account_role="target",
        ),
        TaskOpDefinition(
            op_code="channels.GetFullChannel",
            units_per_execution=1,
            account_role="target",
        ),
        TaskOpDefinition(
            op_code="channels.GetParticipant",
            units_per_execution=1,
            account_role="target",
        ),
    ),
    COLLECT_EXTRA_DATA: (
        TaskOpDefinition(op_code="get_entity", units_per_execution=2),
        TaskOpDefinition(op_code="channels.JoinChannel", units_per_execution=2),
        TaskOpDefinition(op_code="channels.GetFullChannel", units_per_execution=1),
        TaskOpDefinition(op_code="iter_messages", units_per_execution=1),
        TaskOpDefinition(op_code="channels.GetParticipants", units_per_execution=1),
        TaskOpDefinition(op_code="channels.LeaveChannel", units_per_execution=2),
    ),
    UPDATE_CHANNEL: (
        TaskOpDefinition(op_code="get_entity", units_per_execution=2),
        TaskOpDefinition(op_code="channels.JoinChannel", units_per_execution=2),
        TaskOpDefinition(op_code="channels.GetFullChannel", units_per_execution=1),
        TaskOpDefinition(op_code="iter_messages", units_per_execution=1),
        TaskOpDefinition(op_code="channels.GetParticipants", units_per_execution=1),
        TaskOpDefinition(op_code="channels.LeaveChannel", units_per_execution=2),
    ),
    PARSER_REMOVE_CHANNEL: (
        TaskOpDefinition(op_code="get_entity", units_per_execution=2),
        TaskOpDefinition(op_code="channels.GetFullChannel", units_per_execution=1),
        TaskOpDefinition(op_code="channels.LeaveChannel", units_per_execution=2),
    ),
}
