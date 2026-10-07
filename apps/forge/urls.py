from django.urls import path

from . import views

app_name = 'forge'

urlpatterns = [
    # 1. Artifact submissions
    path('', views.artifact_list, name='artifact_list'),
    path('submit/', views.artifact_create, name='artifact_create'),
    path('<int:pk>/', views.artifact_detail, name='artifact_detail'),
    # 2. Blind peer evaluation matrix
    path('review/', views.review_queue, name='review_queue'),
    path('review/submit/', views.review_submit, name='review_submit'),
    # 3. Badges & progress
    path('badges/', views.badge_list, name='badge_list'),
    # 4. Public skill ledger & verification (no login required)
    path('ledger/', views.ledger, name='ledger'),
    path('u/<str:username>/', views.public_profile, name='public_profile'),
    path('p/<uuid:token>/', views.portfolio_by_token, name='portfolio'),
]
