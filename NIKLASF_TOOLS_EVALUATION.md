# Evaluating niklasf's chess libraries for bcaChessHub

_Overnight analysis, 2026-09-13. Every capability claim below was executed, not
read from a README — the distinction matters after a week in which reading
source instead of checking reality cost seven failed deploys._

---

## Who this is

**Niklas Fiekas** — a core Lichess developer. **254 public repos**, of which
**84 are chess-related** and **61 are original and unarchived**; the other 174
are forks (Stockfish, fishtest, various). So the surface is large but the
usable surface is small.

| repo | stars | language | last push | licence |
|---|---:|---|---|---|
| **python-chess** | 2,877 | Python | 2026-08-22 | **GPL-3.0** |
| shakmaty | 306 | Rust | 2026-09-08 | GPL-3.0 |
| **chessops** | 171 | TypeScript | 2026-09-10 | **GPL-3.0** |
| **web-boardimage** | 81 | Python | 2026-08-05 | **AGPL-3.0** |
| syzygy-tables.info | 74 | Python | 2026-08-16 | AGPL-3.0 |
| liglicko2 | 5 | Rust | 2024-11-09 | Apache-2.0 |
| python-asyncdgt | 15 | Python | 2022-12-26 | GPL-3.0 |

---

## What bcaChessHub actually does with chess data today

Grounded in the code, not assumed:

