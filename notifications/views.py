from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from .models import Notification


@login_required
def notification_centre(request):
    qs = Notification.objects.filter(recipient=request.user)

    type_filter = request.GET.get('type', '')
    read_filter = request.GET.get('read', '')

    if type_filter:
        qs = qs.filter(type=type_filter)
    if read_filter == 'unread':
        qs = qs.filter(is_read=False)
    elif read_filter == 'read':
        qs = qs.filter(is_read=True)

    # PAGE FIRST, MARK READ AFTERWARDS.
    #
    # This used to mark everything read BEFORE paging, using the same queryset
    # it was about to display. Two things followed, both live:
    #
    #   1. Under the "Unread" filter that queryset IS is_read=False, so the
    #      update emptied the very set the paginator then evaluated. Clicking
    #      Unread marked everything read and showed a blank page, every time.
    #
    #   2. Even with no filter, every row reached the template already marked
    #      read, so centre.html's highlighting for new items, which has been
    #      written and correct since the page was built, had never once
    #      rendered.
    #
    # Same shape as the dashboard bug fixed on 2026-09-16: call .update() and
    # then evaluate the same lazy queryset.
    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get('page'))

    # Force evaluation while the rows still carry their real read state, so
    # the template can show which ones were new.
    shown = list(page_obj.object_list)

    # Only what was actually put in front of the reader. Marking the other
    # four pages read as well would mean they never get to see those as new,
    # which is the one thing they opened this page to find out.
    unseen = [n.pk for n in shown if not n.is_read]
    if unseen:
        Notification.objects.filter(pk__in=unseen).update(is_read=True)

    params    = request.GET.copy()
    params.pop('page', None)

    return render(request, 'notifications/centre.html', {
        'page_obj': page_obj,
        'type_choices': Notification.TYPE_CHOICES,
        'type_filter': type_filter,
        'read_filter': read_filter,
        'query_string': params.urlencode(),
    })


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    messages.success(request, 'All notifications marked as read.')
    return redirect('notification_centre')
