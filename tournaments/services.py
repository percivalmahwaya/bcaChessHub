"""
Tournament service functions — called from views, admin actions, and management commands.
"""

from django.db import transaction
from .models import Round, Tournament
from .pairing import generate_pairings
from matches.models import Match


@transaction.atomic
def create_next_round(tournament):
    """
    Generate pairings for the next round of `tournament`, create the Round
    and Match objects, and return (round, pairings, bye_player, errors).

    Raises ValueError if the tournament is not in a state that allows new rounds.
    """
    if tournament.status not in ('registration_open', 'in_progress'):
        raise ValueError(f'Cannot create a round: tournament status is "{tournament.status}".')

    last_round = tournament.rounds.order_by('-number').first()

    if last_round and not last_round.is_complete:
        raise ValueError(
            f'Round {last_round.number} is not yet complete. '
            'Record all results before generating the next round.'
        )

    next_number = (last_round.number + 1) if last_round else 1

    if next_number > tournament.num_rounds:
        raise ValueError(
            f'All {tournament.num_rounds} rounds have been played. '
            'The tournament is complete.'
        )

    # Update status to in_progress on first round
    if tournament.status == 'registration_open':
        tournament.status = 'in_progress'
        tournament.save(update_fields=['status'])

    round_obj = Round.objects.create(tournament=tournament, number=next_number)

    # One Round per tournament, pairings per section.
    #
    # A section is a separate competition sharing the hall and the clock, so
    # round three starts at the same moment for everybody, but Open players
    # are only ever paired against Open players. A tournament with no sections
    # pairs exactly as it always did: the loop runs once with section None.
    #
    # Board numbers continue across sections rather than restarting, because
    # a board is a physical table with a number taped to it and two games
    # cannot both be at board 1.
    sections = list(tournament.sections.all()) or [None]

    all_pairings = []
    byes = []
    errors = []
    matches = []
    next_board = 1

    for section in sections:
        pairings, bye_player, section_errors = generate_pairings(
            tournament, next_number, section=section, first_board=next_board)

        for p in pairings:
            matches.append(Match(
                tournament=tournament,
                round=round_obj,
                section=section,
                white_player=p['white'],
                black_player=p['black'],
                board_number=p['board_number'],
            ))
            next_board = max(next_board, p['board_number'] + 1)

        # A bye is per section. With three sections an odd turnout in each
        # means three byes, which is correct: each is a competition of its own.
        if bye_player:
            matches.append(Match(
                tournament=tournament,
                round=round_obj,
                section=section,
                white_player=bye_player,
                black_player=None,
                result='white_win',
                board_number=None,
            ))
            byes.append(bye_player)

        all_pairings.extend(pairings)
        # Name the section on every warning, or a director reading three
        # sections' worth of errors cannot tell which hall half they concern.
        prefix = f'[{section.name}] ' if section else ''
        errors.extend(prefix + e for e in section_errors)

    pairings = all_pairings
    # Kept singular for callers that predate sections. With sections there can
    # be more than one, so bye_players carries the full list.
    bye_player = byes[0] if byes else None
    bye_players = byes

    Match.objects.bulk_create(matches)

    from django.utils import timezone
    round_obj.started_at = timezone.now()
    round_obj.save(update_fields=['started_at'])

    # Fire pairing emails to all players
    from notifications.email import send_round_pairings
    send_round_pairings(round_obj)

    return round_obj, pairings, bye_player, errors, bye_players


@transaction.atomic
def complete_round(round_obj):
    """
    Mark a round as complete. Raises ValueError if any matches are still pending.
    """
    pending = round_obj.matches.filter(result='pending', black_player__isnull=False)
    if pending.exists():
        names = ', '.join(
            f'{m.white_player} vs {m.black_player}' for m in pending[:3]
        )
        raise ValueError(f'Pending results remain: {names}{"..." if pending.count() > 3 else ""}')

    from django.utils import timezone
    round_obj.is_complete = True
    round_obj.completed_at = timezone.now()
    round_obj.save(update_fields=['is_complete', 'completed_at'])

    # Fire round-complete emails
    from notifications.email import send_round_complete, send_tournament_complete
    send_round_complete(round_obj)

    # Auto-complete tournament if this was the final round
    tournament = round_obj.tournament
    if round_obj.number >= tournament.num_rounds:
        tournament.status = 'completed'
        tournament.save(update_fields=['status'])
        send_tournament_complete(tournament)

    return round_obj


