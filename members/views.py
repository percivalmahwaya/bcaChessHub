from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, update_session_auth_hash
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.db.models.functions import Coalesce
import time
from .models import Member, RatingHistory, ranked_by_lichess
from .forms import SignupForm, ProfileEditForm
from associations.models import Association
from matches.models import Match
from notifications.models import Notification


def signup(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome to ChessHub, {user.first_name}!')
            return redirect('dashboard')
    else:
        form = SignupForm()

    return render(request, 'registration/signup.html', {'form': form})


@login_required
def dashboard(request):
    if not hasattr(request.user, 'member'):
        return redirect('home')

    member = request.user.member

    # All matches this member played (completed only)
    matches_as_white = Match.objects.filter(
        white_player=member
    ).exclude(result='pending').select_related('tournament', 'round', 'black_player__user')

    matches_as_black = Match.objects.filter(
        black_player=member
    ).exclude(result='pending').select_related('tournament', 'round', 'white_player__user')

    wins = (
        matches_as_white.filter(result='white_win').count() +
        matches_as_black.filter(result='black_win').count()
    )
    losses = (
        matches_as_white.filter(result='black_win').count() +
        matches_as_black.filter(result='white_win').count()
    )
    draws = (
        matches_as_white.filter(result='draw').count() +
        matches_as_black.filter(result='draw').count()
    )
    games_played = wins + losses + draws

    if games_played > 0:
        win_rate = round(wins / games_played * 100)
        draw_rate = round(draws / games_played * 100)
        loss_rate = 100 - win_rate - draw_rate
    else:
        win_rate = draw_rate = loss_rate = 0

    # Combine and sort recent matches by date
    from itertools import chain
    all_matches = sorted(
        chain(matches_as_white, matches_as_black),
        key=lambda m: m.completed_at or m.scheduled_at or m.round.started_at or m.tournament.start_date,
        reverse=True
    )[:15]

    registrations = member.registrations.select_related(
        'tournament__association'
    ).order_by('-tournament__start_date')

    # Read the rows BEFORE marking them read.
    #
    # This used to be:
    #     unread_qs = Notification.objects.filter(..., is_read=False)
    #     unread_qs.update(is_read=True)
    #     notifications = unread_qs.order_by('-sent_at')[:5]
    #
    # A queryset is lazy. update() wrote is_read=True to every matching row,
    # and then the SAME queryset was evaluated for display, filtering on
    # is_read=False against rows that had just been set True. It always came
    # back empty.
    #
    # So opening your dashboard silently marked every unread notification as
    # read and showed you none of them. The bell cleared, the panel said "no
    # new notifications", and a pairing or a payment confirmation you had
    # never seen was now filed as read. Verified against the database before
    # fixing.
    notifications = list(
        Notification.objects.filter(recipient=request.user, is_read=False)
        .order_by('-sent_at')[:5]
    )
    Notification.objects.filter(
        pk__in=[n.pk for n in notifications]).update(is_read=True)

    coached_players = None
    if member.role == 'coach':
        coached_players = ranked_by_lichess(
            Member.objects.filter(coach=member, is_active=True)
            .select_related('user', 'user__lichess')
        )

    # Annotate each match from this member's point of view, rather than
    # branching on colour in the template. dashboard.html used to carry two
    # near-identical copies of the whole history row, one per colour, which is
    # how they drift apart.
    for m in all_matches:
        playing_white = m.white_player_id == member.pk
        m.colour = 'White' if playing_white else 'Black'
        m.opponent = m.black_player if playing_white else m.white_player
        won = (m.result == 'white_win') if playing_white else (m.result == 'black_win')
        lost = (m.result == 'black_win') if playing_white else (m.result == 'white_win')
        forfeited = m.result == ('white_forfeit' if playing_white else 'black_forfeit')
        m.outcome = ('Win' if won else 'Loss' if lost else 'Draw'
                     if m.result == 'draw' else 'Forfeit' if forfeited else 'Pending')

    pending_challenges = member.challenges_received.filter(status='pending').count()

    return render(request, 'members/dashboard.html', {
        'member': member,
        'pending_challenges': pending_challenges,
        'stats': {
            'games_played': games_played,
            'wins': wins,
            'losses': losses,
            'draws': draws,
            'win_rate': win_rate,
            'draw_rate': draw_rate,
            'loss_rate': loss_rate,
        },
        'recent_matches': all_matches,
        'registrations': registrations,
        'notifications': notifications,
        'coached_players': coached_players,
    })


@login_required
def manage_members(request):
    """Association admin panel — view and manage all members."""
    if not (request.user.is_staff or (hasattr(request.user, 'member') and request.user.member.role == 'admin')):
        messages.error(request, 'Only administrators can access member management.')
        return redirect('dashboard')

    # Staff sees all; association admin sees only their association
    if request.user.is_staff:
        base_qs = Member.objects.select_related('user', 'association')
    else:
        base_qs = Member.objects.select_related('user', 'association').filter(
            association=request.user.member.association
        )

    if request.method == 'POST':
        action     = request.POST.get('action')
        member_pk  = request.POST.get('member_pk')
        target     = get_object_or_404(base_qs, pk=member_pk)

        if action == 'set_role':
            new_role = request.POST.get('role')
            valid_roles = [r[0] for r in Member.ROLE_CHOICES]
            if new_role in valid_roles:
                target.role = new_role
                target.save(update_fields=['role'])
                messages.success(request, f'{target} role changed to {target.get_role_display()}.')

        elif action == 'toggle_active':
            target.is_active = not target.is_active
            target.save(update_fields=['is_active'])
            state = 'activated' if target.is_active else 'deactivated'
            messages.success(request, f'{target} {state}.')

        elif action == 'assign_coach':
            new_coach_pk = request.POST.get('coach_pk', '').strip()
            if new_coach_pk:
                coach = get_object_or_404(base_qs, pk=new_coach_pk, role='coach')
                target.coach = coach
            else:
                target.coach = None
            target.save(update_fields=['coach'])
            label = str(target.coach) if target.coach else 'none'
            messages.success(request, f'{target} coach set to {label}.')

        elif action == 'reset_elo':
            if request.user.is_staff:
                target.rating = 1200
                target.save(update_fields=['rating'])
                RatingHistory.objects.create(member=target, rating=1200, delta=0)
                messages.success(request, f'{target} ELO reset to 1200.')
            else:
                messages.error(request, 'Only site staff can reset ELO ratings.')

        elif action == 'announce':
            subject = request.POST.get('subject', '').strip()
            body    = request.POST.get('body', '').strip()
            if not subject or not body:
                messages.error(request, 'Subject and message body are both required.')
                return redirect('manage_members')

            from notifications.email import send_announcement
            sender_name = request.user.get_full_name() or request.user.username

            if request.user.is_staff:
                assoc_pk = request.POST.get('association_pk')
                if assoc_pk:
                    from associations.models import Association as _Assoc
                    assoc = get_object_or_404(_Assoc, pk=assoc_pk)
                    sent = send_announcement(assoc, subject, body, sender_name)
                    messages.success(request, f'Announcement sent to {sent} member(s) of {assoc.name}.')
                else:
                    from associations.models import Association as _Assoc
                    total = 0
                    for assoc in _Assoc.objects.filter(is_active=True):
                        total += send_announcement(assoc, subject, body, sender_name)
                    messages.success(request, f'Announcement sent to {total} member(s) across all associations.')
            else:
                assoc = request.user.member.association
                sent  = send_announcement(assoc, subject, body, sender_name)
                messages.success(request, f'Announcement sent to {sent} active member(s) of {assoc.name}.')

            return redirect('manage_members')

        return redirect('manage_members')

    # GET — filters
    role_filter   = request.GET.get('role', '')
    status_filter = request.GET.get('status', 'active')
    search        = request.GET.get('q', '').strip()

    qs = base_qs
    if role_filter:
        qs = qs.filter(role=role_filter)
    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)
    if search:
        qs = qs.filter(
            user__first_name__icontains=search
        ) | qs.filter(
            user__last_name__icontains=search
        ) | qs.filter(
            user__username__icontains=search
        )

    qs = qs.order_by('-rating')

    # Summary counts (unfiltered)
    all_members = base_qs
    summary = {
        'total':    all_members.count(),
        'active':   all_members.filter(is_active=True).count(),
        'players':  all_members.filter(role='player').count(),
        'admins':   all_members.filter(role='admin').count(),
        'coaches':  all_members.filter(role='coach').count(),
    }

    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))
    params    = request.GET.copy()
    params.pop('page', None)

    all_associations = Association.objects.filter(is_active=True) if request.user.is_staff else []
    coaches = base_qs.filter(role='coach', is_active=True)

    return render(request, 'members/manage.html', {
        'members': page_obj,
        'page_obj': page_obj,
        'query_string': params.urlencode(),
        'summary': summary,
        'role_choices': Member.ROLE_CHOICES,
        'role_filter': role_filter,
        'status_filter': status_filter,
        'search': search,
        'can_reset_elo': request.user.is_staff,
        'all_associations': all_associations,
        'coaches': coaches,
    })


