"""Hybrid badge goal recommendations for personalized learning paths.

Blends content-based graph metrics computed per-request on the SkillBadge
prerequisite DAG (readiness, unlock power, tree continuation) with
item-based collaborative filtering read from a precomputed badge x badge
cosine-similarity matrix (numpy) stored in the Django cache (Redis in
production, LocMem in dev/tests).

The matrix is rebuilt daily by Celery beat (toto.academy.tasks
.recompute_similarity_matrix), on demand from the RecommendationConfig
admin, and lazily on cache miss — so views work before the first beat run.
Collaborative signal may therefore lag badge awards by up to a day; that
staleness is the accepted trade-off for request-time speed.

Tuning lives in the RecommendationConfig singleton: cf_strength caps the
collaborative share of the score, and cold-start shrinkage still applies
on top (effective weight = cf_strength * data confidence), so a fresh
install degrades cleanly to pure content-based ranking.

The cached payload contains a pickled numpy array (django-redis pickles
transparently); the Redis instance is compose-internal, which is the trust
boundary that makes pickle acceptable here.
"""
import numpy as np

from django.core.cache import cache
from django.utils import timezone

from toto.competence.models import SkillBadge

from .models import PersonalPath, RecommendationConfig, StudentBadge
from .paths import load_prerequisite_graph, transitive_closure

RECOMMENDATION_LIMIT = 6

# Content mix: how the three graph metrics combine (must sum to 1).
W_READINESS = 0.5
W_UNLOCK = 0.25
W_CONTINUATION = 0.25

CACHE_KEY_PREFIX = "academy:recs"
CACHE_VERSION = 2
SIMILARITY_MATRIX_KEY = f"{CACHE_KEY_PREFIX}:simmatrix:v{CACHE_VERSION}"
# Beat refreshes daily; the TTL is only a safety net against orphaned keys.
SIMILARITY_MATRIX_TTL = 7 * 24 * 3600

# Reason-chip thresholds: "popular" is only claimed when the collaborative
# signal is strong and actually carried weight in the score.
POPULAR_MIN_CF = 0.5
POPULAR_MIN_WEIGHT = 0.15


def build_similarity_matrix():
    """Item-item cosine similarity over StudentBadge, as a numpy matrix.

    Returns the cache payload:
      {"matrix": ndarray (n_badges x n_badges, float64, zero diagonal),
       "badge_ids": sorted list mapping row/col index -> badge id,
       "n_students": int,
       "n_pair_students": int,   # students holding >= 2 badges
       "computed_at": iso datetime string}
    """
    rows = list(StudentBadge.objects.values_list("student_id", "badge_id"))

    badge_ids = sorted({badge_id for _, badge_id in rows})
    student_ids = sorted({student_id for student_id, _ in rows})
    badge_index = {badge_id: i for i, badge_id in enumerate(badge_ids)}
    student_index = {student_id: i for i, student_id in enumerate(student_ids)}

    interactions = np.zeros((len(badge_ids), len(student_ids)), dtype=np.float64)
    for student_id, badge_id in rows:
        interactions[badge_index[badge_id], student_index[student_id]] = 1.0

    if len(badge_ids):
        norms = np.sqrt(interactions.sum(axis=1))
        normalized = interactions / np.where(norms == 0, 1.0, norms)[:, None]
        matrix = normalized @ normalized.T
        np.fill_diagonal(matrix, 0.0)
        n_pair_students = int((interactions.sum(axis=0) >= 2).sum())
    else:
        matrix = np.zeros((0, 0), dtype=np.float64)
        n_pair_students = 0

    return {
        "matrix": matrix,
        "badge_ids": badge_ids,
        "n_students": len(student_ids),
        "n_pair_students": n_pair_students,
        "computed_at": timezone.now().isoformat(),
    }


def refresh_similarity_matrix():
    """Build the similarity matrix and store it in the cache."""
    payload = build_similarity_matrix()
    cache.set(SIMILARITY_MATRIX_KEY, payload, SIMILARITY_MATRIX_TTL)
    return payload


def get_similarity_matrix():
    """Cached matrix payload; built synchronously on a cache miss so views
    keep working before the first beat run (or without a worker at all)."""
    payload = cache.get(SIMILARITY_MATRIX_KEY)
    if payload is None:
        payload = refresh_similarity_matrix()
    return payload


