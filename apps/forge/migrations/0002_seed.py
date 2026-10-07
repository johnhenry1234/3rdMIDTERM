"""Seed default skills, rubrics, and badge definitions (idempotent)."""

from django.db import migrations


SKILLS = [
    ('Database Design', 'database-design', 'Schema modeling, normalization, ERDs.'),
    ('Web Security', 'web-security', 'Auth, validation, secure coding.'),
    ('UX & Frontend', 'ux-frontend', 'Interface design, usability, links/demos.'),
]

RUBRICS = {
    'database-design': [
        ('Schema Quality', 'Correctness and normalization of the model.', 5),
        ('Documentation', 'Clarity of the ERD and descriptions.', 5),
    ],
    'web-security': [
        ('Code Quality', 'Readability and structure of the snippet.', 5),
        ('Security', 'Input handling, auth, and safe practices.', 5),
    ],
    'ux-frontend': [
        ('UX', 'Usability and clarity of the interface.', 5),
        ('Craft', 'Visual polish and completeness of the demo/link.', 5),
    ],
}

BADGES = [
    ('Database Architect Level 1', 'database-design', 1, 3, '4.00'),
    ('Secure Coder Level 1', 'web-security', 1, 3, '4.00'),
    ('UX Builder Level 1', 'ux-frontend', 1, 3, '4.00'),
]


def seed_forward(apps, schema_editor):
    Skill = apps.get_model('forge', 'Skill')
    Rubric = apps.get_model('forge', 'Rubric')
    BadgeDefinition = apps.get_model('forge', 'BadgeDefinition')
    for name, slug, desc in SKILLS:
        skill, _ = Skill.objects.get_or_create(
            slug=slug, defaults={'name': name, 'description': desc}
        )
        for rubric_name, rubric_desc, max_score in RUBRICS[slug]:
            Rubric.objects.get_or_create(
                skill=skill, name=rubric_name,
                defaults={'description': rubric_desc, 'max_score': max_score},
            )
    for badge_name, skill_slug, level, min_reviews, min_avg in BADGES:
        skill = Skill.objects.filter(slug=skill_slug).first()
        if skill is None:
            continue
        BadgeDefinition.objects.get_or_create(
            name=badge_name,
            defaults={
                'skill': skill, 'level': level,
                'min_reviews': min_reviews, 'min_avg': min_avg,
            },
        )


def seed_backward(apps, schema_editor):
    BadgeDefinition = apps.get_model('forge', 'BadgeDefinition')
    Rubric = apps.get_model('forge', 'Rubric')
    Skill = apps.get_model('forge', 'Skill')
    BadgeDefinition.objects.filter(name__in=[b[0] for b in BADGES]).delete()
    Skill.objects.filter(slug__in=[s[1] for s in SKILLS]).delete()


class Migration(migrations.Migration):
    dependencies = [('forge', '0001_initial')]

    operations = [migrations.RunPython(seed_forward, seed_backward)]
