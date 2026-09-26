"""Data transfer schemas for post analysis and processing."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ProductMatch:
    """Represents a potential product alignment score and rationale."""

    product: str
    score: float
    why_useful: str
    suggested_use: str


@dataclass(slots=True)
class ProcessedPost:
    """Processed post representation produced by AI pipeline and prefilter."""

    language: str
    summary: str
    why_important: str
    category: str
    importance_score: float
    relevance_score: float
    explanation: str
    is_promotional: bool = False
    product_matches: list[ProductMatch] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Enforce maximum lengths for database constraints."""
        if self.language:
            object.__setattr__(self, "language", str(self.language)[:16])
        if self.category:
            object.__setattr__(self, "category", str(self.category)[:255])
