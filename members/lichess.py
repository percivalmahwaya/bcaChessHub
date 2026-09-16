"""
Lichess account linking: sign in with Lichess, and read real online ratings.

WHAT THIS IS FOR
================
Almost everybody who plays chess in Bulawayo already plays on Lichess. Asking
them to invent a username and a password for yet another site, when they have
an identity that already carries their rating and their game history, is
friction for no benefit.

So: sign in with Lichess, and pull bullet, blitz and rapid ratings with the
number of games behind each.

TWO THINGS THIS DELIBERATELY DOES NOT DO
========================================
1. IT DOES NOT TOUCH THIS SITE'S OWN ELO. A Lichess blitz rating and a rating
   earned over the board in the NUST hall are different measurements of
   different things, and averaging them would corrupt both. Member.rating
   stays exactly what it was: this association's own ELO, from this
   association's own games. Lichess ratings sit alongside it, labelled.

2. IT DOES NOT KEEP THE ACCESS TOKEN. OAuth is used once, to prove that the
   person signing in really owns that Lichess account. After that the token is
   thrown away, because every rating we display is available from the PUBLIC
   endpoint with no authentication at all. Nothing to store, nothing to leak,
   nothing to expire, and a refresh works forever without asking the player to
   sign in again.

THE PROVISIONAL RATING TRAP
===========================
Lichess returns a rating for every format whether or not you have ever played
it. An account that has never played a rapid game comes back as

    rapid: {rating: 2500, games: 0, prov: true}

2500 is a placeholder, not a rating. Displaying it would tell everyone that a
beginner is rated 2500 rapid, on a page whose entire job is showing real
ratings. `games` and `provisional` are stored for exactly this reason, and
nothing in the interface shows a rating without them.

OAUTH ON LICHESS NEEDS NO REGISTRATION
======================================
Lichess implements OAuth2 with PKCE for public clients. There is no developer
portal, no application to register, and no client secret: the client_id is
just a string identifying us, conventionally a URL. That is why this whole
module needs one setting and no secrets.
"""
import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

API = "https://lichess.org/api"
AUTHORIZE_URL = "https://lichess.org/oauth"
TOKEN_URL = "https://lichess.org/api/token"

# The three the site displays. Lichess exposes more, including ultraBullet and
# several variants, but a club player's identity is bullet, blitz and rapid,
# and a row of eight numbers says less than a row of three.
PERFS = ("bullet", "blitz", "rapid")

TIMEOUT = 15
USER_AGENT = "bcaChessHub (+https://github.com/percivalmahwaya/bcaChessHub)"


class LichessError(Exception):
    """Anything that went wrong talking to Lichess, with a sayable message."""


def _get(url, token=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LichessError("No such Lichess account.") from exc
        if exc.code == 429:
            raise LichessError(
                "Lichess is rate limiting us. Try again in a minute.") from exc
        raise LichessError(f"Lichess returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LichessError(f"Could not reach Lichess: {exc}") from exc


# --------------------------------------------------------------------- OAuth

def client_id():
    """Identifies this site to Lichess. Not a secret, and not registered."""
    return getattr(settings, "LICHESS_CLIENT_ID", "") or "bcachesshub"


def start_authorisation(redirect_uri):
    """Build the URL to send somebody to, plus the PKCE state to remember.

    Returns (url, state, verifier). The caller must keep `state` and
    `verifier` in the session: `state` proves the callback belongs to this
    browser, and `verifier` proves the code being exchanged was requested by
    us. Without PKCE, anybody who intercepts the redirect could swap the code
    for a token.

    No scope is requested. Reading a public profile needs none, and asking for
    permissions we will never use is how a login screen ends up frightening.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(24)

    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}", state, verifier


def exchange_code(code, verifier, redirect_uri):
    """Swap the authorisation code for an access token."""
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id(),
    }).encode("ascii")

    request = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise LichessError(
            f"Lichess refused the sign in (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LichessError(f"Could not reach Lichess: {exc}") from exc

    token = payload.get("access_token")
    if not token:
        raise LichessError("Lichess did not return an access token.")
    return token


def whoami(token):
    """The account that just signed in. The only thing the token is used for."""
    return _get(f"{API}/account", token=token)


# ------------------------------------------------------------------ profiles

def public_profile(username):
    """A player's public profile. No authentication, and none needed.

    This is what every refresh after the first sign in uses, which is why no
    token is ever stored.
    """
    if not username:
        raise LichessError("No Lichess username to look up.")
    return _get(f"{API}/user/{urllib.parse.quote(username)}")


def extract(payload):
    """Flatten a Lichess profile into the fields we store.

    Ratings arrive with the number of games behind them and whether Lichess
    still considers them provisional, and both travel with the rating
    everywhere. A rating without its game count is not a fact, it is a number.
    """
    perfs = payload.get("perfs") or {}
    counts = payload.get("count") or {}

    data = {
        "username": payload.get("username") or "",
        "lichess_id": payload.get("id") or "",
        "title": payload.get("title") or "",
        "total_games": counts.get("all") or 0,
        "wins": counts.get("win") or 0,
        "losses": counts.get("loss") or 0,
        "draws": counts.get("draw") or 0,
    }

    for perf in PERFS:
        entry = perfs.get(perf) or {}
        games = entry.get("games") or 0
        data[f"{perf}_games"] = games
        # A format never played has no rating, whatever Lichess reports.
        # Storing the 2500 placeholder would mean every display has to
        # remember to suppress it, and one day one of them would forget.
        data[f"{perf}_rating"] = entry.get("rating") if games else None
        data[f"{perf}_provisional"] = bool(entry.get("prov"))

    return data
