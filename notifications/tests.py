"""
Tests for the notification system.

This was the last app in the project with none, and it is the one about to
start sending REAL email: everything here currently goes to a console backend
that nobody reads, and the moment Brevo is configured it reaches actual
inboxes. Untested code that is about to email a club's members is the wrong
thing to leave alone.

Written the same way the payments tests were on 2026-09-13, which is to say
mostly as attacks and as "prove the thing it claims to do".
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from associations.models import Association
from members.models import Member

from .email import _send, _html_to_plain
from .models import Notification


def make_member(username, assoc=None, email=None):
    if assoc is None:
        # get_or_create, not create: Association.email is unique, so a second
        # member built by this helper was failing on the constraint rather
        # than joining the same club.
        assoc, _ = Association.objects.get_or_create(
            email='club@test.zw',
            defaults={'name': 'Test Club', 'city': 'Bulawayo'})
    user = User.objects.create_user(
        username=username, password='pass1234',
        email=email if email is not None else f'{username}@test.zw',
        first_name=username.title(), last_name='Test')
    return Member.objects.create(user=user, association=assoc)


class NotificationCentreTest(TestCase):
    """The page a member opens to see what they missed.

    THE BUG THIS EXISTS FOR: the view marked everything read BEFORE rendering,
    using the same queryset it was about to display. Under the "Unread" filter
    that queryset is is_read=False, so the update emptied the very set the
    paginator then evaluated, and the page came back blank every time. With no
    filter it still destroyed the read and unread distinction before the
    reader could see it, so nothing could ever be highlighted as new.

    Exactly the same shape as the dashboard bug found on 2026-09-16, which
    called .update() and then evaluated the same lazy queryset.
    """

    def setUp(self):
        self.member = make_member('alice')
        self.client.force_login(self.member.user)
        self.unread = [
            Notification.objects.create(
                recipient=self.member.user, message=f'Unread item {i}',
                is_read=False)
            for i in range(3)
        ]
        self.read = Notification.objects.create(
            recipient=self.member.user, message='Old item', is_read=True)

    def test_the_unread_filter_actually_shows_the_unread_items(self):
        """It showed an empty page: the update emptied the set being paged."""
        response = self.client.get(reverse('notification_centre'),
                                   {'read': 'unread'})
        for item in self.unread:
            self.assertContains(response, item.message)

    def test_opening_the_centre_shows_which_ones_were_new(self):
        """Marking them read before rendering means the page can never
        distinguish what the reader had not seen yet, which is the single
        thing they opened it to find out."""
        response = self.client.get(reverse('notification_centre'))
        shown = list(response.context['page_obj'].object_list)
        was_unread = [n for n in shown if n.pk in {u.pk for u in self.unread}]
        self.assertEqual(len(was_unread), 3)
        self.assertTrue(
            any(not n.is_read for n in shown),
            'every item was already marked read by the time it reached the '
            'template, so nothing could be highlighted as new')

    def test_they_are_marked_read_afterwards(self):
        """The behaviour is still wanted, just not before rendering."""
        self.client.get(reverse('notification_centre'))
        self.assertEqual(
            Notification.objects.filter(recipient=self.member.user,
                                        is_read=False).count(), 0)

    def test_the_read_filter_shows_read_items(self):
        response = self.client.get(reverse('notification_centre'),
                                   {'read': 'read'})
        self.assertContains(response, 'Old item')

    def test_filtering_by_type(self):
        Notification.objects.create(
            recipient=self.member.user, type='payment_received',
            message='A payment cleared')
        response = self.client.get(reverse('notification_centre'),
                                   {'type': 'payment_received'})
        self.assertContains(response, 'A payment cleared')
        self.assertNotContains(response, 'Unread item 0')

    def test_a_member_never_sees_somebody_elses_notifications(self):
        """The whole point of a recipient field."""
        other = make_member('mallory')
        Notification.objects.create(
            recipient=other.user, message='PRIVATE TO MALLORY')
        response = self.client.get(reverse('notification_centre'))
        self.assertNotContains(response, 'PRIVATE TO MALLORY')

    def test_marking_all_read_does_not_touch_anybody_elses(self):
        other = make_member('mallory')
        theirs = Notification.objects.create(
            recipient=other.user, message='Still unread', is_read=False)
        self.client.post(reverse('mark_all_read'))
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read,
                         'one member marking their own as read must not '
                         'reach into another member inbox')

    def test_mark_all_read_refuses_a_get(self):
        """A state change behind a GET is a state change a link preview or a
        prefetching browser can trigger on somebody's behalf."""
        response = self.client.get(reverse('mark_all_read'))
        self.assertEqual(response.status_code, 405)

    def test_the_centre_requires_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('notification_centre'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])


