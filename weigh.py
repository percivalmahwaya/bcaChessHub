"""
Weigh every page, the way a phone on a bundle experiences it.

WHY THIS EXISTS
===============
The design brief for this site is a number: the home page made a phone
download 329 KB, of which 5 KB was content, and took about six seconds on 3G.
Rebuilt on the design system it became 19 KB. That 94% is not a nice side
effect of the redesign, it IS the redesign, and a claim like it is worthless
unless it is measured on demand rather than remembered from the day it was
true.

So this walks the whole site and prints what each page actually costs.

WHAT IT COUNTS
==============
Rendered HTML, plus every stylesheet, script and font the HTML references.
Local files are measured on disk. Remote ones (the Bootstrap CDN) are measured
once and cached, because the visitor pays for those bytes exactly as much as
for ours, and pretending a CDN is free is how a page ends up at 329 KB.

Gzip is counted too, since that is what actually crosses the wire, but the
uncompressed figure is kept because it is what the browser must parse.

    python weigh.py                 # every page
    python weigh.py --save before   # snapshot, to compare against later
    python weigh.py --diff before   # what changed since that snapshot
"""
import gzip
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, str(ROOT))
django.setup()

from django.conf import settings                       # noqa: E402
from django.contrib.auth import get_user_model         # noqa: E402
from django.test import Client                         # noqa: E402

CACHE = ROOT / ".weigh-cache.json"
STATIC_SRC = ROOT / "static"

# Pages worth measuring, as (label, url, needs_login).
# Anonymous pages come first: they are the ones a stranger or a club player
# lands on, and the ones the 329 KB number was about.
PAGES = [
    ("home",              "/",                        False),
    ("security",          "/security/",               False),
    ("rankings",          "/rankings/",               False),
    ("tournaments",       "/tournaments/",            False),
    ("associations",      "/associations/",           False),
    ("login",             "/login/",                  False),
    ("signup",            "/rankings/signup/",        False),
    ("dashboard",         "/dashboard/",              True),
    ("notifications",     "/notifications/",          True),
    ("challenges",        "/matches/challenges/",     True),

    # The pages migrated on 2026-09-17. They were the last five on Bootstrap
    # and, being behind a login, were the ones nobody was measuring, which is
    # part of why three dead-variable bugs sat on them for four days. A page
    # that is never weighed is a page nobody is looking at.
    ("members admin",     "/rankings/manage/",        True),
    ("stats",             "/rankings/admin/stats/",   True),
    ("tournament manage", "/tournaments/1/manage/",   True),
    ("tournament form",   "/tournaments/create/",     True),
    ("game viewer",       "/matches/1/",              False),
    ("print report",      "/tournaments/1/export/print/", True),
]


