"""Local only: a few news items so the section can be looked at."""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.utils import timezone
import datetime
from news.models import Article

ITEMS = [
    dict(title='Zimbabwe Chess Federation opens doors for corporate partnerships',
         source='Zimbabwe Chess Federation',
         source_url='https://example.org/zcf-partnerships',
         summary='The ZCF is inviting corporates, institutions and government '
                 'ministries to partner with the Federation in developing chess.',
         body='The Federation has opened a programme for organisations wishing '
              'to run chess in the workplace.\n\nClubs in Bulawayo interested in '
              'hosting a corporate team should register their interest before '
              'the end of the month.',
         days=1),
    dict(title='Jarmil Ndoro earns FIDE International Arbiter title',
         source='Zimbabwe Chess Federation',
         source_url='https://example.org/zcf-arbiter',
         summary='The ZCF congratulates Jarmil Ndoro on attaining the '
                 'International Arbiter title.',
         body='The title requires a written examination and norms earned at '
              'international events.',
         days=2),
    dict(title='Bulawayo Open concludes at the City Hall',
         source='Bulawayo Chess Hub',
         source_url='',
         summary='Forty players across two days, with the Developmental '
                 'section drawing the largest entry it has had.',
         body='The Open section went to the wire.\n\nFull standings and the '
              'crosstable are on the tournament page.',
         days=4),
]

from django.conf import settings
if not settings.DEBUG:
    raise SystemExit(
        'REFUSING TO RUN: this deletes every news item and replaces it with '
        'sample text. It is local development seeding only. DEBUG is False, '
        'so this is not a development database.')

Article.objects.all().delete()
for item in ITEMS:
    days = item.pop('days')
    Article.objects.create(
        published_at=timezone.now() - datetime.timedelta(days=days),
        is_published=True, **item)

# One held back, to prove the gate in the browser as well as in a test.
Article.objects.create(
    title='Saturday results, not yet public',
    source='Bulawayo Chess Hub',
    summary='Scheduled for Saturday. Must not appear on the list today.',
    published_at=timezone.now() + datetime.timedelta(days=3),
    is_published=True)

print('seeded', Article.objects.count(), 'items, one of them future dated')
