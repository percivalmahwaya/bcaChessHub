from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from .models import Tournament, TournamentRegistration, Round, Section
from .forms import TournamentForm
from matches.models import Match
from notifications.email import (
    send_registration_confirmed,
    send_round_pairings,
    send_round_complete,
    send_tournament_complete,
)


def _is_director(user, tournament):
    """True if user may manage this tournament."""
    if user.is_staff:
        return True
    if hasattr(user, 'member'):
        m = user.member
        return m.role == 'admin' and m.association_id == tournament.association_id
    return False


def tournament_list(request):
    status_filter = request.GET.get('status')
    qs = Tournament.objects.select_related('association').order_by('-start_date')
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 12)
    page_obj  = paginator.get_page(request.GET.get('page'))
    params    = request.GET.copy()
    params.pop('page', None)

    return render(request, 'tournaments/list.html', {
        'tournaments': page_obj,
        'page_obj': page_obj,
        'query_string': params.urlencode(),
        'status_filter': status_filter,
    })


def tournament_detail(request, pk):
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    registrations = tournament.registrations.select_related('player__user').order_by('seed_number', 'registered_at')
    rounds = tournament.rounds.prefetch_related('matches__white_player__user', 'matches__black_player__user').all()

    already_registered = False
    my_registration = None
    my_payment = None

    if request.user.is_authenticated and hasattr(request.user, 'member'):
        member = request.user.member
        my_registration = registrations.filter(player=member).first()
        already_registered = my_registration is not None
        if already_registered and tournament.registration_fee > 0:
            from payments.models import Payment
            my_payment = Payment.objects.filter(
                member=member, tournament=tournament
            ).order_by('-created_at').first()

    # Real standings for the sidebar. The old template labelled a box
    # "Standings" and filled it with the first ten REGISTRATIONS in signup
    # order, numbered 1 to 10, which is not a standing at all: a player who
    # entered first and lost every game appeared top of something called
    # Standings. Now computed, and only once play has begun.
    top_standings = []
    if tournament.status in ('in_progress', 'completed'):
        from .services import compute_standings
        top_standings = compute_standings(tournament)[:5]

    return render(request, 'tournaments/detail.html', {
        'tournament': tournament,
        'registrations': registrations,
        'rounds': rounds,
        'already_registered': already_registered,
        'my_registration': my_registration,
        'my_payment': my_payment,
        'top_standings': top_standings,
    })