def player_profile(request, pk):
    member = get_object_or_404(
        Member.objects.select_related('user', 'association'),
        pk=pk, is_active=True,
    )

    # Match stats
    white_matches = Match.objects.filter(white_player=member).exclude(result='pending').select_related('tournament', 'round', 'black_player__user')
    black_matches = Match.objects.filter(black_player=member).exclude(result='pending').select_related('tournament', 'round', 'white_player__user')

    wins   = white_matches.filter(result__in=['white_win','black_forfeit']).count() + black_matches.filter(result__in=['black_win','white_forfeit']).count()
    losses = white_matches.filter(result__in=['black_win','white_forfeit']).count() + black_matches.filter(result__in=['white_win','black_forfeit']).count()
    draws  = white_matches.filter(result='draw').count() + black_matches.filter(result='draw').count()
    games  = wins + losses + draws

    from itertools import chain
    recent_matches = sorted(
        chain(white_matches, black_matches),
        key=lambda m: m.completed_at or m.scheduled_at or m.round.started_at,
        reverse=True
    )[:12]

    # Tournament history
    from tournaments.models import TournamentRegistration
    from tournaments.services import compute_standings
    registrations = member.registrations.select_related('tournament__association').order_by('-tournament__start_date')

    tournament_rows = []
    for reg in registrations:
        t = reg.tournament
        row = {'tournament': t, 'status': reg.status}
        if t.status in ('in_progress', 'completed'):
            standings = compute_standings(t)
            entry = next((s for s in standings if s['player'].pk == member.pk), None)
            if entry:
                row.update({'score': entry['score'], 'rank': entry['rank'], 'total': len(standings)})
        tournament_rows.append(row)

    # Rating history for chart
    history = list(member.rating_history.order_by('recorded_at').values('rating', 'delta', 'recorded_at'))
    import json
    from django.utils.timezone import localtime
    chart_labels = [localtime(h['recorded_at']).strftime('%d %b %Y') for h in history]
    chart_data   = [h['rating'] for h in history]
    # Prepend starting point (1200) if we have history
    if chart_data:
        chart_labels = ['Start (1200)'] + chart_labels
        chart_data   = [1200] + chart_data

    # Annotate each game from this player's point of view, so the template
    # does not carry two near-identical copies of the row, one per colour.
    for m in recent_matches:
        playing_white = m.white_player_id == member.pk
        m.colour = 'White' if playing_white else 'Black'
        m.opponent = m.black_player if playing_white else m.white_player
        won = m.result in (('white_win', 'black_forfeit') if playing_white
                           else ('black_win', 'white_forfeit'))
        lost = m.result in (('black_win', 'white_forfeit') if playing_white
                            else ('white_win', 'black_forfeit'))
        m.outcome = ('Win' if won else 'Loss' if lost
                     else 'Draw' if m.result == 'draw' else m.get_result_display())

    return render(request, 'members/profile.html', {
        'member': member,
        'wins': wins, 'losses': losses, 'draws': draws, 'games': games,
        'win_pct': round(wins / games * 100) if games else 0,
        'recent_matches': recent_matches,
        'tournament_rows': tournament_rows,
        'rating_chart': rating_sparkline(chart_data, chart_labels),
        'is_own_profile': request.user.is_authenticated and hasattr(request.user, 'member') and request.user.member.pk == member.pk,
    })


