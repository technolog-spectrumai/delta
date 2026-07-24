"""Personalized learning path generation and syncing.

A path is built by gap analysis over the SkillBadge prerequisite DAG:
take the transitive prerequisite closure of the goal, drop badges the
student already earned, and order the rest with a topological sort
(ties broken by the curriculum order fields, matching skill-forest
display order). Steps are frozen at creation; sync_path() re-checks
completion against StudentBadge on every view.
"""
from bisect import insort
from collections import defaultdict

from django.db.models import Max
from django.utils import timezone

from toto.competence.models import SkillBadge, SkillBadgePrerequisite

from .models import PersonalPath, PersonalPathStep, StudentBadge


def _badge_sort_key(badge):
    return (badge.group.order, badge.order, badge.title)


def load_prerequisite_graph():
    """Return (prereqs, dependents) adjacency dicts over SkillBadgePrerequisite."""
    prereqs = defaultdict(set)
    dependents = defaultdict(set)
    for badge_id, prerequisite_id in SkillBadgePrerequisite.objects.values_list(
        "badge_id", "prerequisite_id"
    ):
        prereqs[badge_id].add(prerequisite_id)
        dependents[prerequisite_id].add(badge_id)
    return prereqs, dependents


def transitive_closure(seed_ids, adjacency):
    """All ids reachable from seed_ids via adjacency, including the seeds.

    Visited-set traversal, so cycles in admin data cannot loop forever.
    """
    closure = set()
    stack = list(seed_ids)
    while stack:
        node = stack.pop()
        if node in closure:
            continue
        closure.add(node)
        stack.extend(adjacency[node] - closure)
    return closure


def compute_gap_badges(student, goal_badge=None, goal_group=None):
    """Return the student's unearned badges for the goal, in learning order."""
    earned = set(student.badges.values_list("id", flat=True))

    prereqs, dependents = load_prerequisite_graph()

    if goal_badge is not None:
        frontier = [goal_badge.pk]
    else:
        frontier = list(goal_group.badges.values_list("id", flat=True))

    pending = transitive_closure(frontier, prereqs) - earned
    badges = {
        badge.pk: badge
        for badge in SkillBadge.objects.filter(pk__in=pending).select_related("group")
    }

    # Kahn's algorithm restricted to the pending set: edges from earned or
    # out-of-scope badges drop out of the in-degree counts.
    indegree = {badge_id: len(prereqs[badge_id] & pending) for badge_id in pending}
    ready = sorted(
        (badges[badge_id] for badge_id in pending if indegree[badge_id] == 0),
        key=_badge_sort_key,
    )
    ordered = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for dependent_id in dependents[current.pk] & pending:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                insort(ready, badges[dependent_id], key=_badge_sort_key)

    # The DB only forbids self-prerequisites, not longer cycles — append any
    # leftovers so generation never fails on bad admin data.
    if len(ordered) < len(pending):
        placed = {badge.pk for badge in ordered}
        ordered.extend(
            sorted(
                (badges[badge_id] for badge_id in pending - placed),
                key=_badge_sort_key,
            )
        )

    return ordered


def generate_steps(path, start_order=0, exclude_badge_ids=frozenset()):
    """Create path steps for the goal's gap badges, one per badge."""
    ordered = compute_gap_badges(
        path.student,
        goal_badge=path.goal_badge,
        goal_group=path.goal_group,
    )

    steps = []
    order = start_order
    for badge in ordered:
        if badge.pk in exclude_badge_ids:
            continue

        module = (
            badge.unlocking_modules
            .select_related("course")
            .filter(course__is_published=True)
            .order_by("course__order", "order", "id")
            .first()
        )
        order += 10
        steps.append(
            PersonalPathStep(
                path=path,
                order=order,
                badge=badge,
                module=module,
                step_type=(
                    PersonalPathStep.StepType.MODULE
                    if module
                    else PersonalPathStep.StepType.BADGE
                ),
            )
        )

    PersonalPathStep.objects.bulk_create(steps)
    return steps


def sync_path(path):
    """Absorb badges earned anywhere in the app into the path's step status.

    Returns a progress dict: {"done", "total", "percent"}.
    """
    earned = dict(
        StudentBadge.objects
        .filter(student=path.student)
        .values_list("badge_id", "awarded_at")
    )

    steps = list(path.steps.all())
    changed = [
        step for step in steps
        if step.badge_id and not step.is_completed and step.badge_id in earned
    ]
    for step in changed:
        step.is_completed = True
        step.completed_at = earned[step.badge_id]
    if changed:
        PersonalPathStep.objects.bulk_update(changed, ["is_completed", "completed_at"])

    total = len(steps)
    done = sum(1 for step in steps if step.is_completed)

    if path.status == PersonalPath.Status.ACTIVE and total and done == total:
        path.status = PersonalPath.Status.COMPLETED
        path.completed_at = timezone.now()
        path.save(update_fields=["status", "completed_at", "updated_at"])
    elif path.status == PersonalPath.Status.COMPLETED and done < total:
        path.status = PersonalPath.Status.ACTIVE
        path.completed_at = None
        path.save(update_fields=["status", "completed_at", "updated_at"])

    return {
        "done": done,
        "total": total,
        "percent": round(done * 100 / total) if total else 0,
    }


def regenerate_path(path):
    """Rebuild pending auto steps against the current skill gap.

    Completed steps and user tasks are preserved; pending badge/module
    steps are replaced.
    """
    path.steps.filter(is_completed=False).exclude(
        step_type=PersonalPathStep.StepType.TASK
    ).delete()

    kept = path.steps.all()
    exclude = set(
        kept.filter(badge__isnull=False).values_list("badge_id", flat=True)
    )
    start = kept.aggregate(max_order=Max("order"))["max_order"] or 0
    return generate_steps(path, start_order=start, exclude_badge_ids=exclude)
