from dataclasses import dataclass, field


@dataclass(slots=True)
class ProductMatch:
    product: str
    score: float
    why_useful: str
    suggested_use: str


@dataclass(slots=True)
class ProcessedPost:
    language: str
    summary: str
    why_important: str
    category: str
    importance_score: float
    relevance_score: float
    explanation: str
    is_promotional: bool = False
    product_matches: list[ProductMatch] = field(default_factory=list)
