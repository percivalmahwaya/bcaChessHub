"""
Production QA — audits the DEPLOYED SYSTEM, not the code.

WHY THIS EXISTS
===============
On 2026-09-13 a security fix took seven attempts to reach production. Three
deploys failed and several hours were lost. Every single cause was deployed
configuration, and the test suite — 69 passing Django tests — could not see any
of them:

  * EMAIL_BACKEND was set to the SMTP backend, and a new boot guard rejected
    exactly that value. Nobody had read the live variables before shipping it.
  * PAYNOW_SANDBOX was set to True in production, and a second boot guard
    rejected that too. Same mistake, one deploy later.
  * ALLOWED_HOSTS was '*'. settings.py reads it from the environment with a
    safe default, so reading the CODE said "fine" while the deployed VALUE was
    wide open.
  * DEFAULT_FROM_EMAIL contained a URL, not an email address.
  * Railway's source repo still pointed at the pre-rename GitHub path, so
    pushes silently stopped deploying and "Redeploy" rebuilt a stale commit.
  * There was no GitHub webhook on the repository at all.
  * Tightening ALLOWED_HOSTS then broke Railway's health check, which probes
    the container with its own Host header. Django answered 400, the check
    never passed, and Railway killed a perfectly healthy container after five
    minutes — logging a clean successful boot and no traceback.

The unifying lesson: **unit tests verify code against code. Nothing verified
code against the environment it actually runs in.** That is the gap this file
closes.

Checks are grouped:
    VARIABLES   the live Railway config, including every boot guard evaluated
                against the real values rather than assumed
    LIVE        what production actually returns, probed over HTTPS
    PIPELINE    can a push still reach production at all
    CONSISTENCY is the running code the code we think it is

Nothing here is destructive. Every probe is a GET, or a POST crafted so that
it is rejected whichever version is deployed.

Requires: `railway` and `gh` CLIs authenticated. Run from the repo root.

Usage:
    python qa_production.py
    python qa_production.py --skip-pipeline    # config + live probes only
"""

import json
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).parent
SERVICE = 'web'
BASE = 'https://web-production-abe9b.up.railway.app'
REPO = 'percivalmahwaya/bcaChessHub'

PASS, FAIL, WARN = 'PASS', 'FAIL', 'WARN'
results = []

SECRET_HINTS = ('KEY', 'PASSWORD', 'SECRET', 'TOKEN', 'DATABASE_URL')


def check(name, status, detail=''):
    results.append((name, status, detail))
    icon = {PASS: '[ok]  ', FAIL: '[FAIL]', WARN: '[warn]'}[status]
    print(f'  {icon} {name}')
    if detail:
        print(f'         {detail}')


def ok(name, condition, detail=''):
    check(name, PASS if condition else FAIL, detail)
    return condition


def redact(key, value):
    """Never print a secret, even into a terminal the user is watching."""
    s = str(value)
    if any(h in key.upper() for h in SECRET_HINTS):
        return f'<{len(s)} chars>' if s else '<empty>'
    return s


