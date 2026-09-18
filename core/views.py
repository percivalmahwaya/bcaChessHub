import re
from functools import lru_cache

from django.conf import settings
from django.shortcuts import render
from tournaments.models import Tournament
from django.utils import timezone

from members.models import Member, ranked_by_lichess
from news.models import Article
from matches.models import Match
from associations.models import Association


def home(request):
    upcoming = Tournament.objects.select_related('association').filter(
        status__in=['upcoming', 'registration_open']
    ).order_by('start_date')[:3]

    top_players = ranked_by_lichess(
        Member.objects.select_related('user', 'association', 'user__lichess')
        .filter(role='player', is_active=True)
    )[:5]

    stats = {
        'members': Member.objects.filter(is_active=True).count(),
        'tournaments': Tournament.objects.count(),
        'matches': Match.objects.exclude(result='pending').count(),
        'associations': Association.objects.filter(is_active=True).count(),
    }

    # The same two gates the news section itself uses: published, and not
    # dated into the future. Written out here rather than reaching into
    # news.views, because a home page quietly bypassing the future-dating gate
    # would leak Saturday's results onto the front page on Thursday.
    latest_news = Article.objects.filter(
        is_published=True, published_at__lte=timezone.now()
    )[:4]

    return render(request, 'home.html', {
        'upcoming': upcoming,
        'top_players': top_players,
        'latest_news': latest_news,
        'stats': stats,
    })


@lru_cache(maxsize=1)
def _test_count():
    """How many automated tests this project actually has.

    Counted, never typed. The page used to state "69 automated tests" as a
    literal, and the suite was at 82 by the time anyone looked: a number that
    was true on the day it was written and decayed silently from then on,
    which on a page whose entire promise is "every claim here is checkable" is
    worse than saying nothing.

    Cached for the process. This walks six small files, but not on every
    request to a public page.
    """
    total = 0
    for path in sorted(settings.BASE_DIR.glob("*/tests.py")):
        total += len(re.findall(r"def test_", path.read_text(encoding="utf-8")))
    return total


def security(request):
    """
    Public security and trust page.

    Deliberately close to static: it describes controls that are implemented,
    and names the ones that are not. Every claim on it should be traceable to
    code, and if a control is removed the claim must come off this page in the
    same commit.
    """
    return render(request, 'security.html', {
        'test_count': _test_count(),
        # qa_production.py's own count. Passed in rather than written into the
        # template so both numbers on that page come from one place.
        'deploy_check_count': 29,
    })
