from django.test import TestCase
from django.contrib.auth.models import User
from associations.models import Association
from members.models import Member
from tournaments.models import Tournament, Round, TournamentRegistration
from matches.models import Match, Challenge
from matches.replay import replay
from django.urls import reverse
import datetime


def make_assoc():
    return Association.objects.create(name='Test Club', city='Bulawayo', email='test@chess.zw')


def make_member(username, rating=1200, assoc=None):
    if assoc is None:
        assoc = make_assoc()
    user = User.objects.create_user(username=username, password='pass1234')
    return Member.objects.create(user=user, association=assoc, rating=rating)


def make_tournament(assoc):
    today = datetime.date.today()
    return Tournament.objects.create(
        association=assoc, name='Test Open', status='in_progress',
        start_date=today, end_date=today, location='Harare', num_rounds=5,
    )


def make_round(tournament, number=1):
    return Round.objects.create(tournament=tournament, number=number)


class MatchEloTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.white = make_member('white', 1200, self.assoc)
        self.black = make_member('black', 1200, self.assoc)
        self.tournament = make_tournament(self.assoc)
        self.round = make_round(self.tournament)
        self.match = Match.objects.create(
            tournament=self.tournament, round=self.round,
            white_player=self.white, black_player=self.black,
        )

    def test_white_win_increases_white_elo(self):
        self.match.record_result('white_win')
        self.white.refresh_from_db()
        self.black.refresh_from_db()
        self.assertGreater(self.white.rating, 1200)
        self.assertLess(self.black.rating, 1200)

    def test_black_win_increases_black_elo(self):
        self.match.record_result('black_win')
        self.white.refresh_from_db()
        self.black.refresh_from_db()
        self.assertLess(self.white.rating, 1200)
        self.assertGreater(self.black.rating, 1200)

    def test_draw_equal_players_minimal_change(self):
        self.match.record_result('draw')
        self.white.refresh_from_db()
        self.black.refresh_from_db()
        # Draw between equal players → ratings barely move
        self.assertAlmostEqual(self.white.rating, 1200, delta=2)
        self.assertAlmostEqual(self.black.rating, 1200, delta=2)

    def test_elo_sum_is_conserved(self):
        total_before = self.white.rating + self.black.rating
        self.match.record_result('white_win')
        self.white.refresh_from_db()
        self.black.refresh_from_db()
        total_after = self.white.rating + self.black.rating
        # Due to rounding, allow ±1 deviation
        self.assertAlmostEqual(total_before, total_after, delta=1)

    def test_stronger_player_gains_less_on_win(self):
        strong = make_member('strong', 1600, self.assoc)
        weak   = make_member('weak', 1200, self.assoc)
        m = Match.objects.create(
            tournament=self.tournament, round=self.round,
            white_player=strong, black_player=weak,
        )
        m.record_result('white_win')
        strong.refresh_from_db()
        gain = strong.rating - 1600
        # Expected gain is small (favourite winning) — less than K=32
        self.assertGreater(gain, 0)
        self.assertLess(gain, 10)

    def test_rating_history_created_for_both_players(self):
        from members.models import RatingHistory
        self.match.record_result('white_win')
        self.assertEqual(RatingHistory.objects.filter(member=self.white).count(), 1)
        self.assertEqual(RatingHistory.objects.filter(member=self.black).count(), 1)


class ChallengeEloTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.challenger = make_member('challenger', 1200, self.assoc)
        self.opponent   = make_member('opponent', 1200, self.assoc)
        self.challenge  = Challenge.objects.create(
            challenger=self.challenger,
            opponent=self.opponent,
            status='accepted',
        )

    def test_challenger_win_updates_elo(self):
        self.challenge.record_result('challenger_win')
        self.challenger.refresh_from_db()
        self.opponent.refresh_from_db()
        self.assertGreater(self.challenger.rating, 1200)
        self.assertLess(self.opponent.rating, 1200)

    def test_elo_only_updated_once(self):
        """Calling record_result twice must not apply ELO a second time."""
        self.challenge.record_result('challenger_win')
        self.challenger.refresh_from_db()
        rating_after_first = self.challenger.rating

        # Simulate a second call (e.g. double-submit)
        self.challenge.record_result('challenger_win')
        self.challenger.refresh_from_db()
        self.assertEqual(self.challenger.rating, rating_after_first)

    def test_draw_result_sets_status_completed(self):
        self.challenge.record_result('draw')
        self.challenge.refresh_from_db()
        self.assertEqual(self.challenge.status, 'completed')
        self.assertEqual(self.challenge.result, 'draw')


# ---------------------------------------------------------------------------
# PGN replay
# ---------------------------------------------------------------------------

