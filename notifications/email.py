"""
Email dispatch module for ChessHub.

All outbound emails go through this module. Each function:
  1. Builds the template context
  2. Renders subject + HTML body
  3. Sends via Django's mail backend (console in dev, SMTP in prod)
  4. Creates an in-app Notification record
"""

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from .models import Notification


BASE_URL = getattr(settings, 'SITE_BASE_URL', 'http://127.0.0.1:8000')


def _send(to_user, subject, html_body, notification_type):
    """Record an in-app notification, and email it if we can.

    THE ORDER MATTERS, and it used to be the other way round.

    This returned early when a user had no email address, so somebody without
    one got NOTHING: no mail, and no in-app notification either. The
    notification centre exists precisely so a member without an email address
    still finds out they have been paired for round three. On a club where
    juniors sign up without an address, that was most of the members.

    So the notification is created FIRST and unconditionally. The email is an
    enhancement on top of it, not the thing itself.

    AND email_sent NOW MEANS WHAT IT SAYS. It was set to True unconditionally
    while the send used fail_silently=True, so a failed delivery left a record
    asserting it had been delivered and nothing anywhere said otherwise. It is
    taken from the return value of send(), which is the number of messages
    actually handed over. That is the fifth time this project has found a step
    reporting success having quietly done nothing, and the first where the
    record itself was the lie.
    """
    note = Notification.objects.create(
        recipient=to_user,
        type=notification_type,
        message=subject,
        email_sent=False,
    )

    if not to_user.email:
        return note

    msg = EmailMultiAlternatives(
        subject=subject,
        body=_html_to_plain(html_body),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_user.email],
    )
    msg.attach_alternative(html_body, 'text/html')

    # fail_silently keeps one unreachable mail server from breaking a loop
    # over twenty players mid-round. The try/except covers the failures
    # fail_silently does not, such as the provider raising on a bad API key.
    # Either way the notification above already exists, so nothing is lost.
    try:
        delivered = msg.send(fail_silently=True)
    except Exception:
        delivered = 0

    if delivered:
        note.email_sent = True
        note.save(update_fields=['email_sent'])

    return note


def _html_to_plain(html):
    """Very basic HTML → plain text fallback."""
    import re
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public sending functions
# ---------------------------------------------------------------------------

def send_registration_confirmed(registration):
    """Email a player when their tournament registration is confirmed."""
    player = registration.player
    tournament = registration.tournament
    user = player.user

    ctx = {
        'player_name': user.get_full_name() or user.username,
        'tournament': tournament,
        'tournament_url': f'{BASE_URL}/tournaments/{tournament.pk}/',
    }
    subject = f'Registration Confirmed — {tournament.name}'
    html = render_to_string('email/registration_confirmed.html', ctx)
    _send(user, subject, html, 'registration_confirmed')


def send_round_pairings(round_obj):
    """
    Email every player in the round their pairing.
    Called after create_next_round() in services.py.
    """
    from matches.models import Match
    from tournaments.pairing import _score

    tournament = round_obj.tournament
    all_matches = list(
        round_obj.matches.select_related(
            'white_player__user', 'black_player__user'
        )
    )

    # Build the full pairings list (used in the "all pairings" table for every email)
    all_pairings_display = []
    for m in all_matches:
        if m.black_player is None:
            continue
        all_pairings_display.append({
            'board': m.board_number,
            'white': m.white_player.user.get_full_name() or m.white_player.user.username,
            'black': m.black_player.user.get_full_name() or m.black_player.user.username,
            'white_pk': m.white_player.pk,
            'black_pk': m.black_player.pk,
        })

    tournament_url = f'{BASE_URL}/tournaments/{tournament.pk}/'

    for match in all_matches:
        # --- Email white player ---
        white = match.white_player
        is_bye = match.black_player is None

        pairing_display = [
            dict(p, is_yours=(p['white_pk'] == white.pk or p['black_pk'] == white.pk))
            for p in all_pairings_display
        ]

        ctx = {
            'tournament': tournament,
            'round_number': round_obj.number,
            'is_bye': is_bye,
            'white_player': white.user.get_full_name() or white.user.username,
            'black_player': match.black_player.user.get_full_name() if match.black_player else 'BYE',
            'player_color': 'White',
            'board_number': match.board_number,
            'player_score': _score(white, tournament),
            'all_pairings': pairing_display,
            'tournament_url': tournament_url,
        }
        subject = f'Round {round_obj.number} Pairings — {tournament.name}'
        html = render_to_string('email/round_pairings.html', ctx)
        _send(white.user, subject, html, 'match_scheduled')

        # --- Email black player (skip for byes) ---
        if match.black_player:
            black = match.black_player
            pairing_display_b = [
                dict(p, is_yours=(p['white_pk'] == black.pk or p['black_pk'] == black.pk))
                for p in all_pairings_display
            ]
            ctx_b = dict(ctx,
                player_color='Black',
                player_score=_score(black, tournament),
                all_pairings=pairing_display_b,
            )
            html_b = render_to_string('email/round_pairings.html', ctx_b)
            _send(black.user, subject, html_b, 'match_scheduled')


