from django.contrib import admin

from .models import Artifact, BadgeAward, BadgeDefinition, Review, Rubric, Skill


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'description')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Rubric)
class RubricAdmin(admin.ModelAdmin):
    list_display = ('skill', 'name', 'max_score')
    list_filter = ('skill',)
    search_fields = ('name',)


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'skill', 'artifact_type', 'is_public', 'created_at')
    list_filter = ('skill', 'artifact_type', 'is_public')
    search_fields = ('title', 'author__username')
    readonly_fields = ('share_token',)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('artifact', 'reviewer', 'rubric', 'score', 'created_at')
    list_filter = ('rubric',)
    search_fields = ('artifact__title', 'reviewer__username')


@admin.register(BadgeDefinition)
class BadgeDefinitionAdmin(admin.ModelAdmin):
    list_display = ('name', 'skill', 'level', 'min_reviews', 'min_avg')
    list_filter = ('skill',)


@admin.register(BadgeAward)
class BadgeAwardAdmin(admin.ModelAdmin):
    list_display = ('badge', 'user', 'evidence_avg', 'evidence_count', 'awarded_at')
    list_filter = ('badge',)
    search_fields = ('user__username', 'badge__name')
