"""PG queue producers и read API для discovery API (D8+)."""

from discovery_api.queue.producer import (
    enqueue_parser_add_channels,
    enqueue_parser_remove_channels,
    enqueue_telegram_discover,
)
from discovery_api.queue.account_channels import (
    get_account_channels_pg,
    get_account_channels_summary,
)
from discovery_api.queue.metrics import get_queue_metrics
from discovery_api.queue.task_types import (
    get_task_type,
    list_task_types,
    patch_task_type,
)
from discovery_api.queue.status import get_task_snapshot

__all__ = [
    "enqueue_telegram_discover",
    "enqueue_parser_add_channels",
    "enqueue_parser_remove_channels",
    "get_account_channels_pg",
    "get_account_channels_summary",
    "get_queue_metrics",
    "get_task_snapshot",
    "get_task_type",
    "list_task_types",
    "patch_task_type",
]
