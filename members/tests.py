import pyotp
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from associations.models import Association
from unittest.mock import patch
from .models import Member, RatingHistory, LichessAccount, ranked_by_lichess
from . import lichess


def make_association():
    return Association.objects.create(
        name='Test Chess Club', city='Harare', email='club@test.com'
    )


def make_user_and_member(username='alice', rating=1200, assoc=None, totp_enabled=False):
    if assoc is None:
        assoc = make_association()
    user = User.objects.create_user(
        username=username, password='pass1234', email=f'{username}@test.com',
        first_name='Alice', last_name='Test'
    )
    member = Member.objects.create(user=user, association=assoc, rating=rating)
    if totp_enabled:
        secret = pyotp.random_base32()
        member.totp_secret = secret
        member.totp_enabled = True
        member.save()
    return user, member


class SignupViewTest(TestCase):
    def setUp(self):
        self.assoc = make_association()
        self.client = Client()

    def test_signup_creates_user_and_member(self):
        response = self.client.post(reverse('signup'), {
            'username': 'newplayer',
            'first_name': 'New',
            'last_name': 'Player',
            'email': 'new@test.com',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'association': self.assoc.pk,
        })
        self.assertEqual(User.objects.filter(username='newplayer').count(), 1)
        self.assertEqual(Member.objects.filter(user__username='newplayer').count(), 1)

    def test_signup_sets_default_elo(self):
        self.client.post(reverse('signup'), {
            'username': 'newplayer',
            'first_name': 'New',
            'last_name': 'Player',
            'email': 'new@test.com',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'association': self.assoc.pk,
        })
        member = Member.objects.get(user__username='newplayer')
        self.assertEqual(member.rating, 1200)


class EloAndRatingHistoryTest(TestCase):
    def setUp(self):
        self.assoc = make_association()
        _, self.member = make_user_and_member('player1', assoc=self.assoc)

    def test_update_rating_changes_rating(self):
        self.member.update_rating(1216)
        self.member.refresh_from_db()
        self.assertEqual(self.member.rating, 1216)

    def test_update_rating_creates_history_record(self):
        self.member.update_rating(1216)
        history = RatingHistory.objects.filter(member=self.member)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().rating, 1216)
        self.assertEqual(history.first().delta, 16)

    def test_update_rating_negative_delta(self):
        self.member.update_rating(1184)
        self.member.refresh_from_db()
        self.assertEqual(self.member.rating, 1184)
        self.assertEqual(RatingHistory.objects.get(member=self.member).delta, -16)


class TwoFactorMiddlewareTest(TestCase):
    def setUp(self):
        self.assoc = make_association()
        self.user_no2fa, _ = make_user_and_member('no2fa', assoc=self.assoc)
        self.user_2fa, self.member_2fa = make_user_and_member('with2fa', assoc=self.assoc, totp_enabled=True)
        self.client = Client()

    def test_no_2fa_user_can_reach_dashboard(self):
        self.client.login(username='no2fa', password='pass1234')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_2fa_user_redirected_to_verify(self):
        self.client.login(username='with2fa', password='pass1234')
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, f"{reverse('verify_2fa')}?next={reverse('dashboard')}")

    def test_2fa_user_with_session_flag_can_reach_dashboard(self):
        self.client.login(username='with2fa', password='pass1234')
        session = self.client.session
        session['2fa_verified'] = True
        session.save()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)


class TwoFactorVerifyViewTest(TestCase):
    def setUp(self):
        self.assoc = make_association()
        self.user, self.member = make_user_and_member('with2fa', assoc=self.assoc, totp_enabled=True)
        self.client = Client()
        self.client.login(username='with2fa', password='pass1234')

    def test_correct_code_sets_session_flag(self):
        totp = pyotp.TOTP(self.member.totp_secret)
        response = self.client.post(reverse('verify_2fa'), {
            'code': totp.now(),
            'next': '',
        })
        self.assertTrue(self.client.session.get('2fa_verified'))

    def test_wrong_code_shows_error(self):
        response = self.client.post(reverse('verify_2fa'), {
            'code': '000000',
            'next': '',
        })
        self.assertFalse(self.client.session.get('2fa_verified'))
        self.assertContains(response, 'Invalid code')


