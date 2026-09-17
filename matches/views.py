import json
import re
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError

from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST

from .models import Match, Challenge
from .replay import replay


def match_detail(request, match_pk):
    match = get_object_or_404(
        Match.objects.select_related(
            'tournament__association', 'round',
            'white_player__user', 'black_player__user',
        ),
        pk=match_pk,
    )
    # Parsed here rather than in the browser. The PGN is fixed the moment the
    # game ends, so parsing it on every page load in every visitor's phone was
    # 190 KB of libraries doing work that can be done once. See matches/replay.py.
    return render(request, 'matches/detail.html', {
        'match': match,
        'replay': replay(match.pgn) if match.pgn else None,
    })


def _parse_game_id(raw):
    """Accept a full Lichess URL or bare game ID; return the game ID string."""
    raw = raw.strip()
    m = re.search(r'lichess\.org/([A-Za-z0-9]{6,12})', raw)
    if m:
        return m.group(1)
    if re.fullmatch(r'[A-Za-z0-9]{6,12}', raw):
        return raw
    return None


@login_required
@require_POST
def link_lichess(request, match_pk):
    match = get_object_or_404(
        Match.objects.select_related('tournament', 'round', 'white_player', 'black_player'),
        pk=match_pk,
    )
    tournament = match.tournament

    user = request.user
    is_director = user.is_staff or (
        hasattr(user, 'member') and
        user.member.role == 'admin' and
        user.member.association_id == tournament.association_id
    )
    if not is_director:
        messages.error(request, 'Only tournament directors can link Lichess games.')
        return redirect('tournament_manage', pk=tournament.pk)

    raw = request.POST.get('lichess_url', '').strip()
    game_id = _parse_game_id(raw)
    if not game_id:
        messages.error(request, 'Invalid Lichess URL or game ID.')
        return redirect('tournament_manage', pk=tournament.pk)

    # ── Fetch game metadata from Lichess API ──────────────────────────
    try:
        req = urllib_request.Request(
            f'https://lichess.org/api/game/{game_id}',
            headers={'Accept': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=8) as resp:
            game_data = json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 404:
            messages.error(request, f'Game "{game_id}" not found on Lichess.')
        else:
            messages.error(request, f'Lichess API error (HTTP {e.code}).')
        return redirect('tournament_manage', pk=tournament.pk)
    except URLError:
        messages.error(request, 'Could not reach Lichess — check your connection.')
        return redirect('tournament_manage', pk=tournament.pk)

    # ── Fetch PGN (optional — never blocks the link) ──────────────────
    pgn_text = ''
    try:
        pgn_url = f'https://lichess.org/game/export/{game_id}?clocks=false&evals=false'
        with urllib_request.urlopen(pgn_url, timeout=8) as resp:
            pgn_text = resp.read().decode()
    except (URLError, HTTPError):
        pass

    # ── Map Lichess result → our RESULT_CHOICES ───────────────────────
    DRAW_STATUSES = {
        'draw', 'stalemate', 'threefoldRepetition', 'repetition',
        'fiftyMoves', 'insufficientMaterial', 'agreement',
    }
    winner = game_data.get('winner')          # 'white', 'black', or absent
    status = game_data.get('status', '')

    if winner == 'white':
        detected = 'white_win'
    elif winner == 'black':
        detected = 'black_win'
    elif status in DRAW_STATUSES or (not winner and status not in ('started', 'created')):
        detected = 'draw'
    else:
        detected = None  # game still in progress

    # ── Validate the PGN before storing it ────────────────────────────
    #
    # TRUST BUT VERIFY, on a game that is about to change two people's
    # ratings. Until now a PGN was fetched and stored with nobody checking it
    # even parsed, let alone that its moves were legal. A record that cannot
    # be read is worse than no record: the viewer draws whatever prefix DID
    # parse, which looks like a complete, plausible game that never happened.
    #
    # Recommended by the evaluation of niklasf's libraries, 2026-09-13,
    # item 2: "stops trusting unverified input on rated games".
    parsed = replay(pgn_text) if pgn_text else None

    if pgn_text and parsed is None:
        # Keep the link, drop the record. The game ID is still useful and
        # still resolves on Lichess.
        pgn_text = ''
        messages.warning(
            request,
            f'Game {game_id} was linked, but its move record could not be '
            'read and has not been saved. The link to Lichess still works.')
    elif parsed and parsed['truncated']:
        messages.warning(
            request,
            f'Game {game_id} was linked, but its move record stops early: it '
            'contains a move that cannot be played from the position before '
            'it. Only the moves up to that point will replay.')

    # ── Persist game ID, PGN and opening ──────────────────────────────
    match.lichess_game_id = game_id
    update_fields = ['lichess_game_id']
    if pgn_text:
        match.pgn = pgn_text
        update_fields.append('pgn')
    if parsed:
        # Arriving free on every export and previously discarded.
        match.eco = parsed['eco'][:8]
        match.opening = parsed['opening'][:120]
        update_fields += ['eco', 'opening']

    if match.result == 'pending' and detected and match.black_player:
        match.save(update_fields=update_fields)
        match.record_result(detected)
        label = dict(Match.RESULT_CHOICES).get(detected, detected)
        messages.success(request, f'Game {game_id} linked — result auto-recorded: {label}.')
    else:
        match.save(update_fields=update_fields)
        already = match.result != 'pending'
        if detected and already:
            label = dict(Match.RESULT_CHOICES).get(detected, detected)
            if detected != match.result:
                # NOT a success. The ratings have already been applied from
                # the recorded result, so if Lichess disagrees then two
                # players' ratings have moved the wrong way and somebody has
                # to decide which record is right. This used to be reported
                # in exactly the same tone as everything going fine.
                recorded = dict(Match.RESULT_CHOICES).get(match.result, match.result)
                messages.warning(
                    request,
                    f'Game {game_id} linked, but the results DISAGREE. This '
                    f'match is recorded as "{recorded}" and Lichess says '
                    f'"{label}". Ratings were already applied from the '
                    'recorded result, so if Lichess is right they need '
                    'correcting.')
            else:
                messages.success(
                    request,
                    f'Game {game_id} linked. Lichess confirms the recorded '
                    f'result: {label}.')
        elif not detected:
            messages.success(request, f'Game {game_id} linked (game may still be in progress).')
        else:
            messages.success(request, f'Game {game_id} linked successfully.')

    return redirect('match_detail', match_pk=match_pk)


# ── Challenge views ────────────────────────────────────────────────────────────

@login_required
def challenge_list(request):
    if not hasattr(request.user, 'member'):
        return redirect('home')
    member = request.user.member
    received = Challenge.objects.filter(opponent=member).select_related('challenger__user')
    sent     = Challenge.objects.filter(challenger=member).select_related('opponent__user')
    return render(request, 'matches/challenges/list.html', {
        'received': received,
        'sent': sent,
        'pending_count': received.filter(status='pending').count(),
    })


@login_required
@require_POST
def challenge_send(request, opponent_pk):
    from members.models import Member as MemberModel
    if not hasattr(request.user, 'member'):
        messages.error(request, 'You need a member profile to send challenges.')
        return redirect('rankings')

    challenger = request.user.member
    opponent   = get_object_or_404(MemberModel, pk=opponent_pk, is_active=True)

    if challenger == opponent:
        messages.error(request, "You can't challenge yourself.")
        return redirect('player_profile', pk=opponent_pk)

    if Challenge.objects.filter(challenger=challenger, opponent=opponent, status='pending').exists():
        messages.info(request, f'You already have a pending challenge with {opponent}.')
        return redirect('player_profile', pk=opponent_pk)

    msg = request.POST.get('message', '').strip()
    Challenge.objects.create(challenger=challenger, opponent=opponent, message=msg)

    from notifications.models import Notification
    Notification.objects.create(
        recipient=opponent.user,
        type='general',
        message=f'{challenger.user.get_full_name() or challenger.user.username} has challenged you to a rated match!',
    )
    messages.success(request, f'Challenge sent to {opponent}!')
    return redirect('player_profile', pk=opponent_pk)


@login_required
@require_POST
def challenge_respond(request, challenge_pk):
    if not hasattr(request.user, 'member'):
        return redirect('home')
    challenge = get_object_or_404(Challenge, pk=challenge_pk, opponent=request.user.member, status='pending')
    action = request.POST.get('action')
    from django.utils import timezone

    if action == 'accept':
        challenge.status      = 'accepted'
        challenge.responded_at = timezone.now()
        challenge.save(update_fields=['status', 'responded_at'])
        from notifications.models import Notification
        Notification.objects.create(
            recipient=challenge.challenger.user,
            type='general',
            message=f'{challenge.opponent.user.get_full_name() or challenge.opponent.user.username} accepted your challenge!',
        )
        messages.success(request, 'Challenge accepted! Arrange your game and come back to record the result.')
    elif action == 'decline':
        challenge.status      = 'declined'
        challenge.responded_at = timezone.now()
        challenge.save(update_fields=['status', 'responded_at'])
        from notifications.models import Notification
        Notification.objects.create(
            recipient=challenge.challenger.user,
            type='general',
            message=f'{challenge.opponent.user.get_full_name() or challenge.opponent.user.username} declined your challenge.',
        )
        messages.info(request, 'Challenge declined.')

    return redirect('challenge_list')


@login_required
@require_POST
def challenge_record_result(request, challenge_pk):
    if not hasattr(request.user, 'member'):
        return redirect('home')
    member    = request.user.member
    challenge = get_object_or_404(Challenge, pk=challenge_pk, status='accepted')

    if member not in (challenge.challenger, challenge.opponent):
        messages.error(request, 'Only the players in this challenge can record the result.')
        return redirect('challenge_list')

    result = request.POST.get('result')
    if result not in ('challenger_win', 'opponent_win', 'draw'):
        messages.error(request, 'Invalid result.')
        return redirect('challenge_list')

    challenge.record_result(result)

    winner_name = (
        challenge.challenger.user.get_full_name() or challenge.challenger.user.username
        if result == 'challenger_win' else
        challenge.opponent.user.get_full_name() or challenge.opponent.user.username
        if result == 'opponent_win' else None
    )
    msg = f'Result recorded — {"Draw" if not winner_name else winner_name + " wins"}. ELO ratings updated.'
    messages.success(request, msg)
    return redirect('challenge_detail', challenge_pk=challenge_pk)


@login_required
def challenge_detail(request, challenge_pk):
    if not hasattr(request.user, 'member'):
        return redirect('home')
    member    = request.user.member
    challenge = get_object_or_404(
        Challenge.objects.select_related('challenger__user', 'opponent__user'),
        pk=challenge_pk,
    )
    if member not in (challenge.challenger, challenge.opponent):
        messages.error(request, 'You are not part of this challenge.')
        return redirect('challenge_list')

    is_challenger = member == challenge.challenger
    return render(request, 'matches/challenges/detail.html', {
        'challenge': challenge,
        'is_challenger': is_challenger,
    })

    return redirect('tournament_manage', pk=tournament.pk)
