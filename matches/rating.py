"""
The rating formula. One copy of it.

WHY THIS FILE EXISTS
====================
K = 32 was written out in FOUR separate places in matches/models.py, and the
Elo arithmetic in three. The evaluation of niklasf's libraries flagged the
duplication on 2026-09-13 as something to fix "regardless of which rating
system wins". Following it up on 2026-09-17 found that the copies had already
drifted, in two ways:

  1. `def _update_elo_ratings(self, K=32)` was followed immediately by
     `K = 32`, so the parameter was dead. Passing a different K did nothing.

  2. `Challenge.challenger_elo_delta` computed a DIFFERENT NUMBER from the one
     actually applied to the player's rating, because of operator precedence:

         K * (1 if win else (0.5 if draw else 0) - expected)

     The `- expected` binds inside the `else` branch, so the win case is a
     bare 1. Every challenge win was displayed as +32 whatever the opponent's
     strength. A 1200 beating a 1000 was shown +32 and actually given +8.
     Draws and losses were right, which is why it went unnoticed.

THE SHAPE OF THE FIX
====================
`delta()` is defined as `new_rating() - own`, not as its own formula. That is
deliberate: the number shown to a player and the number applied to their
rating are now the same computation, so they cannot drift apart again. Making
them agree is not enough; they have to be incapable of disagreeing.

Elo, not Glicko-2, for now. liglicko2 models rating uncertainty, which matters
a lot for a club where most members have played a handful of games and a 1200
start is mostly noise, but it is Rust and its own README calls it a proof of
concept. Revisit with this module as the single seam to change.
"""

# The only copy. Standard Elo development coefficient; FIDE uses 40/20/10 by
# strength and experience, and a club running its first rated events is better
# served by one value that moves ratings quickly toward the truth.
K_FACTOR = 32

# Where every unrated player starts. Also in members.Member.rating as the
# field default, which is the authority for storage; this is here so the
# rating maths never has to import the model.
STARTING_RATING = 1200

WIN, DRAW, LOSS = 1.0, 0.5, 0.0


def expected_score(own, opponent):
    """The share of a point a player of `own` rating is expected to take.

    0.5 between equals, rising toward 1 as the gap widens.
    """
    return 1 / (1 + 10 ** ((opponent - own) / 400))


def new_rating(own, opponent, score):
    """What `own` becomes after scoring `score` against `opponent`."""
    return round(own + K_FACTOR * (score - expected_score(own, opponent)))


def delta(own, opponent, score):
    """The change that new_rating() will apply.

    Derived from new_rating rather than recomputed, so the figure shown to a
    player is the figure applied to them BY CONSTRUCTION. Recomputing it is
    exactly how the two came to disagree in the first place.
    """
    return new_rating(own, opponent, score) - own


def score_for(result, is_white):
    """Turn a Match result into the score White or Black actually took.

    Forfeits count as a played game here, the same as they did before this
    module existed. python-chess can distinguish a normal finish from a
    timeout or an abandonment via the PGN Termination header, which is worth
    revisiting if forfeits should stop being rated.
    """
    if result == 'white_win':
        return WIN if is_white else LOSS
    if result == 'black_win':
        return LOSS if is_white else WIN
    return DRAW