class TwoFactorSetupViewTest(TestCase):
    def setUp(self):
        self.assoc = make_association()
        self.user, self.member = make_user_and_member('setup_user', assoc=self.assoc)
        self.client = Client()
        self.client.login(username='setup_user', password='pass1234')

    def test_generate_stores_secret_in_session(self):
        self.client.post(reverse('setup_2fa'), {'action': 'generate'})
        self.assertIn('pending_totp_secret', self.client.session)

    def test_confirm_with_valid_code_enables_2fa(self):
        self.client.post(reverse('setup_2fa'), {'action': 'generate'})
        secret = self.client.session['pending_totp_secret']
        code = pyotp.TOTP(secret).now()
        self.client.post(reverse('setup_2fa'), {'action': 'confirm', 'code': code})
        self.member.refresh_from_db()
        self.assertTrue(self.member.totp_enabled)
        self.assertEqual(self.member.totp_secret, secret)

    def test_confirm_with_wrong_code_does_not_enable_2fa(self):
        self.client.post(reverse('setup_2fa'), {'action': 'generate'})
        self.client.post(reverse('setup_2fa'), {'action': 'confirm', 'code': '000000'})
        self.member.refresh_from_db()
        self.assertFalse(self.member.totp_enabled)


# ---------------------------------------------------------------------------
# Lichess
# ---------------------------------------------------------------------------

class LichessExtractTest(TestCase):
    """Turning a Lichess profile into the fields we store.

    THE TRAP THIS EXISTS FOR: Lichess returns a rating for every format
    whether or not it has ever been played. An account that has never played
    rapid comes back as

        rapid: {rating: 2500, games: 0, prov: true}

    2500 is a placeholder, not a rating. Storing it would put a beginner at
    2500 rapid on a page whose entire job is showing real ratings. Verified
    against the live API before this was written.
    """

    def test_a_format_never_played_has_no_rating(self):
        data = lichess.extract({
            'id': 'someone', 'username': 'Someone',
            'perfs': {'rapid': {'rating': 2500, 'games': 0, 'prov': True}},
        })
        self.assertIsNone(data['rapid_rating'],
                          'the 2500 placeholder must never be stored')
        self.assertEqual(data['rapid_games'], 0)

    def test_a_played_format_keeps_its_rating_and_game_count(self):
        data = lichess.extract({
            'id': 'someone', 'username': 'Someone',
            'perfs': {'blitz': {'rating': 1731, 'games': 11785, 'prov': False}},
        })
        self.assertEqual(data['blitz_rating'], 1731)
        self.assertEqual(data['blitz_games'], 11785)
        self.assertFalse(data['blitz_provisional'])

    def test_provisional_travels_with_the_rating(self):
        data = lichess.extract({
            'id': 'x', 'username': 'X',
            'perfs': {'rapid': {'rating': 2617, 'games': 434, 'prov': True}},
        })
        self.assertEqual(data['rapid_rating'], 2617)
        self.assertTrue(data['rapid_provisional'],
                        'a rating shown without its provisional flag is a '
                        'claim the data does not support')

    def test_missing_sections_do_not_raise(self):
        """A brand new Lichess account has almost nothing on it."""
        data = lichess.extract({'id': 'new', 'username': 'New'})
        self.assertEqual(data['total_games'], 0)
        self.assertIsNone(data['bullet_rating'])


class LichessAccountDisplayTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='p', password='pass1234')
        self.link = LichessAccount.objects.create(
            user=self.user, lichess_id='p', username='P',
            bullet_rating=1774, bullet_games=7483,
            blitz_rating=1500, blitz_games=12, blitz_provisional=True,
            rapid_rating=None, rapid_games=0)

    def test_unplayed_formats_are_left_out_entirely(self):
        """Blitz first, because the rankings are ordered on it. Rapid is
        absent because this club rates on blitz and bullet only as of
        2026-09-17, not because this account has never played it."""
        perfs = [r['perf'] for r in self.link.ratings()]
        self.assertEqual(perfs, ['blitz', 'bullet'],
                         'a format never played should not appear as a dash, '
                         'which reads as missing data')

    def test_rapid_is_still_stored_even_though_it_is_not_shown(self):
        """Dropping it from the interface must not drop it from the record,
        or turning it back on later means refetching every account."""
        self.link.rapid_rating = 1650
        self.link.rapid_games = 40
        self.link.save()
        self.link.refresh_from_db()
        self.assertEqual(self.link.rapid_rating, 1650)
        self.assertNotIn('rapid', [r['perf'] for r in self.link.ratings()])

    def test_ranking_uses_blitz(self):
        self.assertEqual(self.link.ranking_rating, 1500)

    def test_ranking_falls_back_to_bullet_for_a_bullet_only_player(self):
        """Ranking on blitz alone would leave a pure bullet player off the
        list entirely, which is worse than ranking them on what they play."""
        self.link.blitz_rating = None
        self.link.blitz_games = 0
        self.link.save()
        self.assertEqual(self.link.ranking_rating, 1774)

    def test_a_provisional_rating_still_ranks(self):
        """Unlike best_rating, which excludes provisional ones. Leaving new
        players out of the rankings until they have thirty games would mean
        an empty ratings page for months."""
        self.assertTrue(self.link.blitz_provisional)
        self.assertIsNotNone(self.link.ranking_rating)

    def test_best_rating_ignores_provisional_ones(self):
        """A 2500 after three games is not somebody's strength."""
        best = self.link.best_rating
        self.assertEqual(best['perf'], 'bullet')
        self.assertEqual(best['rating'], 1774)

    def test_best_rating_is_none_when_everything_is_provisional(self):
        self.link.bullet_provisional = True
        self.link.save()
        self.assertIsNone(self.link.best_rating)


