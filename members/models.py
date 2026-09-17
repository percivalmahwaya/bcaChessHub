from django.db import models
from django.db.models import F
from django.db.models.functions import Coalesce
from django.contrib.auth.models import User
from associations.models import Association


class Member(models.Model):
    ROLE_CHOICES = [
        ('player', 'Player'),
        ('coach', 'Coach'),
        ('admin', 'Administrator'),
        ('parent', 'Parent'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='member')
    association = models.ForeignKey(Association, on_delete=models.CASCADE, related_name='members')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='player')
    phone = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(blank=True, null=True)
    rating = models.IntegerField(default=1200)  # ELO rating, starts at 1200
    rank = models.CharField(max_length=50, blank=True)
    profile_photo = models.ImageField(upload_to='member_photos/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    totp_secret  = models.CharField(max_length=64, blank=True)
    totp_enabled = models.BooleanField(default=False)

    coach = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='coached_players', limit_choices_to={'role': 'coach'}
    )
    # Parent-child relationship for parental monitoring (from UML)
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='children', limit_choices_to={'role': 'parent'}
    )

    class Meta:
        ordering = ['-rating']

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.association.name})"

    def update_rating(self, new_rating, match=None):
        delta = new_rating - self.rating
        self.rating = new_rating
        self.save(update_fields=['rating'])
        RatingHistory.objects.create(
            member=self,
            rating=new_rating,
            delta=delta,
            match=match,
        )


class RatingHistory(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='rating_history')
    rating = models.IntegerField()
    delta = models.IntegerField(default=0)
    match = models.ForeignKey(
        'matches.Match', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rating_changes',
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['recorded_at']

    def __str__(self):
        sign = '+' if self.delta >= 0 else ''
        return f"{self.member} → {self.rating} ({sign}{self.delta})"

class LichessAccount(models.Model):
    """A verified link between a site login and a Lichess account.

    ATTACHED TO USER, NOT MEMBER, on purpose. Signing in with Lichess creates
    the login first; the Member profile comes afterwards, once the player has
    chosen which club they belong to. Hanging this off Member would make the
    signup order impossible.

    THE RATINGS HERE ARE NOT THIS SITE'S RATINGS. Member.rating is the
    association's own ELO, earned over the board in a hall in Bulawayo.
    These are online ratings earned somewhere else, against different people,
    at different time controls. They sit side by side and are always labelled;
    they are never averaged, compared or substituted.

    NO ACCESS TOKEN IS STORED. OAuth runs once to prove the person owns the
    account, and the token is discarded. Every refresh after that reads the
    PUBLIC Lichess profile, so there is nothing here to leak and nothing to
    expire.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='lichess')

    # Lichess ids are the lowercased username and are stable; the username
    # itself keeps its display capitalisation and can in principle change.
    lichess_id = models.CharField(max_length=40, unique=True)
    username = models.CharField(max_length=40)
    title = models.CharField(max_length=10, blank=True)   # GM, IM, NM, WFM

    linked_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    # Rating is NULL when the format has never been played. Lichess reports
    # 2500 with games=0 for an untouched format, and storing that placeholder
    # would mean every template had to remember to suppress it. One of them
    # eventually would not.
    bullet_rating = models.IntegerField(null=True, blank=True)
    bullet_games = models.PositiveIntegerField(default=0)
    bullet_provisional = models.BooleanField(default=False)

    blitz_rating = models.IntegerField(null=True, blank=True)
    blitz_games = models.PositiveIntegerField(default=0)
    blitz_provisional = models.BooleanField(default=False)

    rapid_rating = models.IntegerField(null=True, blank=True)
    rapid_games = models.PositiveIntegerField(default=0)
    rapid_provisional = models.BooleanField(default=False)

    total_games = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    draws = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['username']

    def __str__(self):
        return f'lichess.org/@/{self.username}'

    @property
    def profile_url(self):
        return f'https://lichess.org/@/{self.username}'

    # The formats this club rates on. Rapid is still fetched and still stored,
    # it is simply not one of the two Percival chose to run the site on as of
    # 2026-09-17. Adding it back is this tuple and nothing else.
    SHOWN = ('blitz', 'bullet')

    def ratings(self):
        """The ratings this club uses, in a shape a template can loop over.

        Blitz first, because it is the one the rankings are ordered on.

        Formats never played are left out entirely rather than shown as a
        dash, because a row of dashes reads as missing data when the truth is
        that the player simply does not play bullet.
        """
        out = []
        for perf in self.SHOWN:
            rating = getattr(self, f'{perf}_rating')
            if rating is None:
                continue
            out.append({
                'perf': perf,
                'rating': rating,
                'games': getattr(self, f'{perf}_games'),
                'provisional': getattr(self, f'{perf}_provisional'),
            })
        return out

    @property
    def ranking_rating(self):
        """The single number this player is ranked on.

        Blitz, falling back to bullet for somebody who only plays bullet.
        Provisional ratings count here, unlike best_rating: leaving a new
        player out of the rankings entirely is worse than listing them with a
        figure marked provisional, and the alternative is an empty list until
        everybody has played thirty games.

        None for a member with no Lichess account, who sorts last.
        """
        return self.blitz_rating if self.blitz_rating is not None else self.bullet_rating

    @property
    def best_rating(self):
        """The strongest ESTABLISHED rating, for a one-number summary.

        Provisional ratings are excluded: a 2500 after three games is not a
        player's strength, and putting it in a list next to established
        ratings would rank them above people who have earned theirs.
        """
        established = [r for r in self.ratings() if not r['provisional']]
        return max(established, key=lambda r: r['rating']) if established else None

    def apply(self, data):
        """Copy an extracted profile onto this row. Does not save."""
        from django.utils import timezone
        for field, value in data.items():
            if field == 'lichess_id':
                continue          # identity never changes under us
            setattr(self, field, value)
        self.synced_at = timezone.now()


def ranked_by_lichess(queryset):
    """Order members by the rating this site actually shows.

    ONE copy of this ordering. It was about to be written out in four places
    (the ratings page, the home page top five, a club's player list and a
    coach's players), and four copies of a rule is how the ELO formula came to
    disagree with itself: see matches/rating.py.

    Blitz decides it, falling back to bullet for a player who only plays
    bullet. Members with no Lichess account sort LAST rather than first, which
    is what a plain descending sort on NULL would do on some databases, and
    they are kept in the list rather than filtered out because they are still
    members. Username breaks ties so the order is stable between page loads
    rather than reshuffling under the reader.
    """
    return queryset.annotate(
        rank_rating=Coalesce('user__lichess__blitz_rating',
                             'user__lichess__bullet_rating'),
    ).order_by(F('rank_rating').desc(nulls_last=True), 'user__username')
