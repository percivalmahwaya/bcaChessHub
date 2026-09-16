"""
Swiss pairing algorithm (Dutch system) for ChessHub.

Pairing rules:
  1. Players sorted by score descending (rating as tiebreaker).
  2. Split group in half; top half paired against bottom half.
  3. No rematches — if conflict, try next available opponent downward.
  4. Color balance — assign color player has had less; higher-rated gets
     white when tied.
  5. Bye — odd player count: lowest scorer who has not yet had a bye sits
     out and receives 1 point.
"""

from matches.models import Match
from .models import TournamentRegistration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score(player, tournament):
    """Total points earned by player in this tournament so far."""
    pts = 0.0
    for m in Match.objects.filter(tournament=tournament, white_player=player).exclude(result='pending'):
        if m.result == 'white_win':
            pts += 1.0
        elif m.result == 'draw':
            pts += 0.5
    for m in Match.objects.filter(tournament=tournament, black_player=player).exclude(result='pending'):
        if m.result == 'black_win':
            pts += 1.0
        elif m.result == 'draw':
            pts += 0.5
    return pts


def _opponents(player, tournament):
    """Set of player PKs already faced by player in this tournament."""
    white_side = set(Match.objects.filter(
        tournament=tournament, white_player=player
    ).values_list('black_player_id', flat=True))
    black_side = set(Match.objects.filter(
        tournament=tournament, black_player=player
    ).values_list('white_player_id', flat=True))
    return white_side | black_side


def _color_counts(player, tournament):
    """Return (white_games, black_games) played so far."""
    w = Match.objects.filter(tournament=tournament, white_player=player).count()
    b = Match.objects.filter(tournament=tournament, black_player=player).count()
    return w, b


def _had_bye(player, tournament):
    """True if player already received a bye in this tournament."""
    return Match.objects.filter(
        tournament=tournament,
        white_player=player,
        black_player=None,
        result='white_win',
    ).exists()


def _assign_colors(p1_data, p2_data):
    """
    Return (white_player, black_player).
    Prefer giving white to whoever has played fewer white games.
    Ties broken by higher rating gets white.
    """
    w1, b1 = p1_data['white_count'], p1_data['black_count']
    w2, b2 = p2_data['white_count'], p2_data['black_count']
    p1_white_debt = b1 - w1  # positive = owes white
    p2_white_debt = b2 - w2

    if p1_white_debt > p2_white_debt:
        return p1_data['player'], p2_data['player']
    elif p2_white_debt > p1_white_debt:
        return p2_data['player'], p1_data['player']
    else:
        # Tiebreak by rating
        if p1_data['player'].rating >= p2_data['player'].rating:
            return p1_data['player'], p2_data['player']
        return p2_data['player'], p1_data['player']


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_pairings(tournament, round_number, section=None, first_board=1):
    """
    Generate Swiss pairings for `round_number` in `tournament`.

    If `section` is given, only players entered in that section are paired.
    A section is a separate competition sharing the hall and the round
    schedule, so nobody in Open is ever paired against a child in
    Developmental, and each section gets its own bye.

    `first_board` lets a caller continue board numbering across sections.
    Two players sitting at "board 1" in the same room is a real problem in a
    hall where boards are physical tables with numbers taped to them.

    The history helpers above deliberately query the whole tournament rather
    than the section. That is not a bug: a player only ever plays inside their
    own section, so their tournament history IS their section history, and
    keeping the queries simple means a player moved between sections still has
    their real opponents and colours respected.

    Returns:
        pairings   list of dicts: {white, black, board_number}
        bye_player Member who receives a bye, or None
        errors     list of warning strings, for example forced rematches
    """
    registrations = TournamentRegistration.objects.filter(
        tournament=tournament, status='confirmed'
    ).select_related('player__user')

    if section is not None:
        registrations = registrations.filter(section=section)

    if not registrations.exists():
        where = f'the {section.name} section' if section else 'this tournament'
        return [], None, [f'No confirmed players in {where}.']

    # Build player data list
    players = []
    for reg in registrations:
        p = reg.player
        w, b = _color_counts(p, tournament)
        players.append({
            'player': p,
            'score': _score(p, tournament),
            'opponents': _opponents(p, tournament),
            'white_count': w,
            'black_count': b,
            'had_bye': _had_bye(p, tournament),
        })

    # Sort: score desc, then rating desc
    players.sort(key=lambda x: (-x['score'], -x['player'].rating))

    errors = []
    bye_player = None

    # Handle odd number of players
    if len(players) % 2 == 1:
        # Give bye to lowest scorer who hasn't had one yet
        for candidate in reversed(players):
            if not candidate['had_bye']:
                bye_player = candidate['player']
                players.remove(candidate)
                break
        else:
            # All players had a bye; give it to the lowest scorer anyway
            bye_player = players[-1]['player']
            players.pop()
            errors.append(f'{bye_player} receives a second bye (all players have had one).')

    # Dutch pairing: split in half, pair top vs bottom
    mid = len(players) // 2
    top_half = players[:mid]
    bot_half = players[mid:]

    paired = set()
    pairings = []
    board = first_board

    for i, p1 in enumerate(top_half):
        if i in paired:
            continue
        matched = False

        # Try to pair p1 with the aligned bot_half player first, then others
        # Filter None sentinels — they mark already-paired players this round
        candidates = [
            p for p in [bot_half[i]] + [bot_half[j] for j in range(len(bot_half)) if j != i]
            if p is not None
        ]

        for p2 in candidates:
            p2_idx = bot_half.index(p2)
            if p2_idx in paired:
                continue
            if p2['player'].pk in p1['opponents']:
                continue  # rematch — skip

            white, black = _assign_colors(p1, p2)
            pairings.append({'white': white, 'black': black, 'board_number': board})
            paired.add(i)
            # Mark p2 used (offset by mid in original list, but we track by bot index)
            bot_half[p2_idx] = None  # sentinel
            board += 1
            matched = True
            break

        if not matched:
            # Force rematch as last resort
            for j, p2 in enumerate(bot_half):
                if p2 is None:
                    continue
                white, black = _assign_colors(p1, p2)
                pairings.append({'white': white, 'black': black, 'board_number': board})
                errors.append(
                    f'Forced rematch: {p1["player"]} vs {p2["player"]} (no other valid opponent).'
                )
                bot_half[j] = None
                board += 1
                matched = True
                break

        if not matched:
            errors.append(f'{p1["player"]} could not be paired in round {round_number}.')

    return pairings, bye_player, errors
