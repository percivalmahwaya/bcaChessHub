from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from .models import Association
from members.models import Member, ranked_by_lichess
from tournaments.models import Tournament
from matches.models import Match


def club_list(request):
    associations = Association.objects.filter(is_active=True)

    rows = []
    for assoc in associations:
        rows.append({
            'assoc': assoc,
            'member_count': Member.objects.filter(association=assoc, is_active=True).count(),
            'tournament_count': Tournament.objects.filter(association=assoc).count(),
            'active_tournaments': Tournament.objects.filter(
                association=assoc, status__in=('registration_open', 'in_progress')
            ).count(),
        })

    return render(request, 'associations/list.html', {'rows': rows})


def club_detail(request, pk):
    assoc = get_object_or_404(Association, pk=pk, is_active=True)

    members = ranked_by_lichess(
        Member.objects.filter(association=assoc, is_active=True)
        .select_related('user', 'user__lichess')
    )

    top_players = members.filter(role='player')[:8]

    active_tournaments = Tournament.objects.filter(
        association=assoc, status__in=('registration_open', 'in_progress')
    ).order_by('start_date')

    recent_tournaments = Tournament.objects.filter(
        association=assoc, status='completed'
    ).order_by('-end_date')[:5]

    upcoming_tournaments = Tournament.objects.filter(
        association=assoc, status='upcoming'
    ).order_by('start_date')[:3]

    games_played = Match.objects.filter(
        tournament__association=assoc, black_player__isnull=False
    ).exclude(result='pending').count()

    stats = {
        'members':     members.count(),
        'players':     members.filter(role='player').count(),
        'coaches':     members.filter(role='coach').count(),
        'tournaments': Tournament.objects.filter(association=assoc).count(),
        'games':       games_played,
    }

    return render(request, 'associations/detail.html', {
        'assoc': assoc,
        'stats': stats,
        'top_players': top_players,
        'active_tournaments': active_tournaments,
        'upcoming_tournaments': upcoming_tournaments,
        'recent_tournaments': recent_tournaments,
    })


def club_contact(request, pk):
    assoc = get_object_or_404(Association, pk=pk, is_active=True)

    if request.method == 'POST':
        name    = request.POST.get('name', '').strip()
        email   = request.POST.get('email', '').strip()
        subject = request.POST.get('subject', '').strip()
        body    = request.POST.get('body', '').strip()

        if not (name and email and subject and body):
            messages.error(request, 'Please fill in all fields.')
        else:
            full_subject = f'[ChessHub Contact] {subject}'
            full_body = (
                f'Message from: {name} <{email}>\n'
                f'Association page: {assoc.name}\n\n'
                f'{body}'
            )
            send_mail(
                subject=full_subject,
                message=full_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[assoc.email],
                fail_silently=True,
            )
            messages.success(request, f'Your message has been sent to {assoc.name}. They will get back to you shortly.')
            return redirect('club_detail', pk=assoc.pk)

    return render(request, 'associations/contact.html', {'assoc': assoc})