class ReplayTest(TestCase):
    """Positions for the game viewer, built on the server.

    This logic used to live in chess.js in the browser. Moving it here saved
    every visitor about 190 KB, and it also moved the responsibility for
    being correct onto us, so the awkward moves are tested rather than
    assumed: the ones where a single move changes more than two squares.
    """

    START = ('rnbqkbnr'
             'pppppppp'
             '........'
             '........'
             '........'
             '........'
             'PPPPPPPP'
             'RNBQKBNR')

    def test_opening_position_is_encoded_a8_first(self):
        """Index 0 is a8 and index 63 is h1, because that is the order CSS
        grid fills cells in. Getting this backwards renders every game
        upside down."""
        data = replay('1. e4 e5 *')
        self.assertEqual(data['positions'][0], self.START)
        self.assertEqual(data['positions'][0][0], 'r', 'index 0 must be a8')
        self.assertEqual(data['positions'][0][63], 'R', 'index 63 must be h1')

    def test_a_move_is_recorded_with_the_squares_it_touched(self):
        data = replay('1. e4 *')
        self.assertEqual(data['moves'], ['e4'])
        self.assertEqual(data['highlights'][1], [52, 36],
                         'e2 is index 52 and e4 is index 36')
        self.assertEqual(data['positions'][1][52], '.', 'e2 is now empty')
        self.assertEqual(data['positions'][1][36], 'P', 'the pawn is on e4')

    def test_castling_moves_the_rook_as_well(self):
        """Four squares change on one move. A viewer that only moves the
        piece named in the SAN leaves the rook behind."""
        data = replay('1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O *')
        final = data['positions'][-1]
        self.assertEqual(final[62], 'K', 'the king castled to g1')
        self.assertEqual(final[61], 'R', 'the rook came to f1')
        self.assertEqual(final[60], '.', 'e1 is empty')
        self.assertEqual(final[63], '.', 'h1 is empty')

    def test_en_passant_removes_a_pawn_from_a_square_neither_end_touched(self):
        """The captured pawn is not on the from square or the to square, so
        a viewer that moves pieces rather than replacing the position leaves
        a ghost pawn on the board."""
        data = replay('1. e4 a6 2. e5 d5 3. exd6 *')
        final = data['positions'][-1]
        self.assertEqual(final[19], 'P', 'the white pawn is on d6')
        self.assertEqual(final[27], '.', 'the black pawn on d5 is gone')

    def test_promotion_puts_the_new_piece_on_the_board(self):
        """The piece that lands is not the piece that moved."""
        data = replay('1. a4 b5 2. axb5 a6 3. bxa6 Nf6 4. a7 Ng8 5. axb8=Q *')
        self.assertEqual(data['moves'][-1], 'axb8=Q')
        self.assertEqual(data['positions'][-1][1], 'Q',
                         'b8 holds a white queen, not a pawn and not the '
                         'knight it captured')

    def test_a_record_that_stops_early_says_so(self):
        """python-chess halts at the first illegal move and returns what came
        before it, as though the game simply ended there. Found by seeding
        the viewer with a PGN that was illegal from move 17: the board showed
        a clean, plausible, shorter game and nothing said it was cut off.
        Reporting success having silently done less is the failure mode this
        project has shipped four times."""
        data = replay('1. e4 e5 2. Nf3 Rg8 *')
        self.assertTrue(data['truncated'])
        self.assertEqual(data['moves'], ['e4', 'e5', 'Nf3'],
                         'the legal moves before the break are still worth showing')

    def test_a_clean_game_is_not_flagged_as_truncated(self):
        """The other direction. A flag that is always on says nothing."""
        data = replay('1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0')
        self.assertFalse(data['truncated'])

    def test_a_game_with_no_moves_is_not_a_replay(self):
        """A PGN header block with no moves. Rendering an empty board with
        no way to move would read as a broken viewer."""
        self.assertIsNone(replay(
            '[Event "Nothing"]\n[Result "*"]\n\n*'))

    def test_unreadable_pgn_returns_none_rather_than_raising(self):
        """A malformed record on one match must not 500 the page: the result,
        the players and their ratings are all still worth showing."""
        self.assertIsNone(replay('this is not a chess game'))
        self.assertIsNone(replay(''))
        self.assertIsNone(replay(None))

    def test_headers_worth_showing_are_carried_through(self):
        data = replay(
            '[Opening "Sicilian Defence"]\n[Result "0-1"]\n\n1. e4 c5 0-1')
        self.assertEqual(data['opening'], 'Sicilian Defence')
        self.assertEqual(data['result'], '0-1')

    def test_every_position_is_exactly_64_squares(self):
        """The renderer indexes straight into this string, so a short one
        would silently blank the end of the board."""
        data = replay('1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 *')
        for i, position in enumerate(data['positions']):
            self.assertEqual(len(position), 64, f'position {i} is not 64 long')


class MatchDetailPageTest(TestCase):
    """The page as a visitor gets it."""

    def setUp(self):
        assoc = make_assoc()
        self.white = make_member('white_pl', assoc=assoc)
        self.black = make_member('black_pl', assoc=assoc)
        self.tournament = Tournament.objects.create(
            name='Viewer Open', association=assoc, location='Bulawayo',
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 2),
            num_rounds=3, max_players=8)
        self.round = Round.objects.create(tournament=self.tournament, number=1)

    def _match(self, pgn=''):
        return Match.objects.create(
            tournament=self.tournament, round=self.round,
            white_player=self.white, black_player=self.black,
            result='white_win', board_number=1, pgn=pgn)

    def test_a_match_with_a_game_ships_the_positions(self):
        match = self._match('1. e4 e5 2. Nf3 1-0')
        response = self.client.get(reverse('match_detail', args=[match.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'replay-data')
        self.assertContains(response, 'rnbqkbnr')

    def test_a_match_with_no_game_says_so_instead_of_drawing_an_empty_board(self):
        match = self._match('')
        response = self.client.get(reverse('match_detail', args=[match.pk]))
        self.assertContains(response, 'No game record')
        self.assertNotContains(response, 'replay-data')

    def test_an_unreadable_game_still_renders_the_result(self):
        match = self._match('total nonsense')
        response = self.client.get(reverse('match_detail', args=[match.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'could not be read')

    def test_the_page_loads_no_third_party_scripts(self):
        """The whole point of the rebuild. jQuery, chess.js and chessboard.js
        came from three CDNs and the piece images from a fourth."""
        match = self._match('1. e4 e5 1-0')
        response = self.client.get(reverse('match_detail', args=[match.pk]))
        body = response.content.decode()
        for host in ['code.jquery.com', 'cdnjs.cloudflare.com', 'unpkg.com',
                     'cdn.jsdelivr.net']:
            self.assertNotIn(host, body, f'{host} is still being loaded')
