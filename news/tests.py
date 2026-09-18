import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from associations.models import Association

from .models import Article


def make_article(**kwargs):
    defaults = {
        'title': 'Bulawayo Open concludes',
        'summary': 'Forty players over two days at the City Hall.',
        'source': 'Bulawayo Chess Hub',
        'is_published': True,
    }
    defaults.update(kwargs)
    return Article.objects.create(**defaults)


class WhatIsVisibleTest(TestCase):
    """Two separate gates, and only one of them is obvious."""

    def test_a_published_item_appears(self):
        make_article()
        response = self.client.get(reverse('news_list'))
        self.assertContains(response, 'Bulawayo Open concludes')

    def test_an_unpublished_item_does_not(self):
        """Unpublished is the DEFAULT, so a half written item cannot appear by
        accident on the way to being finished."""
        make_article(title='Draft, not ready', is_published=False)
        response = self.client.get(reverse('news_list'))
        self.assertNotContains(response, 'Draft, not ready')

    def test_a_future_dated_item_is_held_back(self):
        """THE ONE THAT FAILS QUIETLY.

        published_at may be in the future so an item written on Thursday
        appears on Saturday. Filtering on is_published alone would publish it
        the instant it was saved, which is the exact opposite of what setting
        a future date means, and nothing would look wrong: the item is there,
        it is just there early.
        """
        make_article(title='Saturday results',
                     published_at=timezone.now() + datetime.timedelta(days=2))
        response = self.client.get(reverse('news_list'))
        self.assertNotContains(response, 'Saturday results')

    def test_a_future_dated_item_is_not_reachable_by_its_own_url_either(self):
        """Hiding it from the list while leaving the page open is not hiding
        it. Slugs are guessable from a title."""
        article = make_article(
            title='Saturday results',
            published_at=timezone.now() + datetime.timedelta(days=2))
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertEqual(response.status_code, 404)

    def test_an_unpublished_item_is_not_reachable_by_its_own_url_either(self):
        article = make_article(title='Draft', is_published=False)
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertEqual(response.status_code, 404)

    def test_newest_first(self):
        old = make_article(title='Older item',
                           published_at=timezone.now() - datetime.timedelta(days=10))
        new = make_article(title='Newer item')
        body = self.client.get(reverse('news_list')).content.decode()
        self.assertLess(body.index(new.title), body.index(old.title))


class SlugTest(TestCase):
    def test_a_slug_is_made_from_the_title(self):
        article = make_article(title='ZCF opens doors for partnerships')
        self.assertEqual(article.slug, 'zcf-opens-doors-for-partnerships')

    def test_two_items_with_the_same_title_do_not_collide(self):
        """Two clubs posting "Weekend Results" in the same month is the normal
        case, not an edge case."""
        first = make_article(title='Weekend results')
        second = make_article(title='Weekend results')
        self.assertNotEqual(first.slug, second.slug)
        self.assertEqual(second.slug, 'weekend-results-2')

    def test_a_title_with_no_usable_characters_still_gets_a_slug(self):
        article = make_article(title='...')
        self.assertTrue(article.slug, 'an empty slug would be an unreachable page')


class AttributionTest(TestCase):
    """Relaying somebody else's announcement without crediting it is the
    failure mode this section could easily have."""

    def test_a_relayed_item_links_back_to_the_original(self):
        article = make_article(
            title='Chess South Africa statement',
            source='Chess South Africa',
            source_url='https://example.org/statement')
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertContains(response, 'https://example.org/statement')
        self.assertContains(response, 'Read the original')

    def test_an_item_written_here_claims_no_outside_source(self):
        article = make_article(title='Our own report')
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertNotContains(response, 'Read the original')

    def test_the_byline_is_always_shown(self):
        article = make_article(source='Zimbabwe Chess Federation')
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertContains(response, 'Zimbabwe Chess Federation')


class BodyTest(TestCase):
    def test_blank_lines_become_separate_paragraphs(self):
        article = make_article(body='First para.\n\nSecond para.\n\n\nThird.')
        self.assertEqual(article.paragraphs,
                         ['First para.', 'Second para.', 'Third.'])

    def test_an_empty_body_is_allowed(self):
        """A short notice does not need a page of prose behind it."""
        article = make_article(body='')
        self.assertEqual(article.paragraphs, [])
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertEqual(response.status_code, 200)

    def test_markup_typed_into_the_body_is_shown_as_text_not_rendered(self):
        """The body is written in the Django admin by a club administrator,
        not by a developer. Splitting paragraphs in Python rather than with a
        template filter is what keeps it escaped."""
        article = make_article(body='<script>alert(1)</script>')
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')


class ClubScopeTest(TestCase):
    def setUp(self):
        self.club = Association.objects.create(
            name='Bulawayo Chess Club', city='Bulawayo', email='bcc@test.zw')

    def test_an_item_can_name_the_club_it_concerns(self):
        article = make_article(title='Club night moves to Thursday',
                               club=self.club)
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertContains(response, 'Bulawayo Chess Club')

    def test_an_item_with_no_club_is_site_wide_and_still_shows(self):
        article = make_article(club=None)
        response = self.client.get(reverse('news_detail', args=[article.slug]))
        self.assertEqual(response.status_code, 200)

    def test_deleting_a_club_does_not_delete_its_news(self):
        """SET_NULL, not CASCADE. A club folding is not a reason to erase the
        record that it existed and what it did."""
        article = make_article(club=self.club)
        self.club.delete()
        article.refresh_from_db()
        self.assertIsNone(article.club)
        self.assertEqual(
            self.client.get(reverse('news_detail', args=[article.slug])).status_code,
            200)


class HomePageNewsTest(TestCase):
    """The front page carries the latest items.

    The news section shipped on 2026-09-17 and nothing linked to it from the
    home page, which had been sitting on a hardcoded "Nothing published yet"
    since the redesign regardless of what was actually published.
    """

    def test_a_published_item_reaches_the_front_page(self):
        make_article(title='Bulawayo Open concludes')
        response = self.client.get('/')
        self.assertContains(response, 'Bulawayo Open concludes')
        self.assertContains(response, 'All news')

    def test_the_front_page_respects_the_future_date_too(self):
        """A home page quietly bypassing the gate would leak Saturday's
        results onto the front page on Thursday, which is the one place it
        would be seen fastest."""
        make_article(title='Saturday results',
                     published_at=timezone.now() + datetime.timedelta(days=2))
        response = self.client.get('/')
        self.assertNotContains(response, 'Saturday results')

    def test_an_unpublished_item_does_not_reach_the_front_page(self):
        make_article(title='Still a draft', is_published=False)
        response = self.client.get('/')
        self.assertNotContains(response, 'Still a draft')

    def test_the_empty_state_still_shows_when_there_is_no_news(self):
        response = self.client.get('/')
        self.assertContains(response, 'Nothing published yet')
