from django.contrib import admin

from .models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    """Percival posts news from here.

    Laid out for the job actually being done, which is relaying somebody
    else's announcement with credit: the source and the link to the original
    sit in their own group at the top, right where they are easy to fill in
    and hard to forget.
    """

    list_display = ('title', 'source', 'published_at', 'is_published', 'club')
    list_filter = ('is_published', 'source', 'club')
    search_fields = ('title', 'summary', 'body', 'source')
    date_hierarchy = 'published_at'
    # Editable straight from the list, because the common admin action is
    # "publish the thing I drafted yesterday", not "open it and read it again".
    list_editable = ('is_published',)
    ordering = ('-published_at',)

    fieldsets = (
        ('Where it came from', {
            'fields': ('source', 'source_url'),
            'description': 'Fill in the link whenever the item is somebody '
                           'else’s work. The page shows it as "Read the '
                           'original", which is what keeps relaying it fair.',
        }),
        ('The item', {
            'fields': ('title', 'summary', 'body', 'image'),
        }),
        ('Publishing', {
            'fields': ('club', 'published_at', 'is_published', 'slug'),
            'description': 'A future date holds the item back until then. '
                           'Leave the slug empty and it is made from the title.',
        }),
    )
    prepopulated_fields = {}
