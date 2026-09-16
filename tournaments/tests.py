import datetime
from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from associations.models import Association
from members.models import Member
from .models import Tournament, TournamentRegistration, Round, Section
from .services import create_next_round, compute_standings, compute_crosstable
from .pairing import generate_pairings, _score
from matches.models import Match


def make_assoc():
    return Association.objects.create(name='Test Club', city='Bulawayo', email='test@chess.zw')


def make_member(username, rating=1200, assoc=None):
    if assoc is None:
        assoc = make_assoc()
    user = User.objects.create_user(username=username, password='pass1234')
    return Member.objects.create(user=user, association=assoc, rating=rating)


def make_tournament(assoc, num_rounds=5):
    today = datetime.date.today()
    return Tournament.objects.create(
        association=assoc, name='Test Open', status='in_progress',
        start_date=today, end_date=today, location='Harare',
        num_rounds=num_rounds,
    )


def register(player, tournament):
    return TournamentRegistration.objects.create(
        tournament=tournament, player=player, status='confirmed'
    )


class ScoreHelperTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.p1 = make_member('p1', assoc=self.assoc)
        self.p2 = make_member('p2', assoc=self.assoc)
        self.tournament = make_tournament(self.assoc)
        self.round = Round.objects.create(tournament=self.tournament, number=1)

    def _make_match(self, white, black, result):
        return Match.objects.create(
            tournament=self.tournament, round=self.round,
            white_player=white, black_player=black, result=result,
        )

    def test_win_gives_one_point(self):
        self._make_match(self.p1, self.p2, 'white_win')
        self.assertEqual(_score(self.p1, self.tournament), 1.0)

    def test_loss_gives_zero_points(self):
        self._make_match(self.p1, self.p2, 'white_win')
        self.assertEqual(_score(self.p2, self.tournament), 0.0)

    def test_draw_gives_half_point(self):
        self._make_match(self.p1, self.p2, 'draw')
        self.assertEqual(_score(self.p1, self.tournament), 0.5)
        self.assertEqual(_score(self.p2, self.tournament), 0.5)

    def test_pending_match_not_counted(self):
        self._make_match(self.p1, self.p2, 'pending')
        self.assertEqual(_score(self.p1, self.tournament), 0.0)


class SwissPairingTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.tournament = make_tournament(self.assoc)
        # Create 4 players with different ratings
        self.players = [
            make_member(f'p{i}', rating=1200 + i * 50, assoc=self.assoc)
            for i in range(4)
        ]
        for p in self.players:
            register(p, self.tournament)

    def test_generates_correct_number_of_pairings(self):
        pairings, bye, errors = generate_pairings(self.tournament, 1)
        self.assertEqual(len(pairings), 2)  # 4 players → 2 boards
        self.assertIsNone(bye)

    def test_each_player_appears_once(self):
        pairings, _, _ = generate_pairings(self.tournament, 1)
        assigned = set()
        for p in pairings:
            assigned.add(p['white'].pk)
            assigned.add(p['black'].pk)
        self.assertEqual(len(assigned), 4)

    def test_no_rematches_in_round_2(self):
        # Run round 1 and record results
        round1 = Round.objects.create(tournament=self.tournament, number=1)
        pairings1, _, _ = generate_pairings(self.tournament, 1)
        for pair in pairings1:
            Match.objects.create(
                tournament=self.tournament, round=round1,
                white_player=pair['white'], black_player=pair['black'],
                result='white_win',
            )
        # Round 2 pairings must not repeat round 1 matchups
        pairings2, _, _ = generate_pairings(self.tournament, 2)
        r1_pairs = {frozenset([p['white'].pk, p['black'].pk]) for p in pairings1}
        for pair in pairings2:
            matchup = frozenset([pair['white'].pk, pair['black'].pk])
            self.assertNotIn(matchup, r1_pairs)

    def test_bye_assigned_for_odd_players(self):
        extra = make_member('p_extra', rating=1100, assoc=self.assoc)
        register(extra, self.tournament)  # now 5 players
        pairings, bye, _ = generate_pairings(self.tournament, 1)
        self.assertEqual(len(pairings), 2)
        self.assertIsNotNone(bye)

    def test_empty_tournament_returns_error(self):
        empty_t = make_tournament(self.assoc)
        pairings, bye, errors = generate_pairings(empty_t, 1)
        self.assertEqual(pairings, [])
        self.assertTrue(len(errors) > 0)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

