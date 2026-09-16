"""
Sign in with Lichess, and keep a linked account's ratings up to date.

THE FLOW
========
    /lichess/start/     build a PKCE challenge, stash it in the session,
                        send the browser to lichess.org
    /lichess/callback/  Lichess sends the browser back with a code, we swap it
                        for a token, ask who it belongs to, throw the token
                        away, and sign the person in
    /lichess/refresh/   re-read the public profile. No token involved.
    /lichess/unlink/    forget the whole thing

WHAT THE CALLBACK HAS TO GET RIGHT
==================================
This is the one place in the application where an outside party hands us
something that decides who somebody is, so each check below exists for a
reason and none of them is decoration:

  * `state` must match the one in the session. Without it, an attacker can
    hand a victim a link that completes an OAuth flow into the ATTACKER's
    Lichess account, silently linking the victim's browser to it.
  * the PKCE `verifier` must be the one we generated. Without it, anybody who
    intercepts the redirect can swap the code for a token themselves.
  * both are removed from the session the moment they are read, so a code can
    never be replayed.
  * a Lichess account already linked to a DIFFERENT login is refused rather
    than moved. Silently reassigning it would let whoever controls that
    Lichess account take over an existing member's profile.
"""
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import lichess
from .models import LichessAccount, Member

SESSION_STATE = 'lichess_oauth_state'
SESSION_VERIFIER = 'lichess_oauth_verifier'
SESSION_NEXT = 'lichess_oauth_next'


def _redirect_uri(request):
    """Must be byte-identical in the authorise and token calls, or Lichess
    rejects the exchange. Built from the request so it is right in
    development and in production without a setting to forget."""
    return request.build_absolute_uri(reverse('lichess_callback'))


def lichess_start(request):
    """Begin the sign in. Also used to LINK an existing account."""
    url, state, verifier = lichess.start_authorisation(_redirect_uri(request))
    request.session[SESSION_STATE] = state
    request.session[SESSION_VERIFIER] = verifier
    request.session[SESSION_NEXT] = request.GET.get('next', '')
    return redirect(url)


def lichess_callback(request):
    """Lichess sends the browser back here."""
    landing = request.session.pop(SESSION_NEXT, '') or reverse('dashboard')

    # Read and immediately discard, so a code cannot be replayed.
    expected_state = request.session.pop(SESSION_STATE, None)
    verifier = request.session.pop(SESSION_VERIFIER, None)

    if request.GET.get('error'):
        messages.info(request, 'Lichess sign in was cancelled.')
        return redirect('login')

    code = request.GET.get('code')
    state = request.GET.get('state')

    if not code or not verifier or not expected_state:
        messages.error(request, 'That Lichess sign in has expired. Try again.')
        return redirect('login')

    if state != expected_state:
        # Not a user error. Somebody handed this browser a prepared link.
        messages.error(
            request,
            'That sign in did not start here, so it was refused. '
            'Open the site and use the Lichess button on the login page.')
        return redirect('login')

    try:
        token = lichess.exchange_code(code, verifier, _redirect_uri(request))
        account = lichess.whoami(token)
    except lichess.LichessError as exc:
        messages.error(request, str(exc))
        return redirect('login')
    finally:
        # Nothing keeps the token beyond this function. Every refresh from
        # here on reads the public profile instead.
        token = None

    data = lichess.extract(account)
    if not data['lichess_id']:
        messages.error(request, 'Lichess did not say who that account belongs to.')
        return redirect('login')

    existing = LichessAccount.objects.filter(
        lichess_id=data['lichess_id']).select_related('user').first()

    # ---- already linked -------------------------------------------------
    if existing:
        if request.user.is_authenticated and existing.user_id != request.user.pk:
            # Refuse rather than move. Reassigning would hand this Lichess
            # account's owner control of somebody else's member profile.
            messages.error(
                request,
                f'The Lichess account {data["username"]} is already linked to '
                'another member here. Unlink it there first.')
            return redirect('edit_profile')

        existing.apply(data)
        existing.save()
        if not request.user.is_authenticated:
            auth_login(request, existing.user,
                       backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Signed in as {data["username"]} on Lichess.')
        return redirect(landing if request.user.is_authenticated else 'dashboard')

    # ---- linking to the account already signed in -----------------------
    if request.user.is_authenticated:
        link = LichessAccount(user=request.user, lichess_id=data['lichess_id'])
        link.apply(data)
        link.save()
        messages.success(
            request, f'Linked to {data["username"]} on Lichess.')
        return redirect('edit_profile')

    # ---- brand new person -----------------------------------------------
    # Make the login now and let them choose a club next. A Member cannot be
    # created here because it needs an association, and guessing one would put
    # somebody in a club they have never been to.
    username = _free_username(data['username'])
    user = User.objects.create_user(username=username)
    user.set_unusable_password()   # there is no password to guess
    user.first_name = data['username']
    user.save()

    link = LichessAccount(user=user, lichess_id=data['lichess_id'])
    link.apply(data)
    link.save()

    auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    messages.success(
        request,
        f'Welcome, {data["username"]}. One more step: choose your club.')
    return redirect('lichess_finish_signup')


def _free_username(preferred):
    """A site username that does not collide.

    Lichess usernames are unique on Lichess, not here, and this site already
    has members who signed up with a password. Suffixing is better than
    failing, and better than silently attaching to the existing account.
    """
    base = preferred or 'player'
    candidate = base
    n = 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f'{base}{n}'
        n += 1
    return candidate


@login_required
def lichess_finish_signup(request):
    """Choose a club, the one thing Lichess cannot tell us."""
    from associations.models import Association

    if hasattr(request.user, 'member'):
        return redirect('dashboard')

    associations = Association.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        association = associations.filter(pk=request.POST.get('association')).first()
        if not association:
            messages.error(request, 'Choose a club to continue.')
        else:
            Member.objects.create(
                user=request.user, association=association, role='player')
            messages.success(request, 'You are in. Welcome.')
            return redirect('dashboard')

    return render(request, 'members/lichess_finish_signup.html', {
        'associations': associations,
        'lichess': getattr(request.user, 'lichess', None),
    })


@login_required
@require_POST
def lichess_refresh(request):
    """Re-read the public profile. No token, no consent screen, no expiry."""
    link = get_object_or_404(LichessAccount, user=request.user)
    try:
        link.apply(lichess.extract(lichess.public_profile(link.username)))
        link.save()
        messages.success(request, 'Lichess ratings updated.')
    except lichess.LichessError as exc:
        messages.error(request, str(exc))
    return redirect('edit_profile')


@login_required
@require_POST
def lichess_unlink(request):
    link = get_object_or_404(LichessAccount, user=request.user)
    if not request.user.has_usable_password():
        # Their Lichess account is the only way in. Unlinking would lock them
        # out of an account they cannot reset a password for.
        messages.error(
            request,
            'Lichess is currently your only way to sign in, so it cannot be '
            'unlinked. Set a password first.')
        return redirect('edit_profile')
    name = link.username
    link.delete()
    messages.success(request, f'Unlinked from {name} on Lichess.')
    return redirect('edit_profile')