def send_round_complete(round_obj):
    """
    Email every player their result and current standings after a round completes.
    """
    from matches.models import Match
    from tournaments.pairing import _score

    tournament = round_obj.tournament
    matches = list(
        round_obj.matches.select_related(
            'white_player__user', 'black_player__user'
        ).exclude(black_player__isnull=True)
    )
    tournament_url = f'{BASE_URL}/tournaments/{tournament.pk}/'

    # Build standings list (sorted by score)
    from tournaments.models import TournamentRegistration
    registrations = TournamentRegistration.objects.filter(
        tournament=tournament, status='confirmed'
    ).select_related('player__user')

    standings = sorted(
        [{'player': r.player, 'score': _score(r.player, tournament)} for r in registrations],
        key=lambda x: -x['score']
    )

    all_results = [
        {
            'white': m.white_player.user.get_full_name() or m.white_player.user.username,
            'black': m.black_player.user.get_full_name() or m.black_player.user.username,
            'result': m.result,
            'white_pk': m.white_player.pk,
            'black_pk': m.black_player.pk,
        }
        for m in matches
    ]

    rounds_remaining = tournament.num_rounds - round_obj.number

    for match in matches:
        for player, color, opponent in [
            (match.white_player, 'white', match.black_player),
            (match.black_player, 'black', match.white_player),
        ]:
            if match.result == 'white_win':
                result_label = 'Win' if color == 'white' else 'Loss'
            elif match.result == 'black_win':
                result_label = 'Win' if color == 'black' else 'Loss'
            elif match.result == 'draw':
                result_label = 'Draw'
            else:
                result_label = None

            rank = next(
                (i + 1 for i, s in enumerate(standings) if s['player'].pk == player.pk),
                '—'
            )
            results_with_flag = [
                dict(r, is_yours=(r['white_pk'] == player.pk or r['black_pk'] == player.pk))
                for r in all_results
            ]

            ctx = {
                'tournament': tournament,
                'round_number': round_obj.number,
                'player_result': result_label,
                'opponent_name': opponent.user.get_full_name() or opponent.user.username,
                'player_score': _score(player, tournament),
                'player_rank': rank,
                'rounds_remaining': rounds_remaining,
                'all_results': results_with_flag,
                'tournament_url': tournament_url,
            }
            subject = f'Round {round_obj.number} Results — {tournament.name}'
            html = render_to_string('email/round_complete.html', ctx)
            _send(player.user, subject, html, 'result_posted')


def send_announcement(association, subject, message, sender_name):
    """
    Send a custom announcement to all active members of an association.
    Returns the number of emails dispatched.
    """
    from members.models import Member
    members = Member.objects.filter(
        association=association, is_active=True
    ).select_related('user')

    sent = 0
    for member in members:
        ctx = {
            'association': association,
            'subject': subject,
            'message': message,
            'sender_name': sender_name,
            'dashboard_url': f'{BASE_URL}/dashboard/',
        }
        html = render_to_string('email/announcement.html', ctx)
        _send(member.user, subject, html, 'general')
        sent += 1

    return sent


def send_tournament_complete(tournament):
    """
    Email all players the final standings when the tournament concludes.
    """
    from tournaments.pairing import _score
    from tournaments.models import TournamentRegistration

    registrations = TournamentRegistration.objects.filter(
        tournament=tournament, status='confirmed'
    ).select_related('player__user')

    standings_raw = sorted(
        [{'player': r.player, 'score': _score(r.player, tournament)} for r in registrations],
        key=lambda x: -x['score']
    )

    tournament_url = f'{BASE_URL}/tournaments/{tournament.pk}/'

    for i, entry in enumerate(standings_raw):
        player = entry['player']
        user = player.user

        # Rating change = current rating minus starting ELO (1200 default)
        rating_change = player.rating - 1200

        standings_display = [
            {
                'name': s['player'].user.get_full_name() or s['player'].user.username,
                'score': s['score'],
                'rating': s['player'].rating,
                'is_you': s['player'].pk == player.pk,
            }
            for s in standings_raw
        ]

        ctx = {
            'tournament': tournament,
            'standings': standings_display,
            'player_score': entry['score'],
            'player_rank': i + 1,
            'total_players': len(standings_raw),
            'player_rating': player.rating,
            'rating_change': rating_change,
            'tournament_url': tournament_url,
        }
        subject = f'Final Results — {tournament.name}'
        html = render_to_string('email/tournament_complete.html', ctx)
        _send(user, subject, html, 'ranking_updated')
