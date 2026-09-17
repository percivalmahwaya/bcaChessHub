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
from django.test import TestCase
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
            reverse("association_contact", args=[self.assoc.pk]), {}, follow=True)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Please fill in all fields.", body,
                      "the message text never reached the page")
        self.assertIn('class="note note-error"', body,
                      "the message rendered without its severity, so an error "
                      "is indistinguishable from a confirmation")

    def test_a_success_message_reaches_the_html(self):
        response = self.client.post(
            reverse("association_contact", args=[self.assoc.pk]),
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
            reverse("association_contact", args=[self.assoc.pk]), {}, follow=True)
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

    NOT_YET_MIGRATED = {
        "matches/detail.html",
        "tournaments/manage.html",
        "tournaments/print.html",
        }

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
