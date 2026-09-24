"""
Tests for the site shell: the things every page depends on.

These are not tests of a feature. They are tests of the frame around every
feature, which is exactly the kind of thing that breaks without anyone
noticing because no single page is obviously wrong.
"""
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import constants as message_levels
from django.contrib.messages import get_messages
from django.template.loader import get_template
from django.conf import settings
from django.template import loader
from django.test import TestCase, override_settings
from django.urls import reverse

from associations.models import Association


class EveryTemplateParses(TestCase):
    """Compile every template in the project.

    THE BUG THIS EXISTS FOR: tournaments/round.html contained
    `{% with x=a and b == c %}`. Django's `with` tag supports no boolean
    operators and no comparisons, so it failed to parse, its `{% endwith %}`
    became an unknown block tag, and EVERY round pairings page returned a hard
    500. The page a player opens on a Saturday to find their board number.

    It survived because a template is only compiled when something renders it,
    and nothing did: no test covered the view, and the URL is /rounds/<n>/
    rather than /round/<n>/, so a manual check got a 404 and looked merely
    missing rather than broken.

    This compiles all of them, so a syntax error fails here instead of in
    front of a player in the NUST hall.
    """

    def test_all_templates_compile(self):
        root = settings.BASE_DIR / "templates"
        broken = []
        for path in sorted(root.rglob("*.html")):
            name = path.relative_to(root).as_posix()
            try:
                get_template(name)
            except Exception as exc:
                broken.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(broken, [], "templates that will 500 on render:\n"
                                     + "\n".join(broken))