| where | what it does |
|---|---|
| [matches/views.py:86](matches/views.py#L86) | fetches `lichess.org/game/export/{id}` |
| [matches/views.py:112](matches/views.py#L112) | stores the PGN text **verbatim, unparsed** |
| [matches/models.py:24](matches/models.py#L24) | `pgn = TextField()` — a blob, nothing reads it |
| [matches/views.py:22](matches/views.py#L22) | ships the blob to the browser as JSON |
| templates | `chess.js 0.10.3` from cdnjs does all the work |

**There is no server-side chess logic anywhere in this project.** Every move,
result and position is either trusted from Lichess or computed in the visitor's
browser. That is the gap.

---

## Tier 1 — worth adopting: `python-chess`

**Verified working against the exact URL `matches/views.py` already calls.**

Live Lichess game, parsed server-side:

```
White / Black   : respects_55 vs DrNykterstein
Result          : 0-1
ECO / Opening   : B02 / Alekhine Defense: Sämisch Attack
Termination     : Normal
Elo             : 2644 / 3145
moves           : 136
```

And on a second (in-progress) game, all **47 moves validated as legal**, plus
`is_checkmate()` / `is_stalemate()` detection and a **28 KB SVG board rendered
in-process** — no external service, no CDN.

### What that unlocks, against real gaps

1. **Trust-but-verify on imported games.** Right now a PGN is stored without
   anyone checking it parses, let alone that the moves are legal or that the
   result header matches what gets recorded against a player's rating. One
   `chess.pgn.read_game()` closes that.

2. **ECO codes and opening names for free.** Every imported game already
   carries them; nothing currently reads them. That is an opening-repertoire
   report for the association at roughly zero cost.

3. **Board diagrams in tournament reports and emails.**
   `templates/tournaments/print.html` produces a printable report with no
   positions in it, and `notifications/email.py` sends text-only mail.
   `chess.svg.board()` renders any position server-side.

4. **A real result, not a claimed one.** `Termination` distinguishes a normal
   finish from a timeout or abandonment — which should arguably affect whether
   a game is rated.

**Install:** `pip install chess` — pure Python, no system dependencies.
Verified version **1.11.2**, Python 3.8+.

---

## Tier 2 — worth considering

### `chessops` — replacing a six-year-old frontend dependency

bcaChessHub loads **`chess.js 0.10.3` from cdnjs**. Checked against the npm
registry:

| | version | released |
|---|---|---|
| in use here | 0.10.3 | **2020-02-22** |
| current | 1.4.0 | 2025-06-14 |

**25 releases behind, six years old**, and loaded from a third-party CDN — so
the game viewer breaks if cdnjs is blocked or unreachable, which is not a
theoretical concern for users in Zimbabwe on constrained connections.

`chessops` is the same author's actively-maintained TypeScript library
(last push 2026-09-10) and is what Lichess itself uses. **But** it is a
bundler-oriented ES module, not a drop-in `<script>` tag, so adopting it means
introducing a frontend build step this project currently does not have.

**Honest recommendation:** the cheaper fix is to **vendor a current chess.js
locally** rather than switch libraries — it removes the CDN dependency and the
six-year gap in one change, without adding a build pipeline. Revisit chessops
only if a build step arrives for other reasons.

### `web-boardimage` — only if email diagrams matter

An HTTP service rendering board images. python-chess already renders SVG
in-process, so this is redundant **except for one real case: email clients
largely do not render SVG.** If board diagrams in notification emails are
wanted, something must produce PNG.

Note it is **AGPL-3.0**, which is network copyleft — but it is *designed* as a
standalone HTTP service, so running it in its own process behind an API call
keeps a clean boundary rather than mixing licences inside the Django app.

---

## Tier 3 — relevant, but not now

**`liglicko2`** — Lichess-flavoured Glicko-2. Genuinely better than plain Elo
because it models rating *uncertainty*, which matters enormously for a club
where most members have played a handful of rated games and a 1200 start is
mostly noise.

Two problems: it is **Rust**, and its own README says *"This does not (yet)
exactly match the Lichess implementation... a proof of concept."*

It does, however, point at a real defect here. **`K = 32` is hardcoded in three
separate places** — [matches/models.py:41](matches/models.py#L41),
[:101](matches/models.py#L101), [:118](matches/models.py#L118) — so the rating
formula exists in triplicate and can drift out of agreement with itself. That
is worth fixing regardless of which rating system wins.

**`python-asyncdgt`** — DGT electronic board communication. Only relevant if
BCA ever buys DGT boards for live tournament broadcast. Last push 2022.

**Not relevant:** syzygy tablebases, Stockfish forks, `magics`, the Rust
libraries, the Drupal modules.

---

## The licence problem — the finding I did not expect

```
bcaChessHub license: NONE — all rights reserved by default
visibility:          public
```

**There is no `LICENSE` file in this repo.** A public repo with no licence is
"all rights reserved" by default: nobody — including collaborators or a future
employer reviewing it — has permission to use, copy or modify it.

That matters here specifically, because **python-chess is GPL-3.0**, a strong
copyleft licence. The general position is that GPL-3.0 obligations are
triggered by *distributing* the software, and merely running it as a hosted web
service is not distribution (that is what AGPL adds). But this repo *is*
publicly distributed source, which makes the combined-work question real rather
than academic.

**I am not a lawyer and this is not legal advice.** The practical point:

- Adding an explicit `LICENSE` is worth doing anyway — an unlicensed public
  repo is a weak portfolio artefact, because a reviewer legally cannot reuse it
- If python-chess goes in, **GPL-3.0 for bcaChessHub is the clean, consistent
  choice** and costs nothing given the repo is already public
- Keep `web-boardimage` (AGPL-3.0) in its own process if used at all

---

## Recommendation, in order

| # | change | effort | why |
|---|---|---|---|
| 1 | Add a `LICENSE` file | 5 min | unlicensed public repo blocks reuse and complicates everything below |
| 2 | `pip install chess`; parse and validate imported PGNs | ~1 h | stops trusting unverified input on rated games |
| 3 | De-duplicate `K = 32` into one place | ~30 min | the rating formula currently exists three times |
| 4 | Extract ECO/opening into the `Match` model | ~1 h | data already arrives free; enables opening reports |
| 5 | Board diagrams in tournament print reports | ~2 h | `chess.svg.board()`, no new service |
| 6 | Vendor a current chess.js locally | ~1 h | removes a CDN dependency and six years of drift |
| 7 | Glicko-2 instead of Elo | ~1 day | better for small samples; use liglicko2 as reference, not as a dependency |

**Items 1–3 are the ones I would actually do.** They are cheap, they close real
defects rather than adding features, and none of them touch the payment code
that just took a week to stabilise.

---

## What was verified vs what is inferred

**Executed and confirmed:** python-chess 1.11.2 installs cleanly and parses the
exact Lichess export URL this project uses; header extraction; 47/47 move
legality validation; checkmate and stalemate detection; SVG rendering; ECO and
opening extraction on a finished game; chess.js version dates from the npm
registry; every repo count, star count, licence and push date from the GitHub
API; bcaChessHub's own licence status from the GitHub API; the three hardcoded
`K = 32` sites from the source.

**Inferred, not tested:** that ECO codes are present on *all* Lichess exports
(they were absent on an in-progress game, as expected, but no survey was run);
that chessops requires a bundler — taken from its packaging, not attempted;
every statement about licence obligations, which is a reading of the licences
and not legal advice.