def remote_size(url):
    """Bytes of a CDN asset, cached so this does not hammer jsdelivr."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if url in cache:
        return cache[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as fh:
            n = len(fh.read())
    except Exception:
        n = 0
    cache[url] = n
    CACHE.write_text(json.dumps(cache, indent=1))
    return n


def local_size(path):
    """Bytes of a file referenced by STATIC_URL, measured on disk."""
    rel = path[len(settings.STATIC_URL):] if path.startswith(settings.STATIC_URL) else path
    for base in (STATIC_SRC, ROOT / "staticfiles"):
        p = base / rel
        if p.exists():
            return p.stat().st_size
    return 0


ASSET = re.compile(
    r'<link[^>]+href="([^"]+\.css[^"]*)"|<script[^>]+src="([^"]+\.js[^"]*)"',
    re.I)


def weigh(client, url):
    resp = client.get(url, follow=True)
    if resp.status_code != 200:
        return None
    html = resp.content
    assets = []
    for m in ASSET.finditer(html.decode("utf-8", "replace")):
        href = m.group(1) or m.group(2)
        if href.startswith("http"):
            assets.append((href, remote_size(href), "cdn"))
        else:
            assets.append((href, local_size(href), "local"))

    total = len(html) + sum(n for _, n, _ in assets)
    return {
        "status": resp.status_code,
        "html": len(html),
        "html_gz": len(gzip.compress(html)),
        "assets": assets,
        "total": total,
        "bootstrap": any("bootstrap" in a.lower() for a, _, _ in assets),
        # A page that redirected is a different page. Recorded so the caller
        # can refuse to report it under the label that was asked for.
        "redirected": resp.redirect_chain[-1][0] if resp.redirect_chain else None,
    }


def main():
    User = get_user_model()

    # TWO clients, not one. A single logged-in client silently measured the
    # wrong pages: /rankings/signup/ 302s to /dashboard/ when you are already
    # authenticated, so "signup" was reported as 10.7 KB, byte for byte
    # identical to the dashboard, because it WAS the dashboard. An anonymous
    # page must be measured anonymously or the number is about another page.
    anon = Client()
    auth = Client()
    user = User.objects.filter(is_superuser=True).first()
    if user:
        auth.force_login(user)

    rows = {}
    print(f"{'PAGE':<16} {'HTML':>8} {'ASSETS':>9} {'TOTAL':>9}  {'GZIP':>7}  FRAMEWORK")
    print("-" * 74)
    for label, url, needs_login in PAGES:
        if needs_login and not user:
            print(f"{label:<16} {'':>8} {'':>9} {'':>9}  {'':>7}  (no superuser, skipped)")
            continue
        r = weigh(auth if needs_login else anon, url)
        if r is None:
            print(f"{label:<16} could not be rendered")
            continue
        if r["redirected"]:
            # Say so rather than quietly reporting whatever it landed on.
            print(f"{label:<16} redirected to {r['redirected']}, not measured")
            continue
        rows[label] = r
        assets = r["total"] - r["html"]
        print(f"{label:<16} {r['html']/1024:7.1f}K {assets/1024:8.1f}K "
              f"{r['total']/1024:8.1f}K  {r['html_gz']/1024:6.1f}K  "
              f"{'Bootstrap' if r['bootstrap'] else 'design system'}")

    if rows:
        heavy = [r for r in rows.values() if r["bootstrap"]]
        print("-" * 74)
        print(f"  {len(rows)} pages, {len(heavy)} still on Bootstrap")
        print(f"  mean total: {sum(r['total'] for r in rows.values())/len(rows)/1024:.1f} KB")

    if "--save" in sys.argv:
        name = sys.argv[sys.argv.index("--save") + 1]
        out = ROOT / f".weigh-{name}.json"
        out.write_text(json.dumps(
            {k: {kk: vv for kk, vv in v.items() if kk != "assets"}
             for k, v in rows.items()}, indent=1))
        print(f"\n  saved to {out.name}")

    if "--diff" in sys.argv:
        name = sys.argv[sys.argv.index("--diff") + 1]
        prev = json.loads((ROOT / f".weigh-{name}.json").read_text())
        print(f"\n  CHANGE SINCE '{name}'")
        print(f"  {'PAGE':<16} {'WAS':>9} {'NOW':>9} {'SAVED':>9}  %")
        print("  " + "-" * 56)
        tw = tn = 0
        for label, r in rows.items():
            if label not in prev:
                continue
            was, now = prev[label]["total"], r["total"]
            tw += was
            tn += now
            pct = (was - now) / was * 100 if was else 0
            print(f"  {label:<16} {was/1024:8.1f}K {now/1024:8.1f}K "
                  f"{(was-now)/1024:8.1f}K  {pct:5.1f}%")
        if tw:
            print("  " + "-" * 56)
            print(f"  {'ALL':<16} {tw/1024:8.1f}K {tn/1024:8.1f}K "
                  f"{(tw-tn)/1024:8.1f}K  {(tw-tn)/tw*100:5.1f}%")


if __name__ == "__main__":
    main()