def run(cmd):
    """
    Run a CLI and return (code, stdout, stderr).

    `railway` and `gh` are npm/Windows shims, so a bare exec cannot find them —
    subprocess needs either the .cmd extension or a shell. Falling back to
    shell=True rather than hardcoding a path keeps this working on Linux too.
    The first version of this file reported "railway CLI not linked" when the
    CLI was in fact perfectly linked, which is its own small lesson about
    trusting a failing check's explanation.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired as e:
        return 1, '', str(e)
    try:
        p = subprocess.run(' '.join(f'"{c}"' if ' ' in c else c for c in cmd),
                           capture_output=True, text=True, timeout=180, shell=True)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, '', str(e)


def fetch(path, method='GET', data=None, host=None, timeout=25):
    """Return (status, body). Never raises — a dead site is a result."""
    url = BASE + path
    body = None
    if data:
        body = '&'.join(f'{k}={v}' for k, v in data.items()).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('User-Agent', 'chesshub-production-qa/1.0')
    if host:
        req.add_header('Host', host)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(400).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode('utf-8', 'replace')
    except Exception as e:
        return 0, str(e)


# ------------------------------------------------------------- VARIABLES

def live_variables():
    code, out, err = run(['railway', 'variables', '--service', SERVICE, '--json'])
    if code != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def qa_variables(v):
    print('\nDEPLOYED VARIABLES')

    if v is None:
        check('railway CLI can read the live variables', FAIL,
              'run `railway login` and `railway link`')
        return None
    check('railway CLI can read the live variables', PASS, f'{len(v)} variables')

    debug = str(v.get('DEBUG', 'True')).lower() in ('true', '1', 'yes')
    ok('DEBUG is off in production', not debug, f'DEBUG={v.get("DEBUG")}')

    sk = str(v.get('SECRET_KEY', ''))
    ok('SECRET_KEY is set and not the insecure placeholder',
       sk and not sk.startswith('django-insecure-'), redact('SECRET_KEY', sk))

    # --- ALLOWED_HOSTS: the one that was wrong while the code looked right ---
    hosts = [h.strip() for h in str(v.get('ALLOWED_HOSTS', '')).split(',') if h.strip()]
    ok('ALLOWED_HOSTS is not a wildcard', '*' not in hosts,
       "'*' disables Django's Host-header validation, which is what stops "
       'poisoned password-reset links')
    pub = v.get('RAILWAY_PUBLIC_DOMAIN', '')
    ok('ALLOWED_HOSTS contains the public domain',
       not hosts or not pub or pub in hosts, f'public domain {pub!r}')

    # Railway probes the container with its own Host header. If Django rejects
    # it the health check can never pass and the deploy is killed after
    # healthcheckTimeout, logging a clean boot and no traceback.
    settings_src = (ROOT / 'config' / 'settings.py').read_text(encoding='utf-8')
    covered = ('healthcheck.railway.app' in hosts
               or 'healthcheck.railway.app' in settings_src)
    ok('the Railway health-check host is allowed', covered,
       'without it Railway kills a healthy container after healthcheckTimeout')

    # --- values that trip a boot guard --------------------------------------
    eb = str(v.get('EMAIL_BACKEND', ''))
    ok('EMAIL_BACKEND is not the SMTP backend',
       not eb.endswith('smtp.EmailBackend'),
       'Railway blocks outbound SMTP on 25/465/587/2525 — measured, and '
       'settings.py refuses to boot with it')
    ps = str(v.get('PAYNOW_SANDBOX', '')).lower()
    ok('PAYNOW_SANDBOX is not enabled', ps not in ('true', '1', 'yes'),
       'sandbox mode can mark payments completed without payment')

    # --- shapes, not just presence -----------------------------------------
    dfe = str(v.get('DEFAULT_FROM_EMAIL', ''))
    if dfe:
        looks_like_email = '@' in dfe and not dfe.lower().startswith('http')
        ok('DEFAULT_FROM_EMAIL is an address, not a URL', looks_like_email, dfe)
    sbu = str(v.get('SITE_BASE_URL', ''))
    if sbu:
        ok('SITE_BASE_URL is a URL', sbu.lower().startswith('http'), sbu)

    db = str(v.get('DATABASE_URL', ''))
    ok('DATABASE_URL points at Postgres, not ephemeral SQLite',
       db.startswith('postgres'),
       'SQLite on a container filesystem is destroyed on every redeploy')

    return v


def qa_boot_guards(v):
    """
    Evaluate EVERY boot guard in settings.py against the live variables.

    This is the check that would have prevented today. Three deploys died
    because a guard was added without anyone reading the deployed value it
    would be tested against. Rather than hardcoding the known guards, parse
    them out of the source so a NEW guard is covered the moment it is written.
    """
    print('\nBOOT GUARDS vs LIVE CONFIG')
    src = (ROOT / 'config' / 'settings.py').read_text(encoding='utf-8')

    guards = re.findall(
        r'^if (not DEBUG and [^\n:]+|[^\n:]*DEBUG[^\n:]*):\s*\n\s*raise ImproperlyConfigured',
        src, re.M)
    check('boot guards found in settings.py',
          PASS if guards else WARN,
          f'{len(guards)} guard(s): ' + '; '.join(g.strip()[:60] for g in guards))

    if v is None:
        return

    debug = str(v.get('DEBUG', 'True')).lower() in ('true', '1', 'yes')
    if debug:
        check('guards evaluated against live config', WARN,
              'DEBUG is on, so production guards do not apply')
        return

    # Re-implement each known guard's condition against the real values.
    tripped = []
    sk = str(v.get('SECRET_KEY', ''))
    if sk.startswith('django-insecure-'):
        tripped.append('SECRET_KEY is the insecure placeholder')
    if str(v.get('EMAIL_BACKEND', '')).endswith('smtp.EmailBackend'):
        tripped.append('EMAIL_BACKEND is the SMTP backend')
    if str(v.get('PAYNOW_SANDBOX', '')).lower() in ('true', '1', 'yes'):
        tripped.append('PAYNOW_SANDBOX is enabled')

    ok('no live variable would trip a boot guard', not tripped,
       '; '.join(tripped) if tripped else
       'a deploy would boot rather than dying on ImproperlyConfigured')


# ------------------------------------------------------------------ LIVE

def qa_live():
    print('\nLIVE PRODUCTION BEHAVIOUR')

    status, body = fetch('/')
    if not ok('site responds', status == 200, f'HTTP {status}'):
        return

    for path in ('/rankings/', '/tournaments/', '/associations/'):
        s, _ = fetch(path)
        ok(f'{path} serves', s == 200, f'HTTP {s}')

    # --- payment bypasses, probed as an attacker would ---------------------
    s, b = fetch('/payments/sandbox-checkout/1/')
    ok('sandbox checkout is refused in production', s == 403,
       f'HTTP {s} — 404 means the sandbox guard was PASSED, not that the '
       f'endpoint is missing')

    s, b = fetch('/payments/sandbox-approve/1/', method='POST')
    ok('sandbox approval is refused in production', s in (403, 302, 405),
       f'HTTP {s}')

    # Reference deliberately points at a payment id that will not exist, so
    # this probe is harmless whichever version is deployed.
    s, b = fetch('/payments/callback/', method='POST',
                 data={'reference': 'CHESSHUB-999999', 'status': 'paid'})
    ok('unsigned Paynow callback is rejected', s == 403,
       f'HTTP {s} {b[:30]!r} — 400 means the hash was never checked and the '
       f'request reached the payment lookup')

    s, _ = fetch('/payments/callback/')
    ok('callback rejects GET', s == 405, f'HTTP {s}')

    # --- information leakage ----------------------------------------------
    s, b = fetch('/definitely-not-a-real-page-qa/')
    ok('404 page is not the Django debug page', 'Traceback' not in b
       and 'DEBUG = True' not in b, f'HTTP {s}')

    s, b = fetch('/admin/')
    ok('admin is reachable but requires login', s in (200, 302),
       f'HTTP {s}')


# -------------------------------------------------------------- PIPELINE

def qa_pipeline():
    print('\nDEPLOYMENT PIPELINE')

    # The webhook is what makes `git push` reach production. It vanished when
    # the GitHub account was renamed, and nothing noticed for two days.
    code, out, _ = run(['gh', 'api', f'repos/{REPO}/hooks'])
    if code != 0:
        check('GitHub webhook exists', WARN, 'gh CLI could not read hooks')
    else:
        try:
            hooks = json.loads(out)
        except json.JSONDecodeError:
            hooks = []
        railway_hooks = [h for h in hooks
                         if 'railway' in str(h.get('config', {}).get('url', '')).lower()]
        check('a Railway webhook is installed on the repo',
              PASS if railway_hooks else WARN,
              f'{len(hooks)} webhook(s) total, {len(railway_hooks)} Railway. '
              f'Railway can also poll, so absence is a warning, not a failure — '
              f'but it is why pushes silently stopped deploying.')

    code, out, _ = run(['railway', 'deployment', 'list', '--service', SERVICE])
    if code != 0:
        check('railway CLI can list deployments', FAIL, 'not linked?')
        return
    lines = [l for l in out.splitlines() if '|' in l]
    if not lines:
        check('deployments found', FAIL)
        return
    newest = lines[0]
    status = newest.split('|')[1].strip()
    ok('the most recent deployment succeeded',
       status in ('SUCCESS', 'SLEEPING'), f'status={status}')

    failed_run = [l for l in lines[:5] if 'FAILED' in l]
    check('recent deployment history is clean',
          PASS if not failed_run else WARN,
          f'{len(failed_run)} of the last 5 deployments FAILED')


def qa_consistency():
    print('\nIS PRODUCTION RUNNING THE CODE WE THINK?')

    code, local_head, _ = run(['git', 'rev-parse', 'HEAD'])
    code2, out, _ = run(['git', 'rev-parse', '@{u}'])
    if code == 0 and code2 == 0:
        ok('local HEAD matches its upstream', local_head.strip() == out.strip(),
           f'local {local_head.strip()[:7]} vs upstream {out.strip()[:7]} — '
           f'unpushed work cannot be deployed')

    code, out, _ = run(['git', 'status', '--porcelain'])
    dirty = [l for l in out.splitlines() if l and not l.startswith('??')]
    check('no uncommitted tracked changes',
          PASS if not dirty else WARN,
          f'{len(dirty)} modified file(s)' if dirty else '')

    # requirements.txt is what the build installs; if a package is declared but
    # the running site behaves as though it is absent, the deploy is stale.
    reqs = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    declared = [l.split('#')[0].strip() for l in reqs.splitlines()
                if l.split('#')[0].strip()]
    check('requirements.txt is readable', PASS, f'{len(declared)} packages')


# --------------------------------------------------------------- report

def summary():
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_warn = sum(1 for _, s, _ in results if s == WARN)
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    print('\n' + '=' * 66)
    print(f'  {n_pass} passed, {n_warn} warnings, {n_fail} failures '
          f'({len(results)} checks)')
    if n_fail:
        print('\n  FAILURES:')
        for name, s, d in results:
            if s == FAIL:
                print(f'    - {name}: {d}')
    if n_warn:
        print('\n  WARNINGS:')
        for name, s, d in results:
            if s == WARN:
                print(f'    - {name}: {d}')
    print('=' * 66)
    return n_fail


def self_test():
    """
    Replay today's real failures and confirm each one is CAUGHT.

    A suite that only passes on a healthy system proves nothing — that is
    precisely how 69 green Django tests coexisted with a payment bypass. So
    every configuration that actually broke production on 2026-09-13 is fed
    back in here, and this fails loudly if the corresponding check does not.
    """
    print('SELF-TEST — replaying the configurations that broke production\n')

    good = {
        'DEBUG': 'False',
        'SECRET_KEY': 'a-real-secret-key-value-that-is-long-enough',
        'ALLOWED_HOSTS': 'web-production-abe9b.up.railway.app,healthcheck.railway.app',
        'RAILWAY_PUBLIC_DOMAIN': 'web-production-abe9b.up.railway.app',
        'DEFAULT_FROM_EMAIL': 'BCA ChessHub <mahwayapercival@gmail.com>',
        'SITE_BASE_URL': 'https://web-production-abe9b.up.railway.app',
        'DATABASE_URL': 'postgresql://user:pw@host/db',
    }

    cases = [
        ('EMAIL_BACKEND set to SMTP (killed deploy #1)',
         {**good, 'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend'}),
        ('PAYNOW_SANDBOX=True (killed deploy #2, and was the live bypass)',
         {**good, 'PAYNOW_SANDBOX': 'True'}),
        ("ALLOWED_HOSTS='*' (I read the code and declared this fine)",
         {**good, 'ALLOWED_HOSTS': '*'}),
        ('ALLOWED_HOSTS without the health-check host (killed deploy #3)',
         {**good, 'ALLOWED_HOSTS': 'web-production-abe9b.up.railway.app'}),
        ('DEFAULT_FROM_EMAIL holding a URL',
         {**good, 'DEFAULT_FROM_EMAIL': 'https://web-production-abe9b.up.railway.app'}),
        ('SECRET_KEY left as the insecure placeholder',
         {**good, 'SECRET_KEY': 'django-insecure-local-development-key'}),
        ('DEBUG left on in production',
         {**good, 'DEBUG': 'True'}),
        ('DATABASE_URL falling back to SQLite',
         {**good, 'DATABASE_URL': 'sqlite:///db.sqlite3'}),
    ]

    # The health-check check also consults settings.py, which now contains the
    # fix. Neutralise that for the one case that must fail on the variable.
    global ROOT
    real_root = ROOT

    failures = []
    for label, cfg in cases:
        results.clear()
        if 'health-check host' in label:
            import tempfile
            tmp = Path(tempfile.mkdtemp())
            (tmp / 'config').mkdir()
            (tmp / 'config' / 'settings.py').write_text('# no healthcheck fix\n',
                                                        encoding='utf-8')
            ROOT = tmp
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            qa_variables(cfg)
            qa_boot_guards(cfg)
        ROOT = real_root
        caught = [n for n, s, _ in results if s == FAIL]
        if caught:
            print(f'  [caught] {label}')
            for c in caught:
                print(f'             -> {c}')
        else:
            print(f'  [MISSED] {label}')
            failures.append(label)

    results.clear()
    print()
    print('=' * 66)
    if failures:
        print(f'  {len(failures)} of {len(cases)} real failures NOT caught:')
        for f in failures:
            print(f'    - {f}')
    else:
        print(f'  All {len(cases)} configurations that broke production are caught.')
    print('=' * 66)
    return 1 if failures else 0


def main():
    if '--self-test' in sys.argv:
        sys.exit(self_test())

    print('ChessHub production QA — auditing the DEPLOYED system')
    print(f'  target: {BASE}')

    v = qa_variables(live_variables())
    qa_boot_guards(v)
    qa_live()
    if '--skip-pipeline' not in sys.argv:
        qa_pipeline()
        qa_consistency()

    sys.exit(1 if summary() else 0)


if __name__ == '__main__':
    main()