def rating_sparkline(ratings, labels, width=640, height=160, pad=24):
    """Rating history as plain SVG geometry, computed here and drawn inline.

    The profile page used to pull Chart.js from a CDN, roughly 200 KB of
    JavaScript, to draw one line through a handful of points. On a metered
    bundle in Bulawayo that is most of the cost of the page, for a chart that
    never animates, never gets hovered on a phone, and never changes after
    load.

    This returns the numbers an inline <svg> needs. No library, no script tag,
    no request. It scales to the actual range rather than to zero, because an
    ELO chart anchored at 0 shows a flat line near the top and hides the very
    movement it exists to show.
    """
    if not ratings or len(ratings) < 2:
        return None

    low, high = min(ratings), max(ratings)
    # A dead flat history would divide by zero, and deserves a band anyway.
    span = (high - low) or 40
    low, high = low - span * 0.15, high + span * 0.15
    span = high - low

    step = (width - pad * 2) / (len(ratings) - 1)
    points = []
    for i, value in enumerate(ratings):
        x = pad + i * step
        y = pad + (height - pad * 2) * (1 - (value - low) / span)
        points.append((round(x, 1), round(y, 1)))

    return {
        'width': width,
        'height': height,
        'line': ' '.join(f'{x},{y}' for x, y in points),
        # Closed back along the baseline, for a soft fill under the line.
        'area': (f'{points[0][0]},{height - pad} '
                 + ' '.join(f'{x},{y}' for x, y in points)
                 + f' {points[-1][0]},{height - pad}'),
        'last': points[-1],
        'low': min(ratings),
        'high': max(ratings),
        'first_label': labels[0] if labels else '',
        'last_label': labels[-1] if labels else '',
        'current': ratings[-1],
    }


