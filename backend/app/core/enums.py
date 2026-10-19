"""Shared domain enums, dimension vocabulary, and scoring thresholds.

Kept in ``core`` (not ``models``) so that models, schemas and agents can all
import them without creating a dependency cycle.

Dimension vocabulary (aligned with the frontend's four-dimension report model):

    code  key        label           engine role
    ------------------------------------------------------------------
    T     account    账号可信度      account
    R     image      图片真实性      image
    U     comment    评论区真实性    comment
    S     text       文案真实性      text

``key`` is the stable internal identifier (it equals the engine's role name).
``code`` + ``label`` are the presentation vocabulary the frontend renders.
"""

from __future__ import annotations

from enum import Enum

# Risk bands. Calibrated against the product spec examples:
#   text score 80  -> high
#   report score 65 -> medium
LOW_RISK_MAX = 35
MEDIUM_RISK_MAX = 70


class RiskLevel(str, Enum):
    """Risk severity. Note: a *higher* score always means *more* risk."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def from_score(cls, score: float) -> "RiskLevel":
        if score < LOW_RISK_MAX:
            return cls.LOW
        if score < MEDIUM_RISK_MAX:
            return cls.MEDIUM
        return cls.HIGH


class TrustLevel(str, Enum):
    """Credibility band — the *inverse* of the risk band.

    Internally everything stays a risk score (higher = riskier); this enum only
    exists for the presentation side, where the frontend shows a credibility
    score ("Trust Score / 100") that is higher-is-better.
    """

    HIGH = "high"      # 高可信
    MEDIUM = "medium"  # 存疑
    LOW = "low"        # 低可信


TRUST_LABELS: dict[TrustLevel, str] = {
    TrustLevel.HIGH: "高可信",
    TrustLevel.MEDIUM: "存疑",
    TrustLevel.LOW: "低可信",
}

_RISK_TO_TRUST: dict[RiskLevel, TrustLevel] = {
    RiskLevel.LOW: TrustLevel.HIGH,
    RiskLevel.MEDIUM: TrustLevel.MEDIUM,
    RiskLevel.HIGH: TrustLevel.LOW,
}


def trust_level_for(risk_level: RiskLevel) -> TrustLevel:
    """Invert a risk band into a credibility band (same thresholds)."""
    return _RISK_TO_TRUST[risk_level]


def trust_score_for(risk_score: float) -> float:
    """Convert a 0-100 risk score into a 0-100 credibility score."""
    return round(max(0.0, min(100.0, 100.0 - risk_score)), 1)


class TaskStatus(str, Enum):
    """Lifecycle of an :class:`AnalysisTask`."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED)


class AgentName(str, Enum):
    """Canonical agent identifiers. Used as DB values and API keys."""

    TEXT = "text"
    COMMENT = "comment"
    # Public key is "image" (not "vision") so it matches the engine's role name
    # and the frontend's R dimension without a translation table.
    IMAGE = "image"
    ACCOUNT = "account"
    REPORT = "report"


class DimensionCode(str, Enum):
    """The frontend's four-dimension codes."""

    T = "T"
    R = "R"
    U = "U"
    S = "S"


class DimensionMeta:
    __slots__ = ("key", "code", "label", "question", "input_label")

    def __init__(self, key: str, code: str, label: str, question: str, input_label: str) -> None:
        self.key = key
        self.code = code
        self.label = label
        self.question = question
        self.input_label = input_label


# Canonical order matches how the frontend lays the four dimensions out.
DIMENSION_ORDER: tuple[str, ...] = (
    AgentName.ACCOUNT.value,
    AgentName.IMAGE.value,
    AgentName.COMMENT.value,
    AgentName.TEXT.value,
)

DIMENSION_META: dict[str, DimensionMeta] = {
    AgentName.ACCOUNT.value: DimensionMeta(
        key="account", code="T", label="账号可信度",
        question="这个账号长期可靠吗？",
        input_label="账号主页",
    ),
    AgentName.IMAGE.value: DimensionMeta(
        key="image", code="R", label="图片真实性",
        question="这张图是真的吗？",
        input_label="图片",
    ),
    AgentName.COMMENT.value: DimensionMeta(
        key="comment", code="U", label="评论区真实性",
        question="评论是真人说的吗？",
        input_label="评论",
    ),
    AgentName.TEXT.value: DimensionMeta(
        key="text", code="S", label="文案真实性",
        question="这段话是体验，还是写好的脚本？",
        input_label="文案",
    ),
}


def dimension_meta(key: str) -> DimensionMeta | None:
    return DIMENSION_META.get(key)
