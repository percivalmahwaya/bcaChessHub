from django.db import models
from members.models import Member
from tournaments.models import Tournament, Round


class Match(models.Model):
    RESULT_CHOICES = [
        ('white_win', 'White Wins (1-0)'),
        ('black_win', 'Black Wins (0-1)'),
        ('draw', 'Draw (½-½)'),
        ('white_forfeit', 'White Forfeits'),
        ('black_forfeit', 'Black Forfeits'),
        ('pending', 'Not Yet Played'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='matches')
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='matches')
    # Which section this game belongs to, denormalised from the pairing.
    #
    # It could be looked up through the white player's registration, but a
    # game is a fact about the day it was played: if a director later moves a
    # player between sections, every game they have already played must stay
    # in the section it was played in, or the standings and the crosstable
    # both rewrite history. Null on every tournament that uses no sections.
    section = models.ForeignKey(
        'tournaments.Section', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches')
    white_player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='matches_as_white')
    black_player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='matches_as_black', null=True, blank=True)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default='pending')
    scheduled_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    board_number = models.PositiveIntegerField(blank=True, null=True)
    pgn = models.TextField(blank=True, help_text='Game in PGN notation for analysis')
    lichess_game_id = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['round__number', 'board_number']

    def __str__(self):
        return f"{self.white_player} vs {self.black_player} (Round {self.round.number})"

    def record_result(self, result):
        self.result = result
        from django.utils import timezone
        self.completed_at = timezone.now()
        self.save(update_fields=['result', 'completed_at'])
        self._update_elo_ratings()

    def _update_elo_ratings(self, K=32):
        K = 32
        white = self.white_player
        black = self.black_player
        expected_white = 1 / (1 + 10 ** ((black.rating - white.rating) / 400))
        expected_black = 1 - expected_white

        if self.result == 'white_win':
            score_white, score_black = 1, 0
        elif self.result == 'black_win':
            score_white, score_black = 0, 1
        else:
            score_white, score_black = 0.5, 0.5

        white.update_rating(round(white.rating + K * (score_white - expected_white)), match=self)
        black.update_rating(round(black.rating + K * (score_black - expected_black)), match=self)


class Challenge(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('accepted',  'Accepted'),
        ('declined',  'Declined'),
        ('completed', 'Completed'),
        ('expired',   'Expired'),
    ]
    RESULT_CHOICES = [
        ('challenger_win', 'Challenger Wins'),
        ('opponent_win',   'Opponent Wins'),
        ('draw',           'Draw'),
    ]

    challenger = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='challenges_sent')
    opponent   = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='challenges_received')
    message    = models.TextField(blank=True)
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    result     = models.CharField(max_length=20, choices=RESULT_CHOICES, blank=True)
    lichess_game_id = models.CharField(max_length=100, blank=True)
    elo_updated     = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.challenger} → {self.opponent} [{self.status}]"

    def record_result(self, result):
        from django.utils import timezone
        self.result      = result
        self.status      = 'completed'
        self.completed_at = timezone.now()
        self.save(update_fields=['result', 'status', 'completed_at'])
        if not self.elo_updated:
            self._update_elo()
            self.elo_updated = True
            self.save(update_fields=['elo_updated'])

    def _update_elo(self):
        K = 32
        c, o = self.challenger, self.opponent
        expected_c = 1 / (1 + 10 ** ((o.rating - c.rating) / 400))
        if self.result == 'challenger_win':
            score_c, score_o = 1, 0
        elif self.result == 'opponent_win':
            score_c, score_o = 0, 1
        else:
            score_c, score_o = 0.5, 0.5
        c.update_rating(round(c.rating + K * (score_c - expected_c)))
        o.update_rating(round(o.rating + K * (1 - score_c - (1 - expected_c))))

    @property
    def challenger_elo_delta(self):
        """Rough ELO delta for display — only valid after completion."""
        if not self.elo_updated:
            return None
        K = 32
        return round(K * (1 if self.result == 'challenger_win' else (0.5 if self.result == 'draw' else 0)
                          - 1 / (1 + 10 ** ((self.opponent.rating - self.challenger.rating) / 400))))