def recommend_goals(student, limit=RECOMMENDATION_LIMIT):
    """Ranked SkillBadge goal suggestions for a student.

    Returns a list of dicts:
    {"badge": SkillBadge, "score": float, "reasons": [
        {"kind": "ready"},
        {"kind": "unlocks", "count": 3},
        {"kind": "continues", "group": SkillGroup},
        {"kind": "popular"},
    ]}
    """
    earned = set(student.badges.values_list("id", flat=True))

    excluded = set(earned)
    active_path = student.personal_paths.filter(
        status=PersonalPath.Status.ACTIVE
    ).first()
    if active_path:
        if active_path.goal_badge_id:
            excluded.add(active_path.goal_badge_id)
        excluded.update(
            active_path.steps
            .filter(badge__isnull=False)
            .values_list("badge_id", flat=True)
        )

    all_badges = list(SkillBadge.objects.select_related("group"))
    candidates = [badge for badge in all_badges if badge.pk not in excluded]
    if not candidates:
        return []

    prereqs, dependents = load_prerequisite_graph()

    group_sizes = {}
    earned_in_group = {}
    for badge in all_badges:
        group_sizes[badge.group_id] = group_sizes.get(badge.group_id, 0) + 1
        if badge.pk in earned:
            earned_in_group[badge.group_id] = earned_in_group.get(badge.group_id, 0) + 1

    descendant_counts = {
        badge.pk: len(transitive_closure({badge.pk}, dependents)) - 1
        for badge in all_badges
    }
    max_descendants = max(descendant_counts.values(), default=0)

    config = RecommendationConfig.load()
    payload = get_similarity_matrix()
    matrix = payload["matrix"]
    badge_index = {badge_id: i for i, badge_id in enumerate(payload["badge_ids"])}

    if earned and config.cold_start_students:
        confidence = min(
            1.0, payload["n_pair_students"] / config.cold_start_students)
    elif earned and payload["n_pair_students"]:
        confidence = 1.0
    else:
        confidence = 0.0
    w_cf = config.cf_strength * confidence

    # Badges awarded/created after the last recompute are simply absent from
    # the matrix and score 0 collaboratively — content still ranks them.
    earned_indices = [
        badge_index[badge_id] for badge_id in earned if badge_id in badge_index
    ]

    scored = []
    for badge in candidates:
        ancestors = transitive_closure({badge.pk}, prereqs) - {badge.pk}
        readiness = (
            len(ancestors & earned) / len(ancestors) if ancestors else 1.0
        )

        n_descendants = descendant_counts[badge.pk]
        unlock = n_descendants / max_descendants if max_descendants else 0.0

        group_earned = earned_in_group.get(badge.group_id, 0)
        continuation = (
            group_earned / group_sizes[badge.group_id] if group_earned else 0.0
        )

        content = (
            W_READINESS * readiness
            + W_UNLOCK * unlock
            + W_CONTINUATION * continuation
        )

        if earned_indices and badge.pk in badge_index:
            cf_raw = float(matrix[badge_index[badge.pk], earned_indices].mean())
        else:
            cf_raw = 0.0

        scored.append({
            "badge": badge,
            "readiness": readiness,
            "n_descendants": n_descendants,
            "group_earned": group_earned,
            "content": content,
            "cf_raw": cf_raw,
        })

    max_cf_raw = max(entry["cf_raw"] for entry in scored)
    results = []
    for entry in scored:
        cf = entry["cf_raw"] / max_cf_raw if max_cf_raw else 0.0
        score = (1.0 - w_cf) * entry["content"] + w_cf * cf

        reasons = []
        if entry["readiness"] == 1.0:
            reasons.append({"kind": "ready"})
        if entry["n_descendants"] >= 1:
            reasons.append({"kind": "unlocks", "count": entry["n_descendants"]})
        if entry["group_earned"] >= 1:
            reasons.append({"kind": "continues", "group": entry["badge"].group})
        if cf >= POPULAR_MIN_CF and w_cf >= POPULAR_MIN_WEIGHT:
            reasons.append({"kind": "popular"})

        results.append({
            "badge": entry["badge"],
            "score": score,
            "reasons": reasons,
        })

    results.sort(
        key=lambda entry: (
            -entry["score"],
            entry["badge"].group.order,
            entry["badge"].order,
            entry["badge"].title,
        )
    )
    return results[:limit]
