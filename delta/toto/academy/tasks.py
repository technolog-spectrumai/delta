from celery import shared_task

from .recommendations import refresh_similarity_matrix


@shared_task
def recompute_similarity_matrix():
    """Rebuild the badge x badge similarity matrix and store it in the cache.

    Idempotent. Runs daily via CELERY_BEAT_SCHEDULE and on demand from the
    RecommendationConfig admin. Returns a small summary for worker logs.
    """
    payload = refresh_similarity_matrix()
    return {
        "badges": len(payload["badge_ids"]),
        "students": payload["n_students"],
        "pair_students": payload["n_pair_students"],
        "computed_at": payload["computed_at"],
    }
