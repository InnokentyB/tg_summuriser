from tg_summariser.models import FeedbackValue, Post, PostStatus


class RelevanceScorer:
    def score(
        self,
        post: Post,
        category_affinity: dict[str, float],
        channel_affinity: dict[int, float],
    ) -> tuple[float, PostStatus, str]:
        score = post.relevance_score
        explanation_parts = [post.explanation or "Базовая оценка AI."]

        if post.category and post.category in category_affinity:
            score += min(category_affinity[post.category] * 0.05, 0.2)
            explanation_parts.append("Категория уже часто нравится пользователю.")
        if post.channel_id in channel_affinity:
            score += min(channel_affinity[post.channel_id] * 0.05, 0.2)
            explanation_parts.append("Источник уже показывал интересные посты.")

        status = PostStatus.processed
        if post.duplicate_of_post_id:
            score -= 0.4
            status = PostStatus.hidden
            explanation_parts.append("Пост скрыт как почти дубликат.")
        elif score < 0.35:
            status = PostStatus.hidden
            explanation_parts.append("Слабая релевантность по текущим сигналам.")
        else:
            explanation_parts.append("Лучше показать, чем скрыть, при текущей уверенности.")

        return max(0.0, min(score, 1.0)), status, " ".join(explanation_parts)

    def feedback_adjustment(self, feedback: FeedbackValue) -> float:
        return 0.15 if feedback == FeedbackValue.interested else -0.25

