"""
News, the way Africa Chess League actually does it.

HOW AFCL WORKS, checked rather than assumed
===========================================
Percival asked whether afcl.africa pulls news automatically or an admin links
it. Fetched and read on 2026-09-17: articles carry a source byline (Zimbabwe
Chess Federation, Chess South Africa, Africa Chess Media) but every headline
links to an INTERNAL page, /news/zcf:3367. So a person relays the item and
credits the source. It is curated, not a feed.

WHY THAT IS ALSO RIGHT HERE, and not just imitation:

  * An automatic feed needs the other federations to publish one. The
    Zimbabwe Chess Federation posts to Facebook. There is nothing to consume.
  * Republishing somebody else's article in full, automatically, is a
    copyright problem that arrives silently and lands on Percival.
  * A club site posting four items a month does not need a pipeline. It needs
    a form.

So: an administrator writes or relays an item, credits the source, and links
back to the original. source_url is what keeps that honest.
"""
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class Article(models.Model):
    """One news item, written or relayed by an administrator."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)

    # The byline. "Zimbabwe Chess Federation", or "Bulawayo Chess Hub" for
    # something written here. Free text rather than a foreign key, because the
    # sources are other organisations that will never have accounts here.
    source = models.CharField(
        max_length=120, default='Bulawayo Chess Hub',
        help_text='Who this came from. Shown as the byline.')
    source_url = models.URLField(
        blank=True,
        help_text='Link to the original. Fill this in whenever the item is '
                  'somebody else’s work, so credit is one click away.')

    summary = models.TextField(
        max_length=400,
        help_text='One or two sentences. This is what shows on the news list.')
    body = models.TextField(
        blank=True,
        help_text='The full item. Blank lines start new paragraphs. Leave it '
                  'empty for a short notice that needs no page of its own.')

    image = models.ImageField(upload_to='news/', blank=True, null=True)

    # Which club this concerns, if any. Site wide when empty, which is the
    # common case for federation news.
    club = models.ForeignKey(
        'associations.Association', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='news',
        help_text='Leave empty for news that concerns everybody.')

    published_at = models.DateTimeField(default=timezone.now)
    # Unpublished by default, so a half written item cannot appear by accident
    # on the way to being finished.
    is_published = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at']
        verbose_name = 'news item'
        verbose_name_plural = 'news'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        """A readable URL that does not collide.

        Two clubs announcing "Weekend Results" in the same month is not a
        failure case to guard against, it is the normal case, so a suffix is
        expected rather than exceptional.
        """
        base = slugify(self.title)[:200] or 'news'
        candidate, n = base, 2
        while Article.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f'{base}-{n}'
            n += 1
        return candidate

    def get_absolute_url(self):
        return reverse('news_detail', args=[self.slug])

    @property
    def is_relayed(self):
        """True when this is somebody else's work, credited here."""
        return bool(self.source_url)

    @property
    def paragraphs(self):
        """The body split on blank lines.

        Done here rather than with the linebreaks filter so the text is
        escaped as text and can never carry markup from the admin form into
        the page.
        """
        return [p.strip() for p in (self.body or '').split('\n\n') if p.strip()]
