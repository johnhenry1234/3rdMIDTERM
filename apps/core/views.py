from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

User = get_user_model()

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=1)
INVALID_LOGIN_MESSAGE = 'Invalid username or password.'
LOCKED_MESSAGE = 'Too many failed attempts. Locked for 1 minute.'


def _reset_lock_state(request):
    request.session['failed_attempts'] = 0
    request.session['lock_time'] = None


def _get_lock_state(request):
    failed_attempts = request.session.get('failed_attempts', 0)
    lock_time = request.session.get('lock_time')

    if not lock_time:
        return failed_attempts, None

    try:
        unlock_time = timezone.datetime.fromisoformat(lock_time)
    except (TypeError, ValueError):
        _reset_lock_state(request)
        return 0, None

    if timezone.now() < unlock_time:
        remaining = int((unlock_time - timezone.now()).total_seconds())
        return failed_attempts, remaining

    _reset_lock_state(request)
    return 0, None


def _set_lock_state(request):
    lock_until = timezone.now() + LOCK_DURATION
    request.session['failed_attempts'] = MAX_FAILED_ATTEMPTS
    request.session['lock_time'] = lock_until.isoformat()
    return int(LOCK_DURATION.total_seconds())


def _validate_registration_data(data):
    errors = {}

    username = data.get('username', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')

    if not username:
        errors['username'] = 'Username is required.'
    elif User.objects.filter(username=username).exists():
        errors['username'] = 'Username already taken.'

    if not first_name:
        errors['first_name'] = 'First name is required.'

    if not last_name:
        errors['last_name'] = 'Last name is required.'

    if not email:
        errors['email'] = 'Email is required.'
    else:
        try:
            validate_email(email)
        except ValidationError:
            errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email=email).exists():
                errors['email'] = 'Email already registered.'

    if not password:
        errors['password'] = 'Password is required.'
    elif len(password) < 8:
        errors['password'] = 'Password must be at least 8 characters.'
    else:
        try:
            validate_password(password)
        except ValidationError as exc:
            errors['password'] = ' '.join(exc.messages)

    if password != confirm_password:
        errors['confirm_password'] = 'Passwords do not match.'

    return errors


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    failed_attempts, remaining = _get_lock_state(request)
    if remaining is not None:
        messages.error(request, f'Too many failed attempts. Try again in {remaining} seconds.')
        return render(request, 'auth/login.html', {'locked': True, 'remaining': remaining})

    if request.method == 'GET':
        return render(request, 'auth/login.html')

    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '')
    user = authenticate(request, username=username, password=password)

    if user is not None:
        _reset_lock_state(request)
        login(request, user)
        return redirect('dashboard')

    failed_attempts += 1
    request.session['failed_attempts'] = failed_attempts
    if failed_attempts >= MAX_FAILED_ATTEMPTS:
        remaining = _set_lock_state(request)
        messages.error(request, LOCKED_MESSAGE)
        return render(request, 'auth/login.html', {'locked': True, 'remaining': remaining})

    remaining_attempts = MAX_FAILED_ATTEMPTS - failed_attempts
    messages.error(request, f'{INVALID_LOGIN_MESSAGE} {remaining_attempts} attempts remaining.')
    return render(request, 'auth/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required(login_url='login')
def home(request):
    """SaaS analytics dashboard: live platform metrics + trend series."""
    import json
    from datetime import timedelta

    from django.db.models import Avg, Count
    from django.db.models.functions import TruncDate
    from django.utils import timezone

    context = {
        'my_artifacts_count': 0,
        'reviews_given': 0,
        'received_count': 0,
        'received_avg': None,
        'badges_count': 0,
        'recent_artifacts': [],
        'queue_preview': [],
    }
    try:
        from apps.forge.models import Artifact, BadgeAward, Review
        from apps.forge.services import average_received, review_queue_for

        User = get_user_model()
        now = timezone.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        periods = [day - timedelta(days=i) for i in range(13, -1, -1)]
        labels = [d.strftime('%b %d') for d in periods]

        def per_day(qs, date_field='created_at'):
            counts = dict(
                qs.filter(**{f'{date_field}__gte': periods[0]})
                .annotate(d=TruncDate(date_field))
                .values('d').annotate(c=Count('id')).values_list('d', 'c')
            )
            return [counts.get(d.date(), 0) for d in periods]

        def window_change(qs, date_field='created_at'):
            last7 = qs.filter(**{f'{date_field}__gte': day - timedelta(days=7)}).count()
            prev7 = qs.filter(
                **{f'{date_field}__gte': day - timedelta(days=14),
                   f'{date_field}__lt': day - timedelta(days=7)}
            ).count()
            pct = ((last7 - prev7) / prev7 * 100) if prev7 else (100.0 if last7 else 0.0)
            return last7, round(pct, 1)

        total_users = User.objects.count()
        total_artifacts = Artifact.objects.count()
        total_reviews = Review.objects.count()
        reviewed = Artifact.objects.filter(reviews__isnull=False).distinct().count()
        coverage = round(reviewed / total_artifacts * 100, 1) if total_artifacts else 0.0
        overall_avg = Review.objects.aggregate(a=Avg('score'))['a']

        new_users, users_pct = window_change(User.objects.all(), 'date_joined')
        wk_reviews, reviews_pct = window_change(Review.objects.all())
        wk_artifacts, artifacts_pct = window_change(Artifact.objects.all())

        context.update({
            'kpi_users': total_users,
            'kpi_users_change': users_pct,
            'kpi_sessions': total_reviews,
            'kpi_sessions_change': reviews_pct,
            'kpi_conversion': coverage,
            'kpi_artifacts': total_artifacts,
            'kpi_artifacts_change': artifacts_pct,
            'kpi_avg': round(float(overall_avg), 2) if overall_avg else None,
            'trend_labels': json.dumps(labels),
            'trend_reviews': json.dumps(per_day(Review.objects.all())),
            'trend_artifacts': json.dumps(per_day(Artifact.objects.all())),
            'trend_users': json.dumps(per_day(User.objects.all(), 'date_joined')),
            'my_artifacts_count': Artifact.objects.filter(author=request.user).count(),
            'reviews_given': request.user.reviews_given.count(),
        })
        agg = average_received(request.user)
        context['received_count'] = agg['review_count']
        context['received_avg'] = agg['avg_score']
        context['badges_count'] = BadgeAward.objects.filter(user=request.user).count()

        skill_rows = list(
            Artifact.objects.values('skill__name')
            .annotate(c=Count('id')).order_by('-c')[:6]
        )
        context['skill_labels'] = json.dumps([r['skill__name'] or 'Unassigned' for r in skill_rows])
        context['skill_counts'] = json.dumps([r['c'] for r in skill_rows])

        rows = []
        for a in (
            Artifact.objects.select_related('author', 'skill')
            .annotate(review_count=Count('reviews'), avg_score=Avg('reviews__score'))
            .order_by('-created_at')[:25]
        ):
            avg = float(a.avg_score) if a.avg_score is not None else None
            if avg is not None and avg >= 4.0 and a.review_count >= 3:
                status = 'Verified'
            elif a.review_count > 0:
                status = 'In Review'
            else:
                status = 'Pending'
            rows.append({
                'title': a.title, 'author': a.author.username,
                'skill': a.skill.name if a.skill else '—',
                'reviews': a.review_count,
                'avg': round(avg, 2) if avg is not None else None,
                'status': status,
                'created': a.created_at.strftime('%Y-%m-%d'),
            })
        context['analytics_rows'] = rows

        context['recent_artifacts'] = list(
            Artifact.objects.filter(author=request.user)
            .select_related('skill')
            .annotate(avg_score=Avg('reviews__score'), review_count=Count('reviews'))
            .order_by('-created_at')[:5]
        )
        context['queue_preview'] = list(review_queue_for(request.user, limit=5))
    except Exception:
        pass
    return render(request, 'home.html', context)


@require_http_methods(['GET', 'POST'])
def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'GET':
        return render(request, 'auth/register.html')

    form_data = {
        'username': request.POST.get('username', ''),
        'first_name': request.POST.get('first_name', ''),
        'last_name': request.POST.get('last_name', ''),
        'email': request.POST.get('email', ''),
        'password': request.POST.get('password', ''),
        'confirm_password': request.POST.get('confirm_password', ''),
    }

    errors = _validate_registration_data(form_data)
    if errors:
        return render(request, 'auth/register.html', {'errors': errors, 'form_data': form_data})

    user = User.objects.create_user(
        username=form_data['username'].strip(),
        first_name=form_data['first_name'].strip(),
        last_name=form_data['last_name'].strip(),
        email=form_data['email'].strip(),
        password=form_data['password'],
    )
    login(request, user)
    return redirect('dashboard')