class SectionsTest(TestCase):
    """A section is a separate competition sharing a hall and a round schedule.

    WHY THIS EXISTS: Percival described a Saturday at the NUST hall as running
    "sections like open, ladies and developmental for kids", and this site had
    no concept of them. One tournament meant one pairing pool, so a twelve
    year old in Developmental could be paired against the strongest adult in
    the room, and one standings table mixed three competitions that award
    three separate sets of prizes.

    The rules these tests hold to:
      a pairing never crosses a section
      each section gets its own bye
      board numbers never collide, because a board is a physical table
      standings and crosstables are per section
      a tournament with NO sections behaves exactly as it always did
    """

    def setUp(self):
        self.assoc = make_assoc()
        self.tournament = make_tournament(self.assoc)
        self.tournament.status = 'registration_open'
        self.tournament.save(update_fields=['status'])

        self.open_section = Section.objects.create(
            tournament=self.tournament, name='Open', order=1)
        self.dev_section = Section.objects.create(
            tournament=self.tournament, name='Developmental', order=2)

        # Four strong adults and four juniors. Ratings deliberately far apart:
        # if pairing ever ignored sections, the mismatch would be glaring.
        self.adults = []
        for i in range(4):
            m = make_member('adult%d' % i, rating=1800 + i * 10, assoc=self.assoc)
            TournamentRegistration.objects.create(
                tournament=self.tournament, player=m,
                section=self.open_section, status='confirmed')
            self.adults.append(m)

        self.juniors = []
        for i in range(4):
            m = make_member('junior%d' % i, rating=900 + i * 10, assoc=self.assoc)
            TournamentRegistration.objects.create(
                tournament=self.tournament, player=m,
                section=self.dev_section, status='confirmed')
            self.juniors.append(m)

    def test_pairings_never_cross_a_section(self):
        """The whole point. A junior must never be paired against an adult."""
        round_obj, pairings, _bye, _errors, _byes = create_next_round(self.tournament)

        adult_pks = {m.pk for m in self.adults}
        junior_pks = {m.pk for m in self.juniors}

        for match in round_obj.matches.filter(black_player__isnull=False):
            white, black = match.white_player_id, match.black_player_id
            both_adult = white in adult_pks and black in adult_pks
            both_junior = white in junior_pks and black in junior_pks
            self.assertTrue(
                both_adult or both_junior,
                '%s was paired against %s across sections'
                % (match.white_player, match.black_player))

    def test_every_match_records_the_section_it_was_played_in(self):
        round_obj, *_ = create_next_round(self.tournament)
        for match in round_obj.matches.all():
            self.assertIsNotNone(
                match.section_id,
                'a game in a sectioned tournament must know its section, or '
                'moving a player later rewrites history')

    def test_board_numbers_do_not_collide_across_sections(self):
        """A board is a physical table with a number taped to it.

        Two games at board 1 in the same hall sends two pairs of players to
        the same table.
        """
        round_obj, *_ = create_next_round(self.tournament)
        boards = [m.board_number for m in round_obj.matches.all()
                  if m.board_number is not None]
        self.assertEqual(sorted(boards), sorted(set(boards)),
                         'duplicate board numbers: %s' % boards)

    def test_each_section_gets_its_own_bye(self):
        """Odd turnout in two sections means two byes, not one."""
        for section, name in ((self.open_section, 'adult9'),
                              (self.dev_section, 'junior9')):
            m = make_member(name, rating=1000, assoc=self.assoc)
            TournamentRegistration.objects.create(
                tournament=self.tournament, player=m,
                section=section, status='confirmed')

        round_obj, _pairings, _bye, _errors, bye_players = create_next_round(self.tournament)

        self.assertEqual(len(bye_players), 2,
                         'two odd sections must produce two byes')
        bye_sections = {m.section_id for m in
                        round_obj.matches.filter(black_player__isnull=True)}
        self.assertEqual(bye_sections, {self.open_section.pk, self.dev_section.pk})

    def test_standings_are_per_section(self):
        create_next_round(self.tournament)

        open_table = compute_standings(self.tournament, section=self.open_section)
        dev_table = compute_standings(self.tournament, section=self.dev_section)

        self.assertEqual(len(open_table), 4)
        self.assertEqual(len(dev_table), 4)

        # Each section has its own rank 1. A shared table would have one.
        self.assertEqual(open_table[0]['rank'], 1)
        self.assertEqual(dev_table[0]['rank'], 1)

        open_pks = {r['player'].pk for r in open_table}
        self.assertTrue(open_pks.isdisjoint({r['player'].pk for r in dev_table}))

    def test_standings_without_a_section_still_return_everybody(self):
        """The signature stayed backwards compatible on purpose."""
        everyone = compute_standings(self.tournament)
        self.assertEqual(len(everyone), 8)

    def test_crosstable_is_per_section(self):
        create_next_round(self.tournament)
        grid = compute_crosstable(self.tournament, section=self.open_section)
        self.assertEqual(len(grid['players']), 4,
                         'an Open crosstable must not contain juniors who can '
                         'never appear in it')