class UnreadBadgeTest(TestCase):
    """The count in the navigation bar, from notifications.context_processors."""

    def setUp(self):
        self.member = make_member('alice')

    # Read on the ratings page, NOT the dashboard. The dashboard marks
    # notifications read on purpose, so a zero there is correct behaviour and
    # testing the badge on it proves nothing about the badge.
    BADGE_PAGE = 'rankings'

    def test_the_badge_counts_only_unread(self):
        Notification.objects.create(recipient=self.member.user,
                                    message='a', is_read=False)
        Notification.objects.create(recipient=self.member.user,
                                    message='b', is_read=True)
        self.client.force_login(self.member.user)
        response = self.client.get(reverse(self.BADGE_PAGE))
        self.assertEqual(response.context['unread_notification_count'], 1)

    def test_the_badge_counts_only_this_members_unread(self):
        other = make_member('mallory')
        Notification.objects.create(recipient=other.user, message='theirs')
        self.client.force_login(self.member.user)
        response = self.client.get(reverse(self.BADGE_PAGE))
        self.assertEqual(response.context['unread_notification_count'], 0)

    def test_an_anonymous_visitor_gets_zero_rather_than_an_error(self):
        """The context processor runs on EVERY page, including the public
        ones, so a logged-out visitor must not raise."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unread_notification_count'], 0)


class MarkReadTest(TestCase):
    def test_mark_read_sets_the_flag(self):
        member = make_member('alice')
        note = Notification.objects.create(recipient=member.user, message='x')
        note.mark_read()
        note.refresh_from_db()
        self.assertTrue(note.is_read)

    def test_newest_first(self):
        member = make_member('alice')
        first = Notification.objects.create(recipient=member.user, message='older')
        second = Notification.objects.create(recipient=member.user, message='newer')
        self.assertEqual(
            list(Notification.objects.filter(recipient=member.user))[0].pk,
            second.pk)


class SendTest(TestCase):
    """notifications.email._send: one email, and one in-app notification.

    This is the code about to start reaching real inboxes. Everything here
    currently goes to a console backend nobody reads, so any of it could be
    wrong and nothing would show it.
    """

    def setUp(self):
        self.with_email = make_member('haseemail')
        self.without_email = make_member('noemail', email='')

    def test_an_email_goes_out_and_a_notification_is_recorded(self):
        _send(self.with_email.user, 'Round 3 pairings',
              '<p>You are on board 4.</p>', 'match_scheduled')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['haseemail@test.zw'])
        self.assertEqual(
            Notification.objects.filter(recipient=self.with_email.user).count(), 1)

    def test_a_member_with_no_email_still_gets_the_in_app_notification(self):
        """THE BUG THIS EXISTS FOR.

        _send returned early on a missing email address, so a member without
        one got nothing at all: no mail, and no notification either. The
        notification centre exists precisely so that somebody without an email
        address still finds out they have been paired. Dropping both is the
        opposite of what a fallback is for, and on a club where juniors sign
        up without an address it is most of the members.
        """
        _send(self.without_email.user, 'Round 3 pairings',
              '<p>You are on board 4.</p>', 'match_scheduled')
        self.assertEqual(len(mail.outbox), 0, 'nothing to send to')
        self.assertEqual(
            Notification.objects.filter(recipient=self.without_email.user).count(), 1,
            'the in-app notification does not need an email address')

    def test_a_notification_with_no_email_is_not_recorded_as_emailed(self):
        _send(self.without_email.user, 'Subject', '<p>Body</p>', 'general')
        note = Notification.objects.get(recipient=self.without_email.user)
        self.assertFalse(note.email_sent)

    def test_email_sent_is_only_true_when_the_send_actually_worked(self):
        """THE SECOND BUG.

        email_sent was set to True unconditionally, while the send itself used
        fail_silently=True. So a failed delivery produced a record asserting
        it had been delivered, and nothing anywhere would say otherwise. That
        is the fifth time this project has found a step reporting success
        having done nothing.
        """
        with patch('notifications.email.EmailMultiAlternatives.send',
                   return_value=0):
            _send(self.with_email.user, 'Subject', '<p>Body</p>', 'general')
        note = Notification.objects.get(recipient=self.with_email.user)
        self.assertFalse(note.email_sent,
                         'the mail did not go, so the record must not claim '
                         'it did')

    def test_a_send_that_raises_does_not_lose_the_notification(self):
        """A mail provider being down must not swallow the thing the member
        needs to know. The in-app record is the fallback."""
        with patch('notifications.email.EmailMultiAlternatives.send',
                   side_effect=Exception('Brevo unreachable')):
            _send(self.with_email.user, 'Subject', '<p>Body</p>', 'general')
        self.assertEqual(
            Notification.objects.filter(recipient=self.with_email.user).count(), 1)
        note = Notification.objects.get(recipient=self.with_email.user)
        self.assertFalse(note.email_sent)


class HtmlToPlainTest(TestCase):
    """The plain-text part of every email.

    It matters more than it looks: a plain-text alternative that is empty or
    full of markup is what a spam filter scores, and what a text-only mail
    client shows.
    """

    def test_tags_are_stripped(self):
        self.assertNotIn('<', _html_to_plain('<p>Hello <b>there</b></p>'))

    def test_the_words_survive(self):
        plain = _html_to_plain('<p>Round 3 pairings are up.</p>')
        self.assertIn('Round 3 pairings are up.', plain)

    def test_it_does_not_return_an_empty_string_for_real_content(self):
        plain = _html_to_plain(
            '<html><body><h1>Title</h1><p>Body text here.</p></body></html>')
        self.assertTrue(plain.strip())
        self.assertIn('Body text here.', plain)