class LichessOAuthSecurityTest(TestCase):
    """The callback decides who somebody is, so each check earns its place."""

    def setUp(self):
        self.start_url = reverse('lichess_start')
        self.callback_url = reverse('lichess_callback')

    def test_start_stashes_state_and_verifier_and_redirects_to_lichess(self):
        response = self.client.get(self.start_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://lichess.org/oauth'))
        self.assertIn('code_challenge_method=S256', response['Location'])
        self.assertIn('lichess_oauth_state', self.client.session)
        self.assertIn('lichess_oauth_verifier', self.client.session)

    def test_a_callback_with_no_session_is_refused(self):
        """Somebody pasting a callback URL straight into the browser."""
        response = self.client.get(self.callback_url, {'code': 'x', 'state': 'y'},
                                   follow=True)
        self.assertContains(response, 'expired')
        self.assertFalse(LichessAccount.objects.exists())

    def test_a_mismatched_state_is_refused(self):
        """The attack this defends against.

        Without the state check, an attacker completes an OAuth flow into
        THEIR Lichess account and hands the victim the callback link, silently
        binding the victim's browser to the attacker's identity.
        """
        self.client.get(self.start_url)          # establishes a real state
        response = self.client.get(
            self.callback_url, {'code': 'x', 'state': 'not-the-one'}, follow=True)
        self.assertContains(response, 'did not start here')
        self.assertFalse(LichessAccount.objects.exists())

    def test_state_and_verifier_are_consumed_so_a_code_cannot_be_replayed(self):
        self.client.get(self.start_url)
        self.client.get(self.callback_url, {'code': 'x', 'state': 'wrong'})
        self.assertNotIn('lichess_oauth_state', self.client.session)
        self.assertNotIn('lichess_oauth_verifier', self.client.session)

    def test_cancelling_on_lichess_is_not_an_error(self):
        response = self.client.get(self.callback_url, {'error': 'access_denied'},
                                   follow=True)
        self.assertContains(response, 'cancelled')


class LichessLinkingTest(TestCase):
    """Linking, and refusing to move a link between two people."""

    def setUp(self):
        self.assoc = make_association()
        self.alice, _ = make_user_and_member('alice', assoc=self.assoc)
        self.bob, _ = make_user_and_member('bob', assoc=self.assoc)
        self.profile = {
            'id': 'shared', 'username': 'Shared',
            'perfs': {'blitz': {'rating': 1600, 'games': 50, 'prov': False}},
            'count': {'all': 50, 'win': 25, 'loss': 20, 'draw': 5},
        }

    def _callback_as(self, user, profile):
        """Run the callback with the network calls stubbed out."""
        if user is not None:
            self.client.force_login(user)
        self.client.get(reverse('lichess_start'))
        session = self.client.session
        state = session['lichess_oauth_state']

        with patch.object(lichess, 'exchange_code', return_value='tok'), \
             patch.object(lichess, 'whoami', return_value=profile):
            return self.client.get(reverse('lichess_callback'),
                                   {'code': 'c', 'state': state}, follow=True)

    def test_linking_to_the_signed_in_account(self):
        self._callback_as(self.alice, self.profile)
        link = LichessAccount.objects.get()
        self.assertEqual(link.user, self.alice)
        self.assertEqual(link.username, 'Shared')
        self.assertEqual(link.blitz_rating, 1600)
        self.assertIsNotNone(link.synced_at)

    def test_an_already_linked_account_is_not_moved_to_somebody_else(self):
        """The takeover this refuses.

        If Bob signs in with a Lichess account already linked to Alice, moving
        the link would hand whoever controls that Lichess account control of
        Alice's member profile here.
        """
        self._callback_as(self.alice, self.profile)
        self.client.logout()

        response = self._callback_as(self.bob, self.profile)
        self.assertContains(response, 'already linked')

        link = LichessAccount.objects.get()
        self.assertEqual(link.user, self.alice,
                         'the link must stay with whoever had it')

    def test_signing_in_again_with_a_linked_account_logs_that_person_in(self):
        self._callback_as(self.alice, self.profile)
        self.client.logout()

        self._callback_as(None, self.profile)
        self.assertEqual(int(self.client.session['_auth_user_id']),
                         self.alice.pk)

    def test_a_brand_new_person_gets_a_login_and_is_sent_to_choose_a_club(self):
        response = self._callback_as(None, self.profile)
        user = User.objects.get(username='Shared')
        self.assertFalse(user.has_usable_password(),
                         'there is no password, so there is none to guess')
        self.assertFalse(hasattr(user, 'member'),
                         'a club cannot be guessed, so no Member yet')
        self.assertContains(response, 'choose your club')

    def test_a_clashing_username_is_suffixed_not_merged(self):
        """Lichess usernames are unique on Lichess, not here.

        Attaching to an existing account with the same name would hand a
        stranger somebody else's profile.
        """
        User.objects.create_user(username='Shared', password='pass1234')
        self._callback_as(None, self.profile)
        self.assertTrue(User.objects.filter(username='Shared2').exists())


class LichessUnlinkTest(TestCase):
    def setUp(self):
        self.assoc = make_association()
        self.member, _ = make_user_and_member('carol', assoc=self.assoc)
        self.link = LichessAccount.objects.create(
            user=self.member, lichess_id='carol', username='Carol')

    def test_unlinking_works_when_a_password_exists(self):
        self.client.force_login(self.member)
        self.client.post(reverse('lichess_unlink'), follow=True)
        self.assertFalse(LichessAccount.objects.exists())

    def test_unlinking_is_refused_when_lichess_is_the_only_way_in(self):
        """Otherwise somebody locks themselves out of an account that has no
        password to reset."""
        self.member.set_unusable_password()
        self.member.save()
        self.client.force_login(self.member)

        response = self.client.post(reverse('lichess_unlink'), follow=True)
        self.assertContains(response, 'only way to sign in')
        self.assertTrue(LichessAccount.objects.exists())


class RankedByLichessTest(TestCase):
    """The one ordering used by the ratings page, the home page top five, a
    club's player list and a coach's players.

    It was about to be four copies. Four copies of a rule is exactly how the
    ELO formula came to disagree with itself (see matches/rating.py), so this
    pins the behaviour that copies would have drifted on.
    """

    def _player(self, username, blitz=None, bullet=None, linked=True):
        _user, member = make_user_and_member(username, assoc=self.assoc)
        if linked:
            LichessAccount.objects.create(
                user=member.user, lichess_id=username, username=username,
                blitz_rating=blitz, blitz_games=50 if blitz else 0,
                bullet_rating=bullet, bullet_games=50 if bullet else 0)
        return member

    def setUp(self):
        self.assoc = make_association()
        self.strong = self._player('strong', blitz=2100)
        self.middle = self._player('middle', blitz=1600)
        self.bullet_only = self._player('bulletonly', blitz=None, bullet=1800)
        self.unlinked = self._player('unlinked', linked=False)

    def _order(self):
        return [m.user.username for m in
                ranked_by_lichess(Member.objects.filter(association=self.assoc))]

    def test_ordered_by_blitz_descending(self):
        order = self._order()
        self.assertLess(order.index('strong'), order.index('middle'))

    def test_a_bullet_only_player_is_ranked_on_bullet(self):
        """Ranking on blitz alone would drop them off the list entirely."""
        order = self._order()
        self.assertLess(order.index('strong'), order.index('bulletonly'))
        self.assertLess(order.index('bulletonly'), order.index('middle'),
                        '1800 bullet outranks 1600 blitz')

    def test_a_member_with_no_lichess_account_is_not_dropped(self):
        order = self._order()
        self.assertIn('unlinked', order, 'they are still a member')

    def test_the_ordering_asks_the_database_for_nulls_last(self):
        """THIS TEST EXISTS BECAUSE THE OBVIOUS ONE PROVED NOTHING.

        Asserting that the unrated player comes last passes on SQLite whether
        or not nulls_last is set, because SQLite sorts NULL last on a
        descending sort anyway. Production is PostgreSQL, which sorts NULL
        FIRST, so the obvious test was green while the ratings page would have
        been topped by players who have no rating at all.

        Verified by removing nulls_last: the row-order test stayed green and
        only this one went red. Asserting on the compiled SQL is the only
        check here that means the same thing on both databases.
        """
        sql = str(ranked_by_lichess(Member.objects.all()).query).upper()
        self.assertIn('DESC NULLS LAST', sql,
                      'without NULLS LAST, PostgreSQL puts unrated players '
                      'at the TOP of the ratings page')

    def test_the_order_is_stable_between_calls(self):
        """Two players on the same rating must not swap places on refresh."""
        self._player('tieA', blitz=1500)
        self._player('tieB', blitz=1500)
        self.assertEqual(self._order(), self._order())