@login_required
def admin_stats(request):
    if not (request.user.is_staff or (hasattr(request.user, 'member') and request.user.member.role == 'admin')):
        messages.error(request, 'Only administrators can view the stats dashboard.')
        return redirect('dashboard')

    from django.db.models import Sum, Count
    from tournaments.models import Tournament, TournamentRegistration
    from payments.models import Payment
    from matches.models import Match

    # Determine scope
    is_staff = request.user.is_staff
    assoc = None if is_staff else request.user.member.association

    def mfilter(**kw):
        return kw if is_staff else {'association': assoc, **kw}

    def tfilter(**kw):
        return kw if is_staff else {'association': assoc, **kw}

    # ── Members ──────────────────────────────────────────────
    member_qs = Member.objects.filter(**mfilter())
    member_stats = {
        'total':    member_qs.count(),
        'active':   member_qs.filter(is_active=True).count(),
        'players':  member_qs.filter(role='player', is_active=True).count(),
        'coaches':  member_qs.filter(role='coach', is_active=True).count(),
        'admins':   member_qs.filter(role='admin', is_active=True).count(),
    }
    # Computed here, not in the template. The template used to do
    # {{ member_stats.total|add:"-"|add:member_stats.active }}, which is not
    # subtraction: `add` coerces to int, fails on "-", falls back to string
    # concatenation, fails again, and returns the empty string. The Inactive
    # figure on this dashboard has been blank since it was written. It is the
    # same broken idiom that made every tournament's "spots left" blank.
    member_stats['inactive'] = member_stats['total'] - member_stats['active']

    # ── Tournaments ───────────────────────────────────────────
    t_qs = Tournament.objects.filter(**tfilter())
    tournament_stats = {
        'total':      t_qs.count(),
        'active':     t_qs.filter(status__in=['registration_open', 'in_progress']).count(),
        'in_progress':t_qs.filter(status='in_progress').count(),
        'open':       t_qs.filter(status='registration_open').count(),
        'completed':  t_qs.filter(status='completed').count(),
        'upcoming':   t_qs.filter(status='upcoming').count(),
    }

    # ── Registrations ─────────────────────────────────────────
    reg_qs = TournamentRegistration.objects.filter(**({'tournament__association': assoc} if not is_staff else {}))
    reg_stats = {
        'total':     reg_qs.count(),
        'confirmed': reg_qs.filter(status='confirmed').count(),
        'pending':   reg_qs.filter(status='pending').count(),
    }

    # ── Payments ──────────────────────────────────────────────
    pay_qs = Payment.objects.filter(**({'member__association': assoc} if not is_staff else {}))
    revenue = pay_qs.filter(status='completed').aggregate(total=Sum('amount'))['total'] or 0
    payment_stats = {
        'completed':        pay_qs.filter(status='completed').count(),
        'pending':          pay_qs.filter(status__in=['pending', 'awaiting_approval']).count(),
        'revenue':          revenue,
        'revenue_currency': 'USD',
    }

    # ── Matches ───────────────────────────────────────────────
    match_filter = {} if is_staff else {'tournament__association': assoc}
    match_qs = Match.objects.filter(**match_filter)
    match_stats = {
        'total':   match_qs.exclude(result='pending').count(),
        'pending': match_qs.filter(result='pending').count(),
    }

    # ── Recent activity ───────────────────────────────────────
    recent_regs = (
        reg_qs.select_related('player__user', 'tournament')
        .order_by('-registered_at')[:10]
    )
    recent_payments = (
        pay_qs.select_related('member__user', 'tournament')
        .order_by('-created_at')[:8]
    )
    active_tournaments = (
        t_qs.filter(status__in=['registration_open', 'in_progress'])
        .select_related('association')
        .order_by('start_date')
    )

    return render(request, 'members/admin_stats.html', {
        'assoc': assoc,
        'is_staff': is_staff,
        'member_stats': member_stats,
        'tournament_stats': tournament_stats,
        'reg_stats': reg_stats,
        'payment_stats': payment_stats,
        'match_stats': match_stats,
        'recent_regs': recent_regs,
        'recent_payments': recent_payments,
        'active_tournaments': active_tournaments,
    })


