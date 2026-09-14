from django.shortcuts import render
from tournaments.models import Tournament
from members.models import Member
from matches.models import Match
from associations.models import Association


def home(request):
    upcoming = Tournament.objects.select_related('association').filter(
        status__in=['upcoming', 'registration_open']
    ).order_by('start_date')[:3]

    top_players = Member.objects.select_related('user', 'association').filter(
        role='player', is_active=True
    ).order_by('-rating')[:5]

    stats = {
        'members': Member.objects.filter(is_active=True).count(),
        'tournaments': Tournament.objects.count(),
        'matches': Match.objects.exclude(result='pending').count(),
        'associations': Association.objects.filter(is_active=True).count(),
    }

    return render(request, 'home.html', {
        'upcoming': upcoming,
        'top_players': top_players,
        'stats': stats,
    })


def security(request):
    """
    Public security and trust page.

    Deliberately static: it describes controls that are implemented, and names
    the ones that are not. Every claim on it should be traceable to code — if a
    control is removed, the claim must come off this page in the same commit.
    """
    return render(request, 'security.html')
