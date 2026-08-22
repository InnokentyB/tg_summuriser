from dataclasses import dataclass


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
