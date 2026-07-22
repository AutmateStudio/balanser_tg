"""Lead Intent Discovery — изолированный модуль поиска лидов по интентам.

Не изменяет логику POST /discover и score_channel/lidgen_scorer.
"""
from __future__ import annotations

from discovery_api.lead_intent.keywords import PIPELINE_VERSION
from discovery_api.lead_intent.seeds import generate_intent_seeds
from discovery_api.lead_intent.scorer import ScoreResult, score_messages

__all__ = [
    "PIPELINE_VERSION",
    "generate_intent_seeds",
    "score_messages",
    "ScoreResult",
]
