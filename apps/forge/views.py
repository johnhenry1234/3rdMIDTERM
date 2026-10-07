"""SkillForge views: single-row writes, aggregation reads, no transactions.

Every POST below performs exactly ONE INSERT (Artifact or Review) and never
touches aggregate/profile rows directly. Badges and averages are derived
afterwards via services (Avg/Count) or the Review post_save signal.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Artifact, BadgeAward, BadgeDefinition, Review, Rubric, Skill
from .services import (
    artifact_summary,
    average_received,
    evaluate_badges_for_user,
    review_queue_for,
    skill_progress_for_user,
    user_skill_summary,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# 1. Artifact submissions (authenticated)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def artifact_list(request):
    query = request.GET.get('q', '').strip()
    qs = (
        Artifact.objects.select_related('author', 'skill')
        .annotate(avg_score=Avg('reviews__score'), review_count=Count('reviews'))
        .order_by('-created_at')
    )
    if query:
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(skill__name__icontains=query)
            | Q(author__username__icontains=query)
        )
    paginator = Paginator(qs, 10)
    artifacts = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'forge/artifact_list.html', {
        'artifacts': artifacts,
        'query': query,
        'skills': Skill.objects.all(),
    })


@login_required(login_url='login')
def artifact_create(request):
    skills = Skill.objects.prefetch_related('rubrics').all()
    if request.method == 'GET':
        return render(request, 'forge/artifact_form.html', {'skills': skills})

    title = request.POST.get('title', '').strip()
    skill_id = request.POST.get('skill', '').strip()
    artifact_type = request.POST.get('artifact_type', 'other')
    description = request.POST.get('description', '').strip()
    content = request.POST.get('content', '').strip()
    link_url = request.POST.get('link_url', '').strip()
    is_public = request.POST.get('is_public') == 'on'

    errors = {}
    if not title:
        errors['title'] = 'Title is required.'
    skill = None
    if skill_id:
        try:
            skill = Skill.objects.get(pk=skill_id)
        except (Skill.DoesNotExist, ValueError):
            errors['skill'] = 'Selected skill does not exist.'
    valid_types = {c for c, _ in Artifact.ArtifactType.choices}
    if artifact_type not in valid_types:
        errors['artifact_type'] = 'Invalid artifact type.'
    if not content and not link_url:
        errors['content'] = 'Provide a code snippet/content or a link URL as evidence.'

    if errors:
        return render(request, 'forge/artifact_form.html', {
            'skills': skills,
            'errors': errors,
            'form_data': request.POST,
        })

    # Single-row INSERT -- no aggregate updates, no transaction block.
    artifact = Artifact.objects.create(
        author=request.user,
        skill=skill,
        title=title,
        description=description,
        artifact_type=artifact_type,
        content=content,
        link_url=link_url,
        is_public=is_public,
    )
    messages.success(request, f'Artifact "{artifact.title}" submitted. Share it via its portfolio link.')
    return redirect('forge:artifact_detail', pk=artifact.pk)


@login_required(login_url='login')
def artifact_detail(request, pk):
    artifact = get_object_or_404(
        Artifact.objects.select_related('author', 'skill'), pk=pk
    )
    summary = artifact_summary(artifact)
    reviews = (
        Review.objects.filter(artifact=artifact)
        .select_related('rubric')
        .order_by('-created_at')
    )
    # Blind review: hide reviewer identity from the author; staff see all.
    blind = artifact.author_id == request.user.id and not request.user.is_staff
    rubrics = Rubric.objects.filter(skill=artifact.skill) if artifact.skill else Rubric.objects.all()
    already_reviewed_rubric_ids = set(
        Review.objects.filter(artifact=artifact, reviewer=request.user)
        .values_list('rubric_id', flat=True)
    )
    return render(request, 'forge/artifact_detail.html', {
        'artifact': artifact,
        'summary': summary,
        'reviews': reviews,
        'blind': blind,
        'rubrics': rubrics,
        'already_reviewed_rubric_ids': already_reviewed_rubric_ids,
    })


# ---------------------------------------------------------------------------
# 2. Blind peer evaluation matrix (authenticated, isolated POSTs)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def review_queue(request):
    queue = review_queue_for(request.user)
    rubrics = Rubric.objects.select_related('skill').all()
    return render(request, 'forge/review_queue.html', {
        'queue': queue,
        'rubrics': rubrics,
    })


@login_required(login_url='login')
@require_POST
def review_submit(request):
    """Submit ONE rubric score: a single INSERT, nothing else is mutated."""
    artifact_id = request.POST.get('artifact', '').strip()
    rubric_id = request.POST.get('rubric', '').strip()
    raw_score = request.POST.get('score', '').strip()
    comment = request.POST.get('comment', '').strip()

    artifact = get_object_or_404(Artifact, pk=artifact_id) if artifact_id.isdigit() else None
    rubric = get_object_or_404(Rubric, pk=rubric_id) if rubric_id.isdigit() else None
    if artifact is None or rubric is None:
        messages.error(request, 'Artifact and rubric are required.')
        return redirect('forge:review_queue')

    if artifact.author_id == request.user.id:
        messages.error(request, 'You cannot review your own artifact (blind review).')
        return redirect('forge:artifact_detail', pk=artifact.pk)

    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        messages.error(request, 'Score must be a whole number.')
        return redirect('forge:artifact_detail', pk=artifact.pk)

    if score < 1 or score > rubric.max_score:
        messages.error(request, f'Score must be between 1 and {rubric.max_score}.')
        return redirect('forge:artifact_detail', pk=artifact.pk)

    if Review.objects.filter(artifact=artifact, reviewer=request.user, rubric=rubric).exists():
        messages.error(request, f'You already scored "{rubric.name}" for this artifact (append-only).')
        return redirect('forge:artifact_detail', pk=artifact.pk)

    try:
        # Isolated single-row write: never touches the author's profile.
        Review.objects.create(
            artifact=artifact, reviewer=request.user,
            rubric=rubric, score=score, comment=comment,
        )
    except IntegrityError:
        messages.error(request, 'Duplicate review detected; only one score per rubric is kept.')
        return redirect('forge:artifact_detail', pk=artifact.pk)

    # Badge recomputation happens in the post_save signal (eventual consistency).
    messages.success(request, f'Score {score}/{rubric.max_score} recorded for "{rubric.name}".')
    return redirect('forge:artifact_detail', pk=artifact.pk)


# ---------------------------------------------------------------------------
# 3. Badges & progress (computed on demand, cached querysets)
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def badge_list(request):
    # Recompute on view (idempotent) so the page is never stale, then read.
    evaluate_badges_for_user(request.user)
    definitions = BadgeDefinition.objects.select_related('skill').all()
    awards = {a.badge_id: a for a in BadgeAward.objects.filter(user=request.user).select_related('badge')}
    progress = []
    for definition in definitions:
        stats = skill_progress_for_user(request.user, definition.skill)
        progress.append({
            'definition': definition,
            'award': awards.get(definition.id),
            'avg_score': stats['avg_score'],
            'review_count': stats['review_count'],
        })
    return render(request, 'forge/badge_list.html', {
        'progress': progress,
        'skill_summary': user_skill_summary(request.user),
        'overall': average_received(request.user),
    })


# ---------------------------------------------------------------------------
# 4. Public skill ledger & verification (read-only, no login)
# ---------------------------------------------------------------------------

def ledger(request):
    """Public, read-optimized directory of builders and their badges."""
    query = request.GET.get('q', '').strip()
    users = (
        User.objects.annotate(
            badge_count=Count('badge_awards', distinct=True),
            artifacts_count=Count('artifacts', distinct=True),
        )
        .order_by('-badge_count', 'username')
    )
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    paginator = Paginator(users, 12)
    page = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'forge/ledger.html', {'users': page, 'query': query})


def public_profile(request, username):
    """Public skill profile for employers/peers: reads only."""
    profile_user = get_object_or_404(User, username=username)
    evaluate_badges_for_user(profile_user)  # idempotent refresh; single-row writes only
    artifacts = (
        Artifact.objects.filter(author=profile_user, is_public=True)
        .select_related('skill')
        .annotate(avg_score=Avg('reviews__score'), review_count=Count('reviews'))
        .order_by('-created_at')
    )
    awards = BadgeAward.objects.filter(user=profile_user).select_related('badge', 'badge__skill')
    return render(request, 'forge/profile_public.html', {
        'profile_user': profile_user,
        'artifacts': artifacts,
        'awards': awards,
        'skill_summary': user_skill_summary(profile_user),
        'overall': average_received(profile_user),
    })


def portfolio_by_token(request, token):
    """Unique, shareable dynamic portfolio page for one artifact."""
    artifact = get_object_or_404(
        Artifact.objects.select_related('author', 'skill'), share_token=token
    )
    if not artifact.is_public and (
        not request.user.is_authenticated
        or (request.user != artifact.author and not request.user.is_staff)
    ):
        return render(request, 'forge/portfolio.html', {'private': True})
    summary = artifact_summary(artifact)
    more_from_author = (
        Artifact.objects.filter(author=artifact.author, is_public=True)
        .exclude(pk=artifact.pk)
        .annotate(avg_score=Avg('reviews__score'), review_count=Count('reviews'))
        .order_by('-created_at')[:6]
    )
    return render(request, 'forge/portfolio.html', {
        'artifact': artifact,
        'summary': summary,
        'more_from_author': more_from_author,
        'private': False,
    })
