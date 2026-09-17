from django.db import models


class Association(models.Model):
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default='Zimbabwe')
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    logo = models.ImageField(upload_to='association_logos/', blank=True, null=True)
    website = models.URLField(blank=True)
    founded_year = models.PositiveIntegerField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        # The site calls these clubs, and so does the admin. The class and
        # table keep the old name: five apps hold foreign keys to it and
        # renaming the model means a schema-wide migration against a live
        # database for something no visitor can see. See to_clubs.py in the
        # commit that introduced this.
        verbose_name = 'club'
        verbose_name_plural = 'clubs'

    def __str__(self):
        return self.name
