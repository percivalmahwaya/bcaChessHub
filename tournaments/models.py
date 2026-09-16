from django.db import models
from associations.models import Association
from members.models import Member


class Tournament(models.Model):
    FORMAT_CHOICES = [
        ('swiss', 'Swiss System'),
        ('round_robin', 'Round Robin'),
        ('knockout', 'Knockout'),
        ('rapid', 'Rapid'),
        ('blitz', 'Blitz'),
    ]
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('registration_open', 'Registration Open'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    association = models.ForeignKey(Association, on_delete=models.CASCADE, related_name='tournaments')
    name = models.CharField(max_length=200)
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default='swiss')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')
    start_date = models.DateField()
    end_date = models.DateField()
    location = models.CharField(max_length=200)
    num_rounds = models.PositiveIntegerField(default=5)
    max_players = models.PositiveIntegerField(default=32)
    registration_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='USD')
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, related_name='created_tournaments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.name} ({self.association.name})"

    @property
    def player_count(self):
        return self.registrations.filter(status='confirmed').count()

    @property
    def is_full(self):
        return self.player_count >= self.max_players

    @property
    def spots_left(self):
        """Confirmed places still available. Never negative.

        detail.html used to work this out in the template with
            {{ tournament.max_players|add:"-"|add:tournament.player_count }}
        and Django's `add` filter cannot subtract. It tries int(value) +
        int(arg), falls back to concatenation, and returns an empty string
        when both fail, which is exactly what happened: int("-") raises, then
        32 + "-" raises, so the filter returned "", the next add returned ""
        again, and |default: printed a dash.

        "Spots left" therefore showed a dash on every tournament this site has
        ever had, including full ones. Nothing looked broken; it looked like a
        field nobody had filled in.
        """
        return max(self.max_players - self.player_count, 0)


class Section(models.Model):
    """One competition inside a tournament: Open, Ladies, Developmental.

    WHY THIS EXISTS: Percival described a Saturday at the NUST hall as running
    "sections like open, ladies and developmental for kids", and this site had
    no concept of them at all. One tournament meant one pairing pool, so a
    twelve year old in the Developmental section could be paired against the
    strongest adult in the room, and a single standings table mixed three
    competitions that award three separate sets of prizes.

    A SECTION IS A SEPARATE COMPETITION SHARING A VENUE, A DATE AND A ROUND
    SCHEDULE. Players enter exactly one. Pairings never cross sections. Each
    section has its own standings, its own crosstable and its own bye. Rounds
    are shared, because in a hall round three starts at the same time for
    everybody.

    Sections are OPTIONAL. A tournament with none behaves exactly as it always
    did: one pool, one table, section left null everywhere. That matters
    because this shipped onto a live database with real tournaments in it.
    """

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=60)
    # Display order. Open first, then Ladies, then age groups, is conventional,
    # and alphabetical would put Developmental above Open for no reason.
    order = models.PositiveIntegerField(default=0)
    # Optional guidance shown to players entering: "under 14 on 1 January",
    # "rating under 1400". Not enforced; a director decides who belongs.
    eligibility = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['order', 'name']
        unique_together = ['tournament', 'name']

    def __str__(self):
        return f'{self.name} ({self.tournament.name})'

    @property
    def player_count(self):
        return self.registrations.filter(status='confirmed').count()


class TournamentRegistration(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('withdrawn', 'Withdrawn'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='registrations')
    player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='registrations')
    # Null means this tournament does not use sections, which is the case for
    # every tournament that existed before they were added. SET_NULL rather
    # than CASCADE: deleting a section must never delete the entries in it.
    section = models.ForeignKey(
        'Section', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registrations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    registered_at = models.DateTimeField(auto_now_add=True)
    seed_number = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        unique_together = ['tournament', 'player']
        ordering = ['seed_number', 'registered_at']

    def __str__(self):
        return f"{self.player} → {self.tournament.name}"


class Round(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='rounds')
    number = models.PositiveIntegerField()
    is_complete = models.BooleanField(default=False)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ['tournament', 'number']
        ordering = ['number']

    def __str__(self):
        return f"{self.tournament.name} — Round {self.number}"