def compute_crosstable(tournament, section=None):
    """
    Crosstable for confirmed players, ordered by current standings rank.

    Pass `section` for one section's grid. Without it, a sectioned tournament
    would draw an N by N table in which most cells can never be filled,
    because players in different sections never meet. That is not a sparse
    crosstable, it is three crosstables laid on top of each other.

    Returns a dict:
      {
        'players': [player, ...],        # ordered by rank
        'rows': [
          {
            'rank': 1,
            'player': <Member>,
            'score': 3.5,
            'cells': [
              {'value': '×', 'type': 'diagonal'},
              {'value': '1', 'type': 'win'},
              {'value': '0', 'type': 'loss'},
              {'value': '½', 'type': 'draw'},
              {'value': '',  'type': 'empty'},
              ...
            ]
          },
          ...
        ]
      }
    Returns None if there are no confirmed players.
    """
    from matches.models import Match

    standings = compute_standings(tournament, section=section)
    if not standings:
        return None

    players = [row['player'] for row in standings]
    player_pks = {p.pk for p in players}
    col_index = {p.pk: i for i, p in enumerate(players)}

    # result_map[(row_pk, col_pk)] → display value from row-player's perspective
    result_map = {}

    for m in (
        Match.objects
        .filter(tournament=tournament, black_player__isnull=False)
        .exclude(result='pending')
    ):
        w, b = m.white_player_id, m.black_player_id
        if w not in player_pks or b not in player_pks:
            continue
        if m.result in ('white_win', 'black_forfeit'):
            result_map[(w, b)] = ('1', 'win')
            result_map[(b, w)] = ('0', 'loss')
        elif m.result in ('black_win', 'white_forfeit'):
            result_map[(w, b)] = ('0', 'loss')
            result_map[(b, w)] = ('1', 'win')
        elif m.result == 'draw':
            result_map[(w, b)] = ('½', 'draw')
            result_map[(b, w)] = ('½', 'draw')

    rows = []
    for st in standings:
        p = st['player']
        cells = []
        for col_player in players:
            if col_player.pk == p.pk:
                cells.append({'value': '×', 'type': 'diagonal'})
            else:
                val, ctype = result_map.get((p.pk, col_player.pk), ('', 'empty'))
                cells.append({'value': val, 'type': ctype})
        rows.append({
            'rank': st['rank'],
            'player': p,
            'score': st['score'],
            'cells': cells,
        })

    return {'players': players, 'rows': rows}


def compute_standings(tournament, section=None):
    """
    Standings for confirmed players, by score, then Buchholz, then ELO.

    Pass `section` for one section's table. A section is a separate
    competition, so Open, Ladies and Developmental each have their own rank 1
    and their own prizes, and a single mixed table would be meaningless to all
    three. Omitting it returns every confirmed player, which is what a
    tournament with no sections wants and what every caller wanted before
    sections existed.

    Each entry: player, score, wins, draws, losses, byes, games_played,
    buchholz, rank
    """
    from matches.models import Match

    confirmed = tournament.registrations.filter(status='confirmed').select_related('player__user')
    if section is not None:
        confirmed = confirmed.filter(section=section)
    players = [reg.player for reg in confirmed]
    if not players:
        return []

    player_pks = {p.pk for p in players}

    score = {p.pk: 0.0 for p in players}
    wins = {p.pk: 0 for p in players}
    draws = {p.pk: 0 for p in players}
    losses = {p.pk: 0 for p in players}
    byes = {p.pk: 0 for p in players}
    games = {p.pk: 0 for p in players}
    opponents = {p.pk: [] for p in players}

    real_matches = (
        Match.objects
        .filter(tournament=tournament, black_player__isnull=False)
        .exclude(result='pending')
    )
    for m in real_matches:
        w, b = m.white_player_id, m.black_player_id
        if w in player_pks and b in player_pks:
            opponents[w].append(b)
            opponents[b].append(w)

        if m.result in ('white_win', 'black_forfeit'):
            if w in score: score[w] += 1; wins[w] += 1; games[w] += 1
            if b in score: losses[b] += 1; games[b] += 1
        elif m.result in ('black_win', 'white_forfeit'):
            if b in score: score[b] += 1; wins[b] += 1; games[b] += 1
            if w in score: losses[w] += 1; games[w] += 1
        elif m.result == 'draw':
            if w in score: score[w] += 0.5; draws[w] += 1; games[w] += 1
            if b in score: score[b] += 0.5; draws[b] += 1; games[b] += 1

    for m in Match.objects.filter(tournament=tournament, black_player__isnull=True, result='white_win'):
        w = m.white_player_id
        if w in score:
            score[w] += 1
            byes[w] += 1

    buchholz = {p.pk: sum(score.get(opp, 0) for opp in opponents[p.pk]) for p in players}

    rows = [
        {
            'player': p,
            'score': score[p.pk],
            'wins': wins[p.pk],
            'draws': draws[p.pk],
            'losses': losses[p.pk],
            'byes': byes[p.pk],
            'games_played': games[p.pk],
            'buchholz': buchholz[p.pk],
        }
        for p in players
    ]
    rows.sort(key=lambda x: (-x['score'], -x['buchholz'], -x['player'].rating))
    for i, row in enumerate(rows):
        row['rank'] = i + 1
    return rows
