"""Hybrid badge goal recommendations for personalized learning paths.

Blends content-based graph metrics computed on the SkillBadge prerequisite
DAG (readiness, unlock power, tree continuation) with item-based
collaborative filtering over StudentBadge co-occurrence ("students who
earned X also earned Y", cosine-normalized). The collaborative weight
shrinks automatically while there is little co-occurrence data, so a
fresh install degrades cleanly to pure content-based ranking.
"""
from math import sqrt

from django.core.cache import cache
from django.db.models import Count, Max

from toto.competence.models import SkillBadge

from .models import PersonalPath, StudentBadge
from .paths import load_prerequisite_graph, transitive_closure

RECOMMENDATION_LIMIT = 6

# Content mix: how the three graph metrics combine (must sum to 1).
W_READINESS = 0.5
W_UNLOCK = 0.25
W_CONTINUATION = 0.25

# Collaborative share of the hybrid score at full confidence, and how many
# students with >=2 badges are needed to reach it.
W_CF_MAX = 0.4
CF_COLD_START_STUDENTS = 20

CF_MODEL_TTL = 3600
CACHE_KEY_PREFIX = "academy:recs"
CACHE_VERSION = 1

# Reason-chip thresholds: "popular" is only claimed when the collaborative
# signal is strong and actually carried weight in the score.
POPULAR_MIN_CF = 0.5
POPULAR_MIN_WEIGHT = 0.15


def _build_cf_model():
    """Global co-occurrence model over StudentBadge.

    Returns {"badge_students": {badge_id: set(student_ids)},
             "n_pair_students": int}, where n_pair_students counts students
    holding at least two badges — the only ones creating co-occurrence
    signal.
    """
    badge_students = {}
    student_badge_counts = {}
    for student_id, badge_id in StudentBadge.objects.values_list(
        "student_id", "badge_id"
    ):
        badge_students.setdefault(badge_id, set()).add(student_id)
        student_badge_counts[student_id] = student_badge_counts.get(student_id, 0) + 1

    return {
        "badge_students": badge_students,
        "n_pair_students": sum(
            1 for count in student_badge_counts.values() if count >= 2
        ),
    }


def _cf_model():
    """Cached CF model; the key is stamped with the StudentBadge state so any
    new award switches to a fresh key and old entries expire via TTL."""
    stats = StudentBadge.objects.aggregate(n=Count("id"), max_id=Max("id"))
    key = (
        f"{CACHE_KEY_PREFIX}:cf:v{CACHE_VERSION}"
        f":{stats['n'] or 0}:{stats['max_id'] or 0}"
    )
    return cache.get_or_set(key, _build_cf_model, CF_MODEL_TTL)


def _cosine_similarity(candidate_students, earned_students):
    if not candidate_students or not earned_students:
        return 0.0
    overlap = len(candidate_students & earned_students)
    if not overlap:
        return 0.0
    return overlap / sqrt(len(candidate_students) * len(earned_students))


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

    model = _cf_model()
    badge_students = model["badge_students"]
    confidence = (
        min(1.0, model["n_pair_students"] / CF_COLD_START_STUDENTS)
        if earned else 0.0
    )
    w_cf = W_CF_MAX * confidence

    earned_student_sets = [
        badge_students.get(badge_id, set()) - {student.pk}
        for badge_id in earned
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

        candidate_students = badge_students.get(badge.pk, set())
        cf_raw = (
            sum(
                _cosine_similarity(candidate_students, earned_students)
                for earned_students in earned_student_sets
            ) / len(earned_student_sets)
            if earned_student_sets else 0.0
        )

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
