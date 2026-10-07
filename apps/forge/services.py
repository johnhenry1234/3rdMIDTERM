"""Read-model helpers: on-demand aggregation, no transactions.

Every function here is a pure read (Avg/Count) except
evaluate_badges_for_user, which performs single-row get_or_create calls --
each one is its own atomic INSERT-or-fetch, never a multi-table atomic block.
"""

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count

from .models import Artifact, BadgeAward, BadgeDefinition, Review

User = get_user_model()


def artifact_summary(artifact):
    """Aggregate stats for one artifact, computed on the fly."""
    agg = Review.objects.filter(artifact=artifact).aggregate(
        avg_score=Avg('score'), total_reviews=Count('id')
    )
    per_rubric = list(
        Review.objects.filter(artifact=artifact)
        .values('rubric__id', 'rubric__name', 'rubric__max_score')
        .annotate(avg_score=Avg('score'), review_count=Count('id'))
        .order_by('rubric__name')
    )
    return {
        'avg_score': agg['avg_score'],
        'total_reviews': agg['total_reviews'] or 0,
        'per_rubric': per_rubric,
    }


def user_skill_summary(user):
    """Per-skill aggregates for artifacts authored by `user`.

    One grouped query -- read-only, eventual consistency is fine.
    """
    rows = (
        Review.objects.filter(artifact__author=user)
        .values('artifact__skill__id', 'artifact__skill__name', 'artifact__skill__slug')
        .annotate(avg_score=Avg('score'), review_count=Count('id'))
        .order_by('artifact__skill__name')
    )
    return [
        {
            'skill_id': r['artifact__skill__id'],
            'skill_name': r['artifact__skill__name'] or '(no skill)',
            'skill_slug': r['artifact__skill__slug'],
            'avg_score': r['avg_score'],
            'review_count': r['review_count'],
        }
        for r in rows
    ]


def skill_progress_for_user(user, skill):
    """Aggregate received reviews on the user's artifacts for one skill."""
    agg = Review.objects.filter(
        artifact__author=user, artifact__skill=skill
    ).aggregate(avg_score=Avg('score'), review_count=Count('id'))
    return {
        'avg_score': agg['avg_score'],
        'review_count': agg['review_count'] or 0,
    }


def evaluate_badges_for_user(user):
    """Award every BadgeDefinition whose threshold the user now meets.

    Called from a post_save signal and from read views (idempotent).
    Uses get_or_create per badge: a single atomic row lock, never a
    multi-statement transaction block.
    """
    awarded = []
    for definition in BadgeDefinition.objects.select_related('skill').all():
        progress = skill_progress_for_user(user, definition.skill)
        if progress['review_count'] < definition.min_reviews:
            continue
        if progress['avg_score'] is None or float(progress['avg_score']) < float(definition.min_avg):
            continue
        obj, created = BadgeAward.objects.get_or_create(
            badge=definition,
            user=user,
            defaults={
                'evidence_avg': progress['avg_score'],
                'evidence_count': progress['review_count'],
            },
        )
        if created:
            awarded.append(obj)
    return awarded


def review_queue_for(user, limit=50):
    """Artifacts awaiting this reviewer's evaluation (blind queue).

    Excludes the reviewer's own work and anything they already reviewed,
    ordered by fewest total reviews first so scoring load spreads evenly.
    Author identity is hidden in the template (blind review).
    """
    return (
        Artifact.objects.exclude(author=user)
        .exclude(reviews__reviewer=user)
        .annotate(review_count=Count('reviews'))
        .select_related('skill', 'author')
        .order_by('review_count', '-created_at')[:limit]
    )


def ledger_rows():
    """Public skill ledger: per-user badge counts and received-review stats.

    Read-only aggregation for employers/peers verifying public profiles.
    """
    return (
        User.objects.annotate(
            badge_count=Count('badge_awards', distinct=True),
            artifacts_count=Count('artifacts', distinct=True),
        )
        .values('id', 'username', 'first_name', 'last_name', 'badge_count', 'artifacts_count')
        .order_by('-badge_count', 'username')
    )


def average_received(user):
    """Overall average score received across all of a user's artifacts."""
    agg = Review.objects.filter(artifact__author=user).aggregate(
        avg_score=Avg('score'), review_count=Count('id')
    )
    return {'avg_score': agg['avg_score'], 'review_count': agg['review_count'] or 0}
