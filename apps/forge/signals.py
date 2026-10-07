"""Async-style badge computation via signals -- no transactions.

A new Review row fires post_save; we recompute the author's badge progress
with read aggregations and append at most one BadgeAward row per definition
via get_or_create. Each write is independent (eventual consistency).
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Review
from .services import evaluate_badges_for_user


@receiver(post_save, sender=Review)
def award_badges_on_review(sender, instance, created, **kwargs):
    if not created:
        return
    # Author earns badges from received reviews; reviewer progress is
    # intentionally untouched (isolated POST, no profile mutation).
    evaluate_badges_for_user(instance.artifact.author)
