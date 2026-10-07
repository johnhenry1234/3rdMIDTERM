"""SkillForge models: purely additive, read-heavy, event-driven.

Design notes (why no transactions):
- Reviews, scores, and artifacts exist independently. A peer review is a
  single-row INSERT into one table; it never lock-steps with another write.
- No shared mutable state: nobody reserves inventory/seats/balances, so
  concurrent evaluators never contend on the same row.
- Append-only: evaluations are immutable records. Aggregates (Avg/Count)
  and badge awards are computed on-demand or via a post_save signal that
  only does single-row get_or_create -- never transaction.atomic blocks.
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Skill(models.Model):
    """A verifiable skill area (e.g. Database Design, Web Security, UX)."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Rubric(models.Model):
    """One evaluation criterion belonging to a skill.

    Examples: Code Quality, Security, UX. Each review scores exactly one
    rubric, keeping every POST an isolated single-row write.
    """

    skill = models.ForeignKey(Skill, related_name='rubrics', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    max_score = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )

    class Meta:
        ordering = ['skill__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['skill', 'name'], name='unique_rubric_per_skill'),
        ]

    def __str__(self):
        return f'{self.skill.name} / {self.name}'


class Artifact(models.Model):
    """Evidence of a skill uploaded by a student/creator."""

    class ArtifactType(models.TextChoices):
        CODE = 'code', 'Code snippet'
        ERD = 'erd', 'ERD diagram'
        LINK = 'link', 'UI link'
        DEMO = 'demo', 'Short demonstration'
        OTHER = 'other', 'Other'

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='artifacts', on_delete=models.CASCADE
    )
    skill = models.ForeignKey(
        Skill, related_name='artifacts', on_delete=models.SET_NULL, null=True, blank=True
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    artifact_type = models.CharField(
        max_length=10, choices=ArtifactType.choices, default=ArtifactType.OTHER
    )
    content = models.TextField(blank=True, help_text='Code snippet / embed / notes.')
    link_url = models.URLField(blank=True, help_text='UI link or demo URL.')
    # Unique, shareable portfolio page identifier. Never updated after creation.
    share_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Review(models.Model):
    """One immutable peer evaluation of an artifact against one rubric.

    Append-only: created via a single INSERT. No update endpoint is exposed;
    aggregates are derived with Avg/Count at read time.
    """

    artifact = models.ForeignKey(Artifact, related_name='reviews', on_delete=models.CASCADE)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='reviews_given', on_delete=models.CASCADE
    )
    rubric = models.ForeignKey(Rubric, related_name='reviews', on_delete=models.CASCADE)
    score = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One reviewer scores each (artifact, rubric) at most once.
            models.UniqueConstraint(
                fields=['artifact', 'reviewer', 'rubric'],
                name='unique_review_per_artifact_rubric',
            ),
        ]

    def __str__(self):
        return f'{self.rubric.name}={self.score} on "{self.artifact.title}"'


class BadgeDefinition(models.Model):
    """Threshold rule, e.g. 'Database Architect Level 1'.

    Awarded when an author accumulates >= min_reviews independent reviews
    on artifacts of `skill` with an average score >= min_avg.
    """

    skill = models.ForeignKey(Skill, related_name='badge_definitions', on_delete=models.CASCADE)
    name = models.CharField(max_length=150, unique=True)
    level = models.PositiveIntegerField(default=1)
    min_reviews = models.PositiveIntegerField(default=3)
    min_avg = models.DecimalField(max_digits=4, decimal_places=2, default='4.00')
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['skill__name', 'level']
        constraints = [
            models.UniqueConstraint(fields=['skill', 'level'], name='unique_badge_per_skill_level'),
        ]

    def __str__(self):
        return self.name


class BadgeAward(models.Model):
    """Append-only ledger row recording that a user earned a badge."""

    badge = models.ForeignKey(BadgeDefinition, related_name='awards', on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='badge_awards', on_delete=models.CASCADE
    )
    evidence_avg = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    evidence_count = models.PositiveIntegerField(default=0)
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-awarded_at']
        constraints = [
            models.UniqueConstraint(fields=['badge', 'user'], name='unique_badge_award_per_user'),
        ]

    def __str__(self):
        return f'{self.user.username} <- {self.badge.name}'
