from django.db import models

from . import rating
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
    # Every Lichess export already carries these and nothing read them, so
    # they were arriving free and being thrown away on every linked game.
    # Extracted on import now, which makes an opening report for the
    # association possible without touching a single game again.
    # Blank on games linked before 2026-09-17 and on games still in progress,
    # where Lichess has not settled on an opening yet.
    eco = models.CharField(max_length=8, blank=True,
                           help_text='ECO code from the PGN, e.g. B02')
    opening = models.CharField(max_length=120, blank=True,
                               help_text='Opening name from the PGN')

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

    def _update_elo_ratings(self):
        """Apply the result to both players' ratings.

        The K parameter this used to take was dead: the body reassigned
        `K = 32` on its very first line, so passing anything had no effect.
        The formula lives in matches/rating.py now, in one copy.
        """
        white, black = self.white_player, self.black_player

        # Both players are rated against the ratings held BEFORE this game, so
        # whoever is updated first cannot change the other's expected score.
        white_before, black_before = white.rating, black.rating

        white.update_rating(
            rating.new_rating(white_before, black_before,
                              rating.score_for(self.result, is_white=True)),
            match=self)
        black.update_rating(
            rating.new_rating(black_before, white_before,
                              rating.score_for(self.result, is_white=False)),
            match=self)


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
    # The rating change actually applied to the challenger, stored at the
    # moment it is applied. It used to be recomputed on every page view from
    # the players' CURRENT ratings, which is not the same number: by then both
    # ratings have already moved, and any later game moves them again. Storing
    # it is the only way the figure shown can be the figure applied.
    # Null on rows created before 2026-09-17, which fall back to recomputing.
    challenger_delta = models.IntegerField(null=True, blank=True)
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
            self.save(update_fields=['elo_updated', 'challenger_delta'])

    def _score_for_challenger(self):
        if self.result == 'challenger_win':
            return rating.WIN
        if self.result == 'opponent_win':
            return rating.LOSS
        return rating.DRAW

    def _update_elo(self):
        c, o = self.challenger, self.opponent
        c_before, o_before = c.rating, o.rating
        score_c = self._score_for_challenger()

        c.update_rating(rating.new_rating(c_before, o_before, score_c))
        o.update_rating(rating.new_rating(o_before, c_before, 1 - score_c))

        # Recorded from the ratings held BEFORE the game, which is the only
        # moment this number is knowable.
        self.challenger_delta = rating.delta(c_before, o_before, score_c)

    @property
    def challenger_elo_delta(self):
        """Rough ELO delta for display — only valid after completion."""
        if not self.elo_updated:
            return None
        # The stored figure IS the applied figure. It used to be recomputed
        # here, and operator precedence put the expected score inside the
        # `else` branch, leaving the win case a bare 1, so EVERY challenge win
        # displayed +32 whatever the opponent's strength: a 1200 beating a
        # 1000 was shown +32 and actually given +8.
        if self.challenger_delta is not None:
            return self.challenger_delta
        # Rows completed before the field existed. Recomputed from current
        # ratings, so approximate, but with the correct formula.
        return rating.delta(self.challenger.rating, self.opponent.rating,
                            self._score_for_challenger())
