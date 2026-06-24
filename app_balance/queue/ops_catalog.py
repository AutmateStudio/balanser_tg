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


RESOURCE_OPS: dict[str, ResourceOpDefinition] = {
    "auth.qr_login": ResourceOpDefinition(
        code="auth.qr_login",
        name="QR: qr_login + wait + recreate + get_me + save",
        rph_limit=3,
    ),
    "connect_disconnect": ResourceOpDefinition(
        code="connect_disconnect",
        name="Connect / disconnect сессии",
        rph_limit=1,
    ),
    "get_me": ResourceOpDefinition(
        code="get_me",
        name="Текущий пользователь (валидация сессии)",
        rph_limit=1,
    ),
    "is_user_authorized": ResourceOpDefinition(
        code="is_user_authorized",
        name="Проверка авторизации",
        rph_limit=1,
    ),
    "get_entity": ResourceOpDefinition(
        code="get_entity",
        name="Resolve username / ссылки / peer",
        rph_limit=7,
    ),
    "get_input_entity": ResourceOpDefinition(
        code="get_input_entity",
        name="get_input_entity() для InputPeer",
        rph_limit=7,
    ),
    "contacts.Search": ResourceOpDefinition(
        code="contacts.Search",
        name="Поиск контактов / каналов",
        rph_limit=2,
    ),
    "messages.SearchGlobal": ResourceOpDefinition(
        code="messages.SearchGlobal",
        name="Глобальный поиск сообщений",
        rph_limit=120,
    ),
    "channels.GetChannelRecommendations": ResourceOpDefinition(
        code="channels.GetChannelRecommendations",
        name="Рекомендации каналов",
        rph_limit=30,
    ),
    "channels.GetFullChannel": ResourceOpDefinition(
        code="channels.GetFullChannel",
        name="Полные данные канала",
        rph_limit=80,
    ),
    "channels.JoinChannel": ResourceOpDefinition(
        code="channels.JoinChannel",
        name="Подписка / join канала или discussion",
        rph_limit=30,
    ),
    "channels.LeaveChannel": ResourceOpDefinition(
        code="channels.LeaveChannel",
        name="Выход из канала или discussion",
        rph_limit=30,
    ),
    "channels.GetParticipant": ResourceOpDefinition(
        code="channels.GetParticipant",
        name="Проверка участника (InputPeerSelf)",
        rph_limit=6000,
    ),
    "channels.GetParticipants": ResourceOpDefinition(
        code="channels.GetParticipants",
        name="Список участников (megagroup / lidgen)",
        rph_limit=500,
    ),
    "get_permissions": ResourceOpDefinition(
        code="get_permissions",
        name="get_permissions() для legacy Chat",
        rph_limit=30,
    ),
    "iter_messages": ResourceOpDefinition(
        code="iter_messages",
        name="Итерация сообщений (скоринг / collect)",
        rph_limit=450,
    ),
    "users.GetFullUser": ResourceOpDefinition(
        code="users.GetFullUser",
        name="Полные данные пользователя (NewMessage sender)",
        rph_limit=1500,
    ),
    "bot.send_message": ResourceOpDefinition(
        code="bot.send_message",
        name="Bot API: send_message",
        rph_limit=1000,
    ),
    "bot.send_photo": ResourceOpDefinition(
        code="bot.send_photo",
        name="Bot API: send_photo",
        rph_limit=500,
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
