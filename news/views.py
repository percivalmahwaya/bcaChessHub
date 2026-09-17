from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Article


def _live():
    """Only published items, and only ones whose time has come.

    published_at is allowed to be in the future so an item can be written on
    Thursday and appear on Saturday. Filtering on is_published alone would
    publish it the moment it was saved, which is the opposite of what setting
    a future date means.
    """
    return Article.objects.filter(
        is_published=True, published_at__lte=timezone.now()
    ).select_related('club')


def news_list(request):
    paginator = Paginator(_live(), 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'news/list.html', {
        'articles': page_obj,
        'page_obj': page_obj,
        'query_string': '',
    })


def news_detail(request, slug):
    article = get_object_or_404(_live(), slug=slug)
    more = _live().exclude(pk=article.pk)[:4]
    return render(request, 'news/detail.html', {
        'article': article,
        'more': more,
    })
