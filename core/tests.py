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
