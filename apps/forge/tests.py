import inspect

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.forge import services, views
from apps.forge.models import Artifact, BadgeAward, BadgeDefinition, Review, Rubric, Skill

User = get_user_model()


class SkillForgeTests(TestCase):
    def setUp(self):
        self.skill = Skill.objects.create(name='Test Database Skill', slug='test-db-skill')
        self.rubric = Rubric.objects.create(
            skill=self.skill, name='Test Schema Quality', max_score=5
        )
        self.badge = BadgeDefinition.objects.create(
            skill=self.skill, name='Test Architect Level 1',
            level=9, min_reviews=2, min_avg='4.00',
        )
        self.author = User.objects.create_user(username='author', password='pass12345')
        self.r1 = User.objects.create_user(username='reviewer1', password='pass12345')
        self.r2 = User.objects.create_user(username='reviewer2', password='pass12345')
        self.artifact = Artifact.objects.create(
            author=self.author, skill=self.skill, title='Sample ERD',
            artifact_type='erd', content='users(id, name)',
        )

    def test_review_is_single_isolated_post(self):
        self.client.force_login(self.r1)
        resp = self.client.post(reverse('forge:review_submit'), {
            'artifact': self.artifact.id, 'rubric': self.rubric.id,
            'score': 5, 'comment': 'Great',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Review.objects.count(), 1)

    def test_self_review_blocked(self):
        self.client.force_login(self.author)
        resp = self.client.post(reverse('forge:review_submit'), {
            'artifact': self.artifact.id, 'rubric': self.rubric.id, 'score': 5,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Review.objects.count(), 0)

    def test_duplicate_rubric_score_blocked_append_only(self):
        Review.objects.create(
            artifact=self.artifact, reviewer=self.r1, rubric=self.rubric, score=4)
        self.client.force_login(self.r1)
        resp = self.client.post(reverse('forge:review_submit'), {
            'artifact': self.artifact.id, 'rubric': self.rubric.id, 'score': 5,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(Review.objects.get().score, 4)  # immutable

    def test_aggregation_uses_avg_count(self):
        Review.objects.create(
            artifact=self.artifact, reviewer=self.r1, rubric=self.rubric, score=4)
        Review.objects.create(
            artifact=self.artifact, reviewer=self.r2, rubric=self.rubric, score=5)
        summary = services.artifact_summary(self.artifact)
        self.assertEqual(summary['total_reviews'], 2)
        self.assertAlmostEqual(float(summary['avg_score']), 4.5)

    def test_badge_awarded_when_threshold_met(self):
        Review.objects.create(
            artifact=self.artifact, reviewer=self.r1, rubric=self.rubric, score=4)
        self.assertFalse(BadgeAward.objects.filter(user=self.author).exists())
        Review.objects.create(
            artifact=self.artifact, reviewer=self.r2, rubric=self.rubric, score=5)
        award = BadgeAward.objects.filter(user=self.author, badge=self.badge).first()
        self.assertIsNotNone(award)
        self.assertEqual(award.evidence_count, 2)

    def test_public_ledger_and_portfolio_need_no_login(self):
        for url in (reverse('forge:ledger'),
                    reverse('forge:portfolio', args=[self.artifact.share_token]),
                    reverse('forge:public_profile', args=[self.author.username])):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)

    def test_review_queue_excludes_own_and_reviewed(self):
        self.client.force_login(self.r1)
        resp = self.client.get(reverse('forge:review_queue'))
        self.assertEqual(resp.status_code, 200)
        Review.objects.create(
            artifact=self.artifact, reviewer=self.r1, rubric=self.rubric, score=5)
        queue = services.review_queue_for(self.r1)
        self.assertNotIn(self.artifact, list(queue))

    def test_no_transaction_blocks_in_forge(self):
        for module in (views, services):
            source = inspect.getsource(module)
            self.assertNotIn('transaction.atomic', source)
        import apps.forge.signals as signals  # noqa
        self.assertNotIn('transaction.atomic', inspect.getsource(signals))
