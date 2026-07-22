"""Словари интентов, спама и премиум-сигналов для lead-intent discovery."""
from __future__ import annotations

from typing import FrozenSet, Sequence

# Маркеры заказов / намерений заказчика в тексте постов.
LEAD_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "ищу",
        "нужен",
        "нужна",
        "нужно",
        "закажу",
        "заказ",
        "требуется",
        "задача",
        "бюджет",
        "оплата",
        "портфолио",
        "сроки",
        "срок",
        "фриланс",
        "фрилансер",
        "вакансия",
        "удаленка",
        "сдельная",
        "проектная",
        "usdt",
        "в рублях",
        "пишите в лс",
        "жду откликов",
        "портфолио в личку",
        "в личку",
        "отклик",
    }
)

# Комьюнити / обучение — шум для поиска лидов.
SPAM_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "курсы",
        "обучение",
        "инсайты",
        "вдохновение",
        "референс",
        "референсы",
        "оцените работу",
        "оцените",
        "разбор работ",
        "портфолио дня",
        "челлендж",
    }
)

JOB_BOARD_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "вакансия",
        "заказ",
        "фриланс",
        "ищу исполнителя",
        "нужен исполнитель",
        "тендер",
        "бриф",
    }
)

PREMIUM_URL_MARKERS: FrozenSet[str] = frozenset(
    {
        "behance.net",
        "behance.com",
        "dribbble.com",
        "figma.com",
        "www.figma.com",
    }
)

TZ_FILE_EXTENSIONS: FrozenSet[str] = frozenset({".fig", ".pdf", ".docx", ".doc"})

# Шаблоны intent-сидов; {q} = ниша из query.
INTENT_TEMPLATES: Sequence[str] = (
    "ищу {q}",
    "нужен {q}",
    "закажу {q}",
    "требуется {q}",
    "фрилансер {q}",
    "сделать {q}",
    "бюджет {q}",
    "оплата {q}",
    "{q} usdt",
    "{q} удаленка",
    "проектная работа {q}",
    "задача: {q}",
)

# Маркеры без ниши (общие для любой ниши).
MARKER_SEEDS: Sequence[str] = (
    "портфолио в личку",
    "жду откликов",
    "пишите в лс",
)

# Group-pass суффиксы (как у /discover, плюс вакансия/фриланс/заказ).
GROUP_SUFFIXES: Sequence[str] = (
    "чат",
    "группа",
    "вакансия",
    "фриланс",
    "заказ",
    "community",
    "group",
)

# Доп. сиды для design-like ниш.
DESIGN_EXTRA_SEEDS: Sequence[str] = (
    "сверстать",
    "отрисовать",
    "нужен логотип",
    "требуется ux/ui",
    "фрилансер дизайнер",
    "сделать баннеры",
)

DESIGN_NICHE_TOKENS: FrozenSet[str] = frozenset(
    {
        "дизайн",
        "дизайнер",
        "design",
        "designer",
        "логотип",
        "баннер",
        "ui",
        "ux",
        "ux/ui",
        "figma",
    }
)

# Веса скоринга (псевдокод из ТЗ).
SCORE_LEAD_HIT = 10
SCORE_SPAM_HIT = -5
SCORE_TZ_FILE = 15
SCORE_PREMIUM_URL = 12
SCORE_COMMUNITY_PENALTY = -25
SCORE_MAX = 100
SCORE_MIN = 0

PIPELINE_VERSION = "lead_intent_v1"