@login_required
def tournament_register(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if request.method != 'POST':
        return redirect('tournament_detail', pk=pk)

    if not hasattr(request.user, 'member'):
        messages.error(request, 'You need a member profile to register for tournaments.')
        return redirect('tournament_detail', pk=pk)

    member = request.user.member

    if tournament.status != 'registration_open':
        messages.error(request, 'Registration is not currently open for this tournament.')
        return redirect('tournament_detail', pk=pk)

    if tournament.is_full:
        messages.error(request, 'This tournament is full.')
        return redirect('tournament_detail', pk=pk)

    _, created = TournamentRegistration.objects.get_or_create(
        tournament=tournament,
        player=member,
        defaults={'status': 'pending'}
    )

    if not created:
        messages.info(request, 'You are already registered for this tournament.')
        return redirect('tournament_detail', pk=pk)

    # Paid tournament → send player straight to the payment page
    if tournament.registration_fee > 0:
        messages.info(request, f'Complete your payment to confirm your spot in {tournament.name}.')
        return redirect('payment_initiate', tournament_pk=pk)

    messages.success(request, f'You have registered for {tournament.name}. Awaiting confirmation.')
    return redirect('tournament_detail', pk=pk)


def _resolve_section(request, tournament):
    """The section a viewer is looking at, from ?section=<pk>.

    Returns (section, sections). `section` is None for "everything", which is
    both the whole tournament when it has no sections and the deliberate
    all-sections view when it does.

    An unknown or foreign pk falls back to None rather than 404ing: a stale
    link from last season should show the tournament, not an error page.
    """
    sections = list(tournament.sections.all())
    raw = request.GET.get('section')
    if not raw or not sections:
        return None, sections
    return next((s for s in sections if str(s.pk) == str(raw)), None), sections


def tournament_standings(request, pk):
    """Public standings table for a tournament."""
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    from .services import compute_standings
    section, sections = _resolve_section(request, tournament)
    standings = compute_standings(tournament, section=section)
    rounds = tournament.rounds.order_by('number')
    return render(request, 'tournaments/standings.html', {
        'tournament': tournament,
        'standings': standings,
        'rounds': rounds,
        'section': section,
        'sections': sections,
    })


def tournament_crosstable(request, pk):
    """Public crosstable view for a tournament."""
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    from .services import compute_crosstable
    section, sections = _resolve_section(request, tournament)

    # With sections and none chosen, default to the first rather than drawing
    # an N by N grid in which most cells can never be filled. That is not a
    # sparse crosstable, it is three crosstables laid on top of each other.
    if sections and section is None:
        section = sections[0]

    crosstable = compute_crosstable(tournament, section=section)
    rounds = tournament.rounds.order_by('number')
    return render(request, 'tournaments/crosstable.html', {
        'tournament': tournament,
        'crosstable': crosstable,
        'rounds': rounds,
        'section': section,
        'sections': sections,
    })


def tournament_round(request, pk, round_number):
    """Public pairings view for a single round."""
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    round_obj = get_object_or_404(Round, tournament=tournament, number=round_number)
    matches = (
        Match.objects
        .filter(round=round_obj)
        .select_related('white_player__user', 'black_player__user')
        .order_by('board_number')
    )
    rounds = tournament.rounds.order_by('number')
    section, sections = _resolve_section(request, tournament)
    if section is not None:
        matches = matches.filter(section=section)
    viewer = (request.user.member
              if request.user.is_authenticated and hasattr(request.user, 'member')
              else None)

    # Annotated here rather than worked out in the template.
    #
    # round.html used to do this with
    #     {% with is_viewer_white=viewer_member and viewer_member == match.white_player %}
    # and Django's `with` tag accepts no boolean operators and no comparisons,
    # only plain values and filters. The tag failed to parse, which left its
    # `{% endwith %}` as an unknown block tag, which raised TemplateSyntaxError
    # at render time, which meant EVERY round pairings page on this site
    # returned a hard 500. That is the page a player opens on a Saturday to
    # find out which board they are on.
    #
    # Nothing caught it because nothing ever rendered this template: no test
    # covered it, and the URL is /rounds/<n>/ rather than /round/<n>/, so a
    # casual check got a 404 and looked merely missing rather than broken.
    for match in matches:
        match.viewer_is_white = viewer is not None and viewer == match.white_player
        match.viewer_is_black = viewer is not None and viewer == match.black_player

    return render(request, 'tournaments/round.html', {
        'tournament': tournament,
        'round': round_obj,
        'matches': matches,
        'rounds': rounds,
        'viewer_member': viewer,
        'section': section,
        'sections': sections,
    })


@login_required
def tournament_manage(request, pk):
    """Tournament director panel — start rounds, record results, confirm registrations."""
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)

    if not _is_director(request.user, tournament):
        messages.error(request, 'You do not have permission to manage this tournament.')
        return redirect('tournament_detail', pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'set_status':
            new_status = request.POST.get('status')
            valid = [s[0] for s in Tournament.STATUS_CHOICES]
            if new_status in valid:
                tournament.status = new_status
                tournament.save(update_fields=['status'])
                messages.success(request, f'Status changed to "{tournament.get_status_display()}".')
                if new_status == 'completed':
                    try:
                        send_tournament_complete(tournament)
                    except Exception:
                        pass

        elif action == 'confirm_reg':
            reg = get_object_or_404(TournamentRegistration, pk=request.POST.get('reg_pk'), tournament=tournament)
            reg.status = 'confirmed'
            reg.save(update_fields=['status'])
            messages.success(request, f'{reg.player} confirmed.')
            try:
                send_registration_confirmed(reg)
            except Exception:
                pass

        elif action == 'confirm_all':
            pending = list(
                TournamentRegistration.objects.filter(
                    tournament=tournament, status='pending'
                ).select_related('player__user')
            )
            count = TournamentRegistration.objects.filter(
                tournament=tournament, status='pending'
            ).update(status='confirmed')
            messages.success(request, f'{count} registration(s) confirmed.')
            for reg in pending:
                reg.status = 'confirmed'
                try:
                    send_registration_confirmed(reg)
                except Exception:
                    pass

        elif action == 'set_seeds':
            if tournament.rounds.exists():
                messages.error(request, 'Seeds cannot be changed after rounds have started.')
            else:
                confirmed = list(
                    TournamentRegistration.objects.filter(tournament=tournament, status='confirmed')
                )
                seen, updates, errors = set(), [], []
                for reg in confirmed:
                    raw = request.POST.get(f'seed_{reg.pk}', '').strip()
                    if not raw:
                        continue
                    try:
                        seed = int(raw)
                        if seed < 1:
                            raise ValueError
                    except ValueError:
                        errors.append(f'Invalid seed value for {reg.player}.')
                        continue
                    if seed in seen:
                        errors.append(f'Duplicate seed #{seed} — each player needs a unique number.')
                        continue
                    seen.add(seed)
                    updates.append((reg, seed))
                if errors:
                    for e in errors:
                        messages.error(request, e)
                else:
                    for reg, seed in updates:
                        reg.seed_number = seed
                        reg.save(update_fields=['seed_number'])
                    messages.success(request, f'Seeds saved for {len(updates)} player(s).')

        elif action == 'auto_seed':
            if tournament.rounds.exists():
                messages.error(request, 'Seeds cannot be changed after rounds have started.')
            else:
                confirmed = list(
                    TournamentRegistration.objects.filter(tournament=tournament, status='confirmed')
                    .select_related('player')
                    .order_by('-player__rating')
                )
                for i, reg in enumerate(confirmed, start=1):
                    reg.seed_number = i
                    reg.save(update_fields=['seed_number'])
                messages.success(request, f'{len(confirmed)} players auto-seeded by ELO (highest = seed 1).')

        elif action == 'start_round':
            from .services import create_next_round
            try:
                round_obj, pairings, bye_player, errors, bye_players = create_next_round(tournament)
                messages.success(
                    request,
                    f'Round {round_obj.number} started, {len(pairings)} board(s).')
                # A bye is per section, so a three-section event with an odd
                # turnout in each produces three of them. Naming only the first
                # would leave two players wondering why they have no game.
                for b in bye_players:
                    messages.info(request, f'{b} receives a full-point bye.')
                for err in errors:
                    messages.warning(request, err)
                try:
                    send_round_pairings(round_obj)
                except Exception:
                    pass
            except ValueError as exc:
                messages.error(request, str(exc))

        elif action == 'complete_round':
            round_obj = get_object_or_404(Round, pk=request.POST.get('round_pk'), tournament=tournament)
            from .services import complete_round
            try:
                complete_round(round_obj)
                messages.success(request, f'Round {round_obj.number} marked complete.')
                try:
                    send_round_complete(round_obj)
                except Exception:
                    pass
            except ValueError as exc:
                messages.error(request, str(exc))

        elif action == 'add_section':
            name = (request.POST.get('section_name') or '').strip()
            if not name:
                messages.error(request, 'A section needs a name.')
            elif tournament.rounds.exists():
                # Adding a section mid-event would leave its players with no
                # games in the rounds already paired, and a standings table
                # that cannot be compared with anybody else's.
                messages.error(
                    request,
                    'Rounds have already been paired, so sections can no '
                    'longer be changed. Sections split the field before play '
                    'begins.')
            elif tournament.sections.filter(name__iexact=name).exists():
                messages.error(request, f'There is already a "{name}" section.')
            else:
                Section.objects.create(
                    tournament=tournament, name=name,
                    eligibility=(request.POST.get('eligibility') or '').strip(),
                    order=tournament.sections.count() + 1)
                messages.success(request, f'Section "{name}" added.')

        elif action == 'delete_section':
            section = get_object_or_404(
                Section, pk=request.POST.get('section_pk'), tournament=tournament)
            if tournament.rounds.exists():
                messages.error(
                    request, 'Rounds have been paired. Sections can no longer '
                             'be changed.')
            else:
                # SET_NULL on the registration, so entries survive and simply
                # fall back to having no section. Deleting a section must
                # never delete the people in it.
                name = section.name
                section.delete()
                messages.success(
                    request,
                    f'Section "{name}" removed. Its players are still entered, '
                    'with no section.')

        elif action == 'assign_sections':
            if tournament.rounds.exists():
                messages.error(
                    request, 'Rounds have been paired. Players can no longer '
                             'be moved between sections.')
            else:
                moved = 0
                for reg in tournament.registrations.all():
                    raw = request.POST.get(f'section_{reg.pk}')
                    new_section = None
                    if raw:
                        new_section = tournament.sections.filter(pk=raw).first()
                    if reg.section_id != (new_section.pk if new_section else None):
                        reg.section = new_section
                        reg.save(update_fields=['section'])
                        moved += 1
                if moved:
                    messages.success(request, f'{moved} player(s) reassigned.')
                else:
                    messages.info(request, 'No changes to make.')

        return redirect('tournament_manage', pk=pk)

    # GET — build context
    registrations = list(
        tournament.registrations.select_related('player__user').order_by('status', 'registered_at')
    )

    # Annotate each registration with payment status (only relevant when fee > 0)
    if tournament.registration_fee > 0:
        from payments.models import Payment
        paid_pks = set(
            Payment.objects.filter(tournament=tournament, status='completed')
            .values_list('member_id', flat=True)
        )
        for reg in registrations:
            reg.has_paid = reg.player_id in paid_pks
    else:
        for reg in registrations:
            reg.has_paid = True  # free tournament — treat everyone as paid
    rounds = list(
        tournament.rounds.prefetch_related(
            'matches__white_player__user',
            'matches__black_player__user',
        ).order_by('number')
    )
    current_round = next((r for r in rounds if not r.is_complete), None)
    completed_rounds = tournament.rounds.filter(is_complete=True).count()
    can_start_round = (
        tournament.status in ('registration_open', 'in_progress')
        and current_round is None
        and completed_rounds < tournament.num_rounds
        and tournament.registrations.filter(status='confirmed').count() >= 2
    )

    no_rounds_yet = len(rounds) == 0
    confirmed_seeding = sorted(
        [r for r in registrations if r.status == 'confirmed'],
        key=lambda r: (r.seed_number or 9999, -r.player.rating),
    )

    return render(request, 'tournaments/manage.html', {
        'tournament': tournament,
        'registrations': registrations,
        'rounds': rounds,
        'current_round': current_round,
        'can_start_round': can_start_round,
        'result_choices': [rc for rc in Match.RESULT_CHOICES if rc[0] != 'pending'],
        'status_choices': Tournament.STATUS_CHOICES,
        'next_round_number': completed_rounds + 1,
        'no_rounds_yet': no_rounds_yet,
        'confirmed_seeding': confirmed_seeding,
        'sections': list(tournament.sections.all()),
        # Sections split the field BEFORE play. Once a round is paired,
        # moving somebody would leave them with no games in the rounds already
        # played and a table nobody can be compared against.
        'sections_locked': tournament.rounds.exists(),
    })


@login_required
@require_POST
def tournament_record_result(request, pk, match_pk):
    """Record a match result from the director panel."""
    tournament = get_object_or_404(Tournament, pk=pk)

    if not _is_director(request.user, tournament):
        messages.error(request, 'Permission denied.')
        return redirect('tournament_manage', pk=pk)

    match = get_object_or_404(Match, pk=match_pk, tournament=tournament)

    if match.black_player is None:
        messages.error(request, 'Cannot override a bye result.')
        return redirect('tournament_manage', pk=pk)

    result = request.POST.get('result')
    valid = [rc[0] for rc in Match.RESULT_CHOICES if rc[0] != 'pending']
    if result not in valid:
        messages.error(request, f'Invalid result value.')
        return redirect('tournament_manage', pk=pk)

    if match.result != 'pending':
        messages.warning(request, f'Board {match.board_number} result already recorded ({match.result}).')
        return redirect('tournament_manage', pk=pk)

    match.record_result(result)
    messages.success(request, f'Board {match.board_number}: {match.get_result_display()} saved.')
    return redirect('tournament_manage', pk=pk)


def export_standings_csv(request, pk):
    """Download final standings as CSV."""
    import csv
    from django.http import HttpResponse
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    from .services import compute_standings
    standings = compute_standings(tournament)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{tournament.name}_standings.csv"'
    writer = csv.writer(response)
    writer.writerow(['Rank', 'Player', 'Association', 'Score', 'ELO Rating'])
    for entry in standings:
        p = entry['player']
        writer.writerow([
            entry['rank'],
            p.user.get_full_name() or p.user.username,
            p.association.name,
            entry['score'],
            p.rating,
        ])
    return response


def export_pairings_csv(request, pk):
    """Download all round pairings and results as CSV."""
    import csv
    from django.http import HttpResponse
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    rounds = tournament.rounds.prefetch_related(
        'matches__white_player__user',
        'matches__black_player__user',
    ).order_by('number')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{tournament.name}_pairings.csv"'
    writer = csv.writer(response)
    writer.writerow(['Round', 'Board', 'White', 'Black', 'Result'])
    for round_obj in rounds:
        for match in round_obj.matches.order_by('board_number'):
            writer.writerow([
                round_obj.number,
                match.board_number,
                match.white_player.user.get_full_name() or match.white_player.user.username,
                match.black_player.user.get_full_name() if match.black_player else 'BYE',
                match.get_result_display(),
            ])
    return response


def export_print(request, pk):
    """Print-friendly full tournament report (browser Print → Save as PDF)."""
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)
    from .services import compute_standings, compute_crosstable

    # ONE REPORT PER SECTION, not one report.
    #
    # Sections shipped on 2026-09-16 and this view was missed, so a tournament
    # split into Open, Ladies and Developmental printed a SINGLE standings
    # table mixing all three, and a single crosstable whose cells mostly could
    # never be filled because those players never meet. That printed sheet is
    # the prize list read out at the end of the day, so it is the one place
    # the mixing actually costs somebody a trophy.
    sections = list(tournament.sections.all()) if hasattr(tournament, 'sections') else []
    if sections:
        blocks = [{
            'title': section.name,
            'eligibility': section.eligibility,
            'standings': compute_standings(tournament, section=section),
            'crosstable': compute_crosstable(tournament, section=section),
        } for section in sections]
    else:
        blocks = [{
            'title': '',
            'eligibility': '',
            'standings': compute_standings(tournament),
            'crosstable': compute_crosstable(tournament),
        }]

    rounds = tournament.rounds.prefetch_related(
        'matches__white_player__user',
        'matches__black_player__user',
    ).order_by('number')
    return render(request, 'tournaments/print.html', {
        'tournament': tournament,
        'blocks': blocks,
        'sectioned': bool(sections),
        'rounds': rounds,
    })


