"""Put a real game on a real match so the viewer can be looked at.

Local only, and it picks an existing match rather than inventing one, so what
gets screenshotted is the page a visitor actually gets.
"""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from matches.models import Match

PGN = '''[Event "Bulawayo Open"]
[Site "https://lichess.org/abc12345"]
[Result "1-0"]
[Opening "Philidor Defence: Morphy's Opera Game"]
[TimeControl "600+5"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6
7. Qb3 Qe7 8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8
13. Rxd7 Rxd7 14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
'''

m = Match.objects.filter(black_player__isnull=False).order_by('pk').first()
if m is None:
    raise SystemExit('no match with two players in this database')
m.pgn = PGN
m.lichess_game_id = 'abc12345'
m.result = 'white_win'
m.save()
print('match', m.pk, '->', m.white_player, 'vs', m.black_player)
print('url: /matches/%s/' % m.pk)