class MessagesAreActuallyDisplayed(TestCase):
    """base.html must render Django messages.

    THE BUG THIS EXISTS FOR: until 2026-09-15 no template in this project
    rendered messages at all. There were 85 messages.success and
    messages.error calls across the views and every one of them was written
    into the session and thrown away unread.

    So a tournament director pasting a bad Lichess URL got a page that looked
    exactly like success. Someone submitting an empty contact form got the
    form back with no explanation. Nothing was broken in a way any test or
    any log could see, because the messages framework was working perfectly:
    it was storing messages nobody ever read.
    """

    def setUp(self):
        self.assoc = Association.objects.create(
            name="Bulawayo Chess Association", city="Bulawayo")

    def test_an_error_message_reaches_the_html(self):
        """The whole point. If this fails, users are flying blind again."""
        response = self.client.post(
            reverse("club_contact", args=[self.assoc.pk]), {}, follow=True)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Please fill in all fields.", body,
                      "the message text never reached the page")
        self.assertIn('class="note note-error"', body,
                      "the message rendered without its severity, so an error "
                      "is indistinguishable from a confirmation")

    def test_a_success_message_reaches_the_html(self):
        response = self.client.post(
            reverse("club_contact", args=[self.assoc.pk]),
            # The textarea is named `body`, not `message`. Getting that wrong
            # sends an incomplete form, which the view correctly rejects, and
            # the test then "proves" success messages do not work.
            {"name": "Percival", "email": "p@example.com",
             "subject": "Joining", "body": "How do I join?"},
            follow=True)

        body = response.content.decode()
        self.assertIn("note note-success", body)
        self.assertIn("has been sent", body)

    def test_no_page_renders_its_own_duplicate_message_block(self):
        """base.html owns messages. A page copy shows every message twice.

        Nine templates had their own copy before base.html took the job. They
        were removed by strip_local_messages.py; this stops one coming back.
        """
        offenders = []
        for path in (settings.BASE_DIR / "templates").rglob("*.html"):
            if path.name == "base.html":
                continue
            if re.search(r"\{%\s*for\s+\w+\s+in\s+messages\s*%\}",
                         path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        self.assertEqual(offenders, [],
                         f"these render messages a second time: {offenders}")

    def test_severity_survives_into_the_class_name(self):
        """An error and a confirmation must not look the same.

        The template uses message.tags. If that ever becomes a bare `note`,
        colour stops carrying meaning and the one moment a reader must not
        miss looks like every other notice.
        """
        response = self.client.post(
            reverse("club_contact", args=[self.assoc.pk]), {}, follow=True)
        stored = list(get_messages(response.wsgi_request))
        self.assertTrue(stored, "no message was stored at all")
        self.assertEqual(stored[0].level, message_levels.ERROR)

    def test_pages_without_messages_render_no_empty_notice_box(self):
        """An empty `notes` container on every page is visual noise."""
        response = self.client.get(reverse("home"))
        self.assertNotIn('class="wrap notes"', response.content.decode())


class ShellRenders(TestCase):
    """The masthead and footer are on every page, so a break is total."""

    def test_home_renders_for_a_stranger(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Bulawayo", body)
        self.assertIn("bch.css", body, "the design system is not loaded")

    def test_security_page_is_public(self):
        response = self.client.get(reverse("security"))
        self.assertEqual(response.status_code, 200)

    def test_navigation_differs_for_a_signed_in_member(self):
        User = get_user_model()
        User.objects.create_user(username="percival", password="not-a-real-pw")
        self.client.login(username="percival", password="not-a-real-pw")

        body = self.client.get(reverse("home")).content.decode()
        self.assertIn("Log out", body)
        self.assertNotIn(">Sign up<", body)


class HouseRules(TestCase):
    """The five rules Percival gave for this interface, enforced.

    These are taste, not correctness, which is exactly why they need a test.
    Nothing breaks when an emoji creeps back into a heading, so nothing stops
    it, and six months later the site looks like every other site. The rules
    live at the top of static/css/bch.css:

        no em dashes in interface copy
        no emoji used as icons
        no gradients at all
        no centred hero with a gradient background and two buttons
        no row of three feature cards each with an icon in a circle

    NOT_YET_MIGRATED shrinks as templates move onto the design system. It is a
    worklist that fails if it lies in either direction: a template on it that
    is already clean must come off, so the list cannot rot into a permanent
    excuse.
    """

    # Pictographs: emoji blocks, dingbats, arrows, and the chess piece glyphs.
    # Chess glyphs are arguable on a chess site, but they fall back to empty
    # boxes on the mid-range Android handsets this site is actually opened on,
    # which is worse than a word.
    PICTOGRAPH = re.compile(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
        "\U0000FE0F\U00002B00-\U00002BFF\U00002190-\U000021FF♔-♟]")

    # EMPTY, as of 2026-09-17. Every template in this project is on the design
    # system and Bootstrap is gone from the repository. Leave this set here
    # rather than deleting it: it is the thing that makes the two rules below
    # apply to everything, and the "already clean" half of the check means a
    # name put back in without cause fails immediately.
    NOT_YET_MIGRATED = set()

    # Email is a different medium. Mail clients strip stylesheets, so those
    # templates carry inline styles by necessity and are excluded from the
    # design system entirely. They are still held to the emoji rule elsewhere.
    EXCLUDED = {"email/"}

    def _templates(self):
        root = settings.BASE_DIR / "templates"
        for path in sorted(root.rglob("*.html")):
            name = path.relative_to(root).as_posix()
            if any(name.startswith(prefix) for prefix in self.EXCLUDED):
                continue
            yield name, path.read_text(encoding="utf-8")

    def test_migrated_templates_use_no_pictographs(self):
        offenders = {}
        for name, text in self._templates():
            if name in self.NOT_YET_MIGRATED:
                continue
            found = sorted({f"U+{ord(c):04X}" for c in self.PICTOGRAPH.findall(text)})
            if found:
                offenders[name] = found
        self.assertEqual(offenders, {},
                         "emoji or glyph icons in migrated templates. Use a "
                         f"word: {offenders}")

    def test_the_worklist_does_not_lie(self):
        """A template on the list that is already clean must come off it."""
        stale = []
        for name, text in self._templates():
            if name in self.NOT_YET_MIGRATED and not self.PICTOGRAPH.search(text):
                stale.append(name)
        self.assertEqual(stale, [],
                         "these are clean and should be removed from "
                         f"NOT_YET_MIGRATED: {stale}")

    def test_no_em_dashes_in_migrated_templates(self):
        offenders = []
        for name, text in self._templates():
            if name in self.NOT_YET_MIGRATED:
                continue
            if "—" in text:
                offenders.append(name)
        self.assertEqual(offenders, [],
                         f"em dashes in interface copy: {offenders}. Use a "
                         "comma, a colon, or two sentences.")

    def test_the_stylesheet_still_carries_the_rules(self):
        css = (settings.BASE_DIR / "static" / "css" / "bch.css").read_text(encoding="utf-8")
        self.assertIn("HOUSE RULES", css,
                      "the rules must survive in the file, not in memory")


class DjangoCommentsDoNotLeak(TestCase):
    """`{# ... #}` is SINGLE LINE ONLY, and a multi-line one ships to the user.

    Django's inline comment tag is lexed line by line. Open one, write four
    lines of explanation, close it, and the whole thing is emitted verbatim
    into the response: developer notes about workarounds and old bugs,
    delivered to every visitor and paid for out of their data bundle.

    This has now happened twice in this project, during the original redesign
    and again while migrating tournaments/detail.html, where a note reading
    "the old version hid these behind a Bootstrap dropdown" was served to the
    public. Twice is a pattern, so it gets a test rather than another
    resolution to remember.

    Use {% comment %}{% endcomment %} for anything spanning lines.
    """

    def test_no_multiline_inline_comments(self):
        offenders = []
        root = settings.BASE_DIR / "templates"
        for path in sorted(root.rglob("*.html")):
            name = path.relative_to(root).as_posix()
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if "{#" in line and "#}" not in line.split("{#", 1)[1]:
                    offenders.append(f"{name}:{number}")
        self.assertEqual(
            offenders, [],
            "multi-line {# #} comments are rendered into the page for every "
            f"visitor to download. Use {{% comment %}}: {offenders}")


class ErrorPagesTest(TestCase):
    """404 and 500.

    Django served a bare white page reading "Not Found" until 2026-09-18, on a
    site that now 301-redirects two old URL prefixes, so wrong URLs are live
    traffic rather than a hypothetical.
    """

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_a_missing_page_gets_the_real_404(self):
        response = self.client.get('/no-such-page-exists/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'That page is not here', status_code=404)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_the_404_offers_somewhere_to_go(self):
        """A 404 that only apologises leaves the reader where they were."""
        response = self.client.get('/no-such-page-exists/')
        body = response.content.decode()
        for destination in ['/rankings/', '/tournaments/', '/clubs/', '/news/']:
            self.assertIn(destination, body,
                          f'the 404 should offer {destination}')

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_the_404_still_has_the_navigation(self):
        """page_not_found renders WITH the request, so context processors run
        and the masthead works. That is what lets 404.html extend base.html
        while 500.html must not."""
        response = self.client.get('/no-such-page-exists/')
        self.assertContains(response, 'Bulawayo', status_code=404)

    def test_the_500_page_renders_with_no_context_whatsoever(self):
        """THE PROPERTY THAT MATTERS.

        Django's server_error handler renders this template WITHOUT the
        request, so context processors never run. If the page depended on
        `user` or the unread notification count it would fail at exactly the
        moment it is needed and the visitor would get the blank page this
        exists to replace. Verified by sabotage: making it extend base.html
        produced a page with an EMPTY BODY and no error message at all.
        """
        html = loader.get_template('500.html').render()
        self.assertIn('Something went wrong', html)
        self.assertIn('Bulawayo Chess Hub', html)

    def test_the_500_page_does_not_extend_base(self):
        """A 500 often means the DATABASE is unreachable, and base.html's
        context processor runs a query. An error page that queries the
        database is an error page that fails when it is most needed.

        Asserted on the source rather than the output, because a rendered
        page cannot tell you why it worked.
        """
        source = (settings.BASE_DIR / 'templates' / '500.html').read_text(
            encoding='utf-8')
        self.assertNotIn('{% extends', source, '500.html must stand alone')
        self.assertNotIn('unread_notification_count', source)

    def test_the_500_page_carries_its_own_colours(self):
        """The stylesheet is linked and will very likely load, but the page
        must be readable if it does not."""
        source = (settings.BASE_DIR / 'templates' / '500.html').read_text(
            encoding='utf-8')
        self.assertIn('<style>', source)
        self.assertIn('#b85c38', source, 'the terracotta, written out')


class SkipLinkTest(TestCase):
    """Skip to content.

    It became more necessary on 2026-09-17, when the masthead was made sticky:
    a keyboard user now tabs through six navigation links on every page before
    reaching what they came for.
    """

    def test_it_is_the_first_thing_in_the_body(self):
        body = self.client.get('/').content.decode()
        start = body.index('<body>')
        self.assertLess(body.index('class="skip"', start),
                        body.index('<header', start),
                        'a skip link after the header skips nothing')

    def test_it_points_at_a_target_that_exists(self):
        body = self.client.get('/').content.decode()
        self.assertIn('href="#content"', body)
        self.assertIn('id="content"', body)

    def test_the_target_can_receive_focus(self):
        """Without tabindex the browser scrolls to the anchor but focus stays
        in the header, so the next Tab drops the reader straight back into the
        navigation they just skipped."""
        body = self.client.get('/').content.decode()
        self.assertIn('id="content" tabindex="-1"', body)

    def test_it_is_hidden_by_position_not_by_display(self):
        """display:none and visibility:hidden both remove an element from the
        tab order, which is the one thing this exists to be in."""
        css = (settings.BASE_DIR / 'static' / 'css' / 'bch.css').read_text(
            encoding='utf-8')
        skip = css[css.index('.skip {'):css.index('.skip:focus')]
        self.assertIn('position: absolute', skip)
        self.assertNotIn('display: none', skip)
        self.assertNotIn('visibility: hidden', skip)


class SiteSearchTest(TestCase):
    """Site search.

    The dangerous bug in a search box is not a missing result. It is an extra
    one: something the searcher was never allowed to see, surfaced because a
    visibility rule lives in a view somewhere and the search query was written
    from the model instead.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from members.models import Member
        from news.models import Article
        from tournaments.models import Tournament
        import datetime

        self.club = Association.objects.create(
            name='Bulawayo Chess Association', city='Bulawayo',
            email='bca@example.com')
        other = Association.objects.create(
            name='Gweru Chess Club', city='Gweru', email='gweru@example.com')

        user = User.objects.create_user('tendai', password='x',
                                        first_name='Tendai', last_name='Moyo')
        self.member = Member.objects.create(user=user, association=self.club,
                                            is_active=True)
        gone = User.objects.create_user('retired', password='x',
                                        first_name='Tendai', last_name='Ncube')
        self.inactive = Member.objects.create(user=gone, association=self.club,
                                              is_active=False)

        today = timezone.now().date()
        self.tournament = Tournament.objects.create(
            name='Bulawayo Open', association=self.club, location='Bulawayo',
            start_date=today, end_date=today)

        now = timezone.now()
        self.live = Article.objects.create(
            title='Bulawayo Open results', slug='open-results',
            source='BCA', summary='Who won', body='The full report',
            is_published=True, published_at=now - datetime.timedelta(days=1))
        self.draft = Article.objects.create(
            title='Bulawayo Open draft', slug='open-draft',
            source='BCA', summary='Not ready', body='Secret',
            is_published=False, published_at=now - datetime.timedelta(days=1))
        self.embargoed = Article.objects.create(
            title='Bulawayo Open preview', slug='open-preview',
            source='BCA', summary='Saturday', body='Embargoed',
            is_published=True, published_at=now + datetime.timedelta(days=3))

    def _titles(self, groups):
        return [i.title for g in groups if g['kind'] == 'news' for i in g['items']]

    # ---------------------------------------------------------- visibility

    def test_an_unpublished_article_is_never_returned(self):
        from core.search import search
        groups, _ = search('Bulawayo Open')
        titles = self._titles(groups)
        self.assertIn('Bulawayo Open results', titles)
        self.assertNotIn('Bulawayo Open draft', titles)

    def test_a_future_dated_article_is_never_returned(self):
        """THE GATE THAT IS EASY TO MISS.

        An editor writes up a Saturday event on Thursday, marks it published
        and dates it forward. Filtering on is_published alone returns only
        articles somebody deliberately published, looks entirely correct, and
        leaks Saturday's results on Thursday. The home page had to relearn
        this on 2026-09-18.
        """
        from core.search import search
        groups, _ = search('Bulawayo Open')
        self.assertNotIn('Bulawayo Open preview', self._titles(groups))

    def test_an_inactive_member_is_not_returned(self):
        from core.search import search
        groups, _ = search('Tendai')
        found = [i for g in groups if g['kind'] == 'player' for i in g['items']]
        self.assertEqual(len(found), 1, 'the deactivated member leaked')

    # ------------------------------------------------------------ matching

    def test_it_finds_a_player_by_either_name(self):
        from core.search import search
        for term in ('Tendai', 'Moyo', 'tendai'):
            groups, total = search(term)
            self.assertTrue(any(g['kind'] == 'player' for g in groups),
                            term + ' found nobody')

    def test_it_finds_a_tournament_by_name_and_by_place(self):
        from core.search import search
        for term in ('Bulawayo Open', 'Bulawayo'):
            groups, _ = search(term)
            self.assertTrue(any(g['kind'] == 'tournament' for g in groups), term)

    def test_it_finds_a_club_by_city(self):
        from core.search import search
        groups, _ = search('Gweru')
        clubs = [i.name for g in groups if g['kind'] == 'club' for i in g['items']]
        self.assertIn('Gweru Chess Club', clubs)

    def test_it_searches_article_bodies_not_only_titles(self):
        from core.search import search
        groups, _ = search('full report')
        self.assertIn('Bulawayo Open results', self._titles(groups))

    # --------------------------------------------------------------- shape

    def test_a_one_character_query_returns_nothing(self):
        """A single letter matches most of the site. Returning all of it is
        not a search result, it is a page dump with a text box above it."""
        from core.search import search
        groups, total = search('a')
        self.assertEqual((groups, total), ([], 0))

    def test_an_empty_query_returns_nothing(self):
        from core.search import search
        for empty in ('', '   ', None):
            self.assertEqual(search(empty), ([], 0))

    def test_empty_groups_are_dropped(self):
        """A results page listing four headings with nothing under three of
        them is three headings of noise."""
        from core.search import search
        groups, _ = search('Gweru')
        self.assertTrue(all(g['items'] for g in groups))

    # ---------------------------------------------------------------- page

    def test_the_page_answers_and_is_linkable(self):
        response = self.client.get(reverse('search') + '?q=Bulawayo')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bulawayo Open')

    def test_the_page_works_with_no_query_at_all(self):
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)

    def test_a_search_that_matches_nothing_says_so(self):
        response = self.client.get(reverse('search') + '?q=zzzznothing')
        self.assertContains(response, 'Nothing matches')

    def test_a_too_short_query_explains_itself(self):
        response = self.client.get(reverse('search') + '?q=a')
        self.assertContains(response, 'at least 2 characters')

    def test_the_form_is_a_get_so_a_result_is_a_url(self):
        """A POST search cannot be bookmarked, mailed or reached with the back
        button, and this site works without JavaScript everywhere else."""
        response = self.client.get(reverse('search'))
        body = response.content.decode()
        self.assertIn('method="get"', body)
        self.assertNotIn('method="post"', body)

    def test_search_reaches_every_page_through_the_masthead(self):
        response = self.client.get('/')
        self.assertContains(response, reverse('search'))

    def test_a_query_containing_html_is_escaped(self):
        response = self.client.get(reverse('search') + '?q=<script>alert(1)</script>')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_the_search_field_has_a_label(self):
        """Hidden from sight, not from a screen reader. A bare input in a
        search landmark is announced as "edit text, blank"."""
        response = self.client.get(reverse('search'))
        self.assertContains(response, 'for="q"')
        self.assertContains(response, 'sr-only')

    def test_the_hidden_label_is_still_in_the_accessibility_tree(self):
        """display:none and visibility:hidden both remove an element from the
        accessibility tree, which defeats the only reason this class exists.
        Same trap the skip link avoids by moving offscreen instead."""
        css = (settings.BASE_DIR / 'static' / 'css' / 'bch.css').read_text(encoding='utf-8')
        rule = css[css.index('.sr-only {'):]
        rule = rule[:rule.index('}')]
        self.assertIn('position: absolute', rule)
        self.assertNotIn('display: none', rule)
        self.assertNotIn('visibility: hidden', rule)