def _can_create(user):
    """True if user may create tournaments."""
    if user.is_staff:
        return True
    return hasattr(user, 'member') and user.member.role == 'admin'


@login_required
def create_tournament(request):
    if not _can_create(request.user):
        messages.error(request, 'Only administrators can create tournaments.')
        return redirect('tournament_list')

    if request.method == 'POST':
        form = TournamentForm(request.POST)
        if form.is_valid():
            tournament = form.save(commit=False)
            # Staff can set any association; admins default to their own
            if request.user.is_staff:
                from associations.models import Association
                assoc_pk = request.POST.get('association')
                tournament.association = get_object_or_404(Association, pk=assoc_pk)
            else:
                tournament.association = request.user.member.association
            tournament.created_by = request.user.member if hasattr(request.user, 'member') else None
            tournament.save()
            messages.success(request, f'"{tournament.name}" created successfully.')
            return redirect('tournament_manage', pk=tournament.pk)
    else:
        form = TournamentForm()

    associations = []
    if request.user.is_staff:
        from associations.models import Association
        associations = Association.objects.filter(is_active=True)

    return render(request, 'tournaments/form.html', {
        'form': form,
        'associations': associations,
        'action': 'Create',
        'title': 'Create Tournament',
    })


@login_required
def edit_tournament(request, pk):
    tournament = get_object_or_404(Tournament.objects.select_related('association'), pk=pk)

    if not _is_director(request.user, tournament):
        messages.error(request, 'You do not have permission to edit this tournament.')
        return redirect('tournament_detail', pk=pk)

    if request.method == 'POST':
        form = TournamentForm(request.POST, instance=tournament)
        if form.is_valid():
            form.save()
            messages.success(request, 'Tournament updated.')
            return redirect('tournament_manage', pk=pk)
    else:
        form = TournamentForm(instance=tournament)

    return render(request, 'tournaments/form.html', {
        'form': form,
        'tournament': tournament,
        'action': 'Save Changes',
        'title': f'Edit — {tournament.name}',
    })
