"""
Site search.

WHAT IS SEARCHABLE AND WHY THAT LIST IS SHORT
=============================================
Players, tournaments, clubs and news. Those are the four things a visitor
arrives looking for, and all four are already public pages. Nothing here
reaches into payments, notifications, challenges or member contact details:
search is a faster route to pages somebody could already open, never a new
way to see something they could not.

THE ONE THING THAT COULD ACTUALLY GO WRONG
==========================================
News has TWO visibility gates and only one of them is obvious. An article is
hidden unless `is_published` is set AND `published_at` has passed, because an
editor writes up a Saturday event on Thursday and dates it forward. A search
that filters on `is_published` alone looks completely correct, returns only
articles somebody deliberately published, and leaks Saturday's results on
Thursday.

The home page had to relearn this on 2026-09-18. The gates are written out
here rather than borrowed from `news.views`, for the same reason they were
written out there: a shortcut that quietly drops a gate is invisible at the
call site.

WHY icontains AND NOT FULL TEXT SEARCH
======================================
Postgres full-text search is better and is not portable: development runs on
SQLite and production on Postgres, and this project has already shipped a
test that passed on SQLite and would have failed in production. `icontains`
behaves the same on both. At this site's size, a few hundred members and a
few dozen tournaments, the difference is unmeasurable.
"""
from django.db.models import Q
from django.utils import timezone

# Below this, a query matches a large fraction of the site and the results
# page is noise. "a" would return almost every member.
MIN_QUERY = 2

# Per category. A search page is a signpost, not a report.
LIMIT = 8


def _members(query):
    from members.models import Member, ranked_by_lichess
    return ranked_by_lichess(
        Member.objects.select_related('user', 'association', 'user__lichess')
        .filter(is_active=True)
        .filter(Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
                | Q(user__username__icontains=query))
    )[:LIMIT]


def _tournaments(query):
    from tournaments.models import Tournament
    return (Tournament.objects.select_related('association')
            .filter(Q(name__icontains=query)
                    | Q(location__icontains=query)
                    | Q(association__name__icontains=query))
            .order_by('-start_date')[:LIMIT])


def _clubs(query):
    from associations.models import Association
    return (Association.objects.filter(is_active=True)
            .filter(Q(name__icontains=query) | Q(city__icontains=query))
            .order_by('name')[:LIMIT])


def _news(query):
    from news.models import Article
    return (Article.objects
            # BOTH gates. See the module docstring: dropping the second one
            # publishes every future-dated article the moment it is written.
            .filter(is_published=True, published_at__lte=timezone.now())
            .filter(Q(title__icontains=query)
                    | Q(summary__icontains=query)
                    | Q(body__icontains=query))
            .order_by('-published_at')[:LIMIT])


def search(query):
    """Grouped results for a query, or empty groups if it is too short.

    Returns a list of groups rather than a dict so the template renders them
    in a fixed, meaningful order: people first, because a name is what most
    searches here actually are.
    """
    query = (query or '').strip()
    if len(query) < MIN_QUERY:
        return [], 0

    groups = [
        {'label': 'Players', 'kind': 'player', 'items': list(_members(query))},
        {'label': 'Tournaments', 'kind': 'tournament',
         'items': list(_tournaments(query))},
        {'label': 'Clubs', 'kind': 'club', 'items': list(_clubs(query))},
        {'label': 'News', 'kind': 'news', 'items': list(_news(query))},
    ]
    total = sum(len(g['items']) for g in groups)
    return [g for g in groups if g['items']], total