class TournamentsWithoutSectionsAreUnchanged(TestCase):
    """Sections are optional, and this shipped onto a live database.

    Every tournament that already existed has no sections, and must pair,
    score and display exactly as it did before any of this was written.
    """

    def setUp(self):
        self.assoc = make_assoc()
        self.tournament = make_tournament(self.assoc)
        self.tournament.status = 'registration_open'
        self.tournament.save(update_fields=['status'])
        self.players = []
        for i in range(6):
            m = make_member('p%d' % i, rating=1500 + i * 25, assoc=self.assoc)
            TournamentRegistration.objects.create(
                tournament=self.tournament, player=m, status='confirmed')
            self.players.append(m)

    def test_a_round_still_pairs_everybody_together(self):
        round_obj, pairings, _bye, _errors, byes = create_next_round(self.tournament)
        self.assertEqual(len(pairings), 3, 'six players should make three games')
        self.assertEqual(byes, [], 'an even field needs no bye')

    def test_matches_have_no_section(self):
        round_obj, *_ = create_next_round(self.tournament)
        for match in round_obj.matches.all():
            self.assertIsNone(match.section_id)

    def test_standings_cover_the_whole_field(self):
        create_next_round(self.tournament)
        self.assertEqual(len(compute_standings(self.tournament)), 6)


class SectionsLockOnceRoundsStart(TestCase):
    """Sections split the field BEFORE play, and then stop moving.

    Adding a section after round one is paired leaves its players with no
    games in the rounds already played, so their standings cannot be compared
    with anyone else's. Moving a player between sections is worse: their
    existing results follow them into a table where their opponents do not
    exist.

    The director panel refuses both once any round exists. Match.section is
    stored per game precisely so that history stays put whatever happens
    later.
    """

    def setUp(self):
        self.assoc = make_assoc()
        self.tournament = make_tournament(self.assoc)
        self.tournament.status = 'registration_open'
        self.tournament.save(update_fields=['status'])

        self.director = User.objects.create_user(
            username='director', password='pass1234', is_staff=True)

        self.section = Section.objects.create(
            tournament=self.tournament, name='Open', order=1)
        for i in range(4):
            m = make_member('lock%d' % i, rating=1400 + i, assoc=self.assoc)
            TournamentRegistration.objects.create(
                tournament=self.tournament, player=m,
                section=self.section, status='confirmed')

        self.client.login(username='director', password='pass1234')
        self.url = reverse('tournament_manage', args=[self.tournament.pk])

    def test_a_section_can_be_added_before_any_round(self):
        self.client.post(self.url, {'action': 'add_section',
                                    'section_name': 'Ladies'}, follow=True)
        self.assertTrue(self.tournament.sections.filter(name='Ladies').exists())

    def test_a_section_cannot_be_added_once_a_round_is_paired(self):
        create_next_round(self.tournament)
        self.client.post(self.url, {'action': 'add_section',
                                    'section_name': 'Ladies'}, follow=True)
        self.assertFalse(
            self.tournament.sections.filter(name='Ladies').exists(),
            'adding a section mid-event leaves its players with no games in '
            'the rounds already played')

    def test_players_cannot_be_moved_once_a_round_is_paired(self):
        other = Section.objects.create(
            tournament=self.tournament, name='Ladies', order=2)
        create_next_round(self.tournament)

        reg = self.tournament.registrations.first()
        self.client.post(self.url, {'action': 'assign_sections',
                                    'section_%d' % reg.pk: other.pk}, follow=True)
        reg.refresh_from_db()
        self.assertEqual(
            reg.section_id, self.section.pk,
            'moving a player after pairing carries their results into a table '
            'where their opponents do not exist')

    def test_deleting_a_section_keeps_its_players_entered(self):
        """SET_NULL, not CASCADE. Removing a section must not remove people."""
        before = self.tournament.registrations.count()
        self.client.post(self.url, {'action': 'delete_section',
                                    'section_pk': self.section.pk}, follow=True)
        self.assertEqual(self.tournament.registrations.count(), before)
        self.assertFalse(self.tournament.registrations.exclude(section=None).exists())