@login_required
def change_password(request):
    from django.contrib.auth.forms import PasswordChangeForm
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # keep user logged in
            messages.success(request, 'Password changed successfully.')
            return redirect('dashboard')
    else:
        form = PasswordChangeForm(request.user)
    return render(request, 'members/change_password.html', {'form': form})


# ── Two-Factor Authentication ──────────────────────────────────────────────

def verify_2fa(request):
    """Post-login TOTP verification page."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not hasattr(request.user, 'member') or not request.user.member.totp_enabled:
        return redirect('dashboard')
    if request.session.get('2fa_verified'):
        return redirect(request.GET.get('next', 'dashboard'))

    if request.method == 'POST':
        import pyotp
        locked_until = request.session.get('2fa_locked_until', 0)
        if time.time() < locked_until:
            wait = int(locked_until - time.time())
            messages.error(request, f'Too many failed attempts. Try again in {wait}s.')
            return render(request, 'members/2fa_verify.html', {
                'next': request.GET.get('next', ''),
            })

        code = request.POST.get('code', '').replace(' ', '')
        totp = pyotp.TOTP(request.user.member.totp_secret)
        if totp.verify(code, valid_window=1):
            request.session['2fa_verified'] = True
            request.session.pop('2fa_attempts', None)
            request.session.pop('2fa_locked_until', None)
            next_url = request.POST.get('next') or request.GET.get('next') or 'dashboard'
            return redirect(next_url)

        attempts = request.session.get('2fa_attempts', 0) + 1
        if attempts >= 5:
            request.session['2fa_attempts'] = 0
            request.session['2fa_locked_until'] = time.time() + 60
            messages.error(request, 'Too many failed attempts. Try again in 60s.')
        else:
            request.session['2fa_attempts'] = attempts
            messages.error(request, 'Invalid code. Please try again.')

    return render(request, 'members/2fa_verify.html', {
        'next': request.GET.get('next', ''),
    })


@login_required
def setup_2fa(request):
    """Enable 2FA: generate secret, show QR, confirm with first code."""
    import pyotp, qrcode, io, base64

    member = request.user.member

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'generate':
            secret = pyotp.random_base32()
            request.session['pending_totp_secret'] = secret
            return redirect('setup_2fa')

        if action == 'confirm':
            secret = request.session.get('pending_totp_secret')
            code   = request.POST.get('code', '').replace(' ', '')
            if not secret:
                messages.error(request, 'Session expired. Please start again.')
                return redirect('setup_2fa')
            totp = pyotp.TOTP(secret)
            if totp.verify(code, valid_window=1):
                member.totp_secret  = secret
                member.totp_enabled = True
                member.save(update_fields=['totp_secret', 'totp_enabled'])
                request.session.pop('pending_totp_secret', None)
                request.session['2fa_verified'] = True
                messages.success(request, '2FA enabled. Your account is now protected.')
                return redirect('dashboard')
            messages.error(request, 'Code did not match. Try scanning again or wait for the next code.')

    secret = request.session.get('pending_totp_secret')
    qr_b64 = None

    if secret:
        label = request.user.email or request.user.username
        uri   = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name='ChessHub')
        img   = qrcode.make(uri)
        buf   = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return render(request, 'members/2fa_setup.html', {
        'member':      member,
        'secret':      secret,
        'qr_b64':      qr_b64,
    })


@login_required
def disable_2fa(request):
    """Disable 2FA after password confirmation."""
    if request.method == 'POST':
        password = request.POST.get('password', '')
        if request.user.check_password(password):
            member = request.user.member
            member.totp_enabled = False
            member.totp_secret  = ''
            member.save(update_fields=['totp_enabled', 'totp_secret'])
            request.session.pop('2fa_verified', None)
            messages.success(request, '2FA has been disabled.')
            return redirect('dashboard')
        messages.error(request, 'Incorrect password.')

    return render(request, 'members/2fa_disable.html')


@login_required
def edit_profile(request):
    if not hasattr(request.user, 'member'):
        return redirect('home')

    member = request.user.member
    user   = request.user

    if request.method == 'POST':
        form = ProfileEditForm(request.POST, request.FILES)
        if form.is_valid():
            form.save(user, member)
            messages.success(request, 'Profile updated.')
            return redirect('dashboard')
    else:
        form = ProfileEditForm(initial={
            'first_name':    user.first_name,
            'last_name':     user.last_name,
            'email':         user.email,
            'phone':         member.phone,
            'date_of_birth': member.date_of_birth,
        })

    return render(request, 'members/edit_profile.html', {'form': form, 'member': member})


def rankings(request):
    assoc_filter = request.GET.get('association')
    search       = request.GET.get('q', '').strip()

    # RANKED ON LICHESS, not on this club's own ELO.
    #
    # Percival's call, 2026-09-17: blitz and bullet from Lichess are the
    # ratings this site runs on for now. The club ELO still exists and
    # tournament results still move it, but it is no longer shown anywhere a
    # player looks, so no rating history is lost if this is reversed.
    #
    # Blitz decides the order, falling back to bullet for somebody who only
    # plays bullet: ranking on blitz alone would leave a pure bullet player
    # off the list entirely. Members with no Lichess account have no rating
    # to rank on and sort last rather than being hidden, because they are
    # still members and the fix is one button on their profile.
    qs = ranked_by_lichess(
        Member.objects.select_related('user', 'association', 'user__lichess')
        .filter(role='player', is_active=True)
    )

    if assoc_filter:
        qs = qs.filter(association__pk=assoc_filter)
    if search:
        qs = qs.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(user__username__icontains=search)
        )

    associations = Association.objects.filter(is_active=True)

    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))
    params    = request.GET.copy()
    params.pop('page', None)

    return render(request, 'members/rankings.html', {
        'members': page_obj,
        'page_obj': page_obj,
        'query_string': params.urlencode(),
        'associations': associations,
        'assoc_filter': assoc_filter,
        'search': search,
    })
