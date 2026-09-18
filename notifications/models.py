from django.db import models
from django.contrib.auth.models import User


class Notification(models.Model):
    TYPE_CHOICES = [
        ('tournament_created', 'Tournament Created'),
        ('registration_confirmed', 'Registration Confirmed'),
        ('match_scheduled', 'Match Scheduled'),
        ('result_posted', 'Result Posted'),
        ('ranking_updated', 'Ranking Updated'),
        ('payment_received', 'Payment Received'),
        ('deadline_reminder', 'Registration Deadline Reminder'),
        ('general', 'General Announcement'),
    ]

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    type = models.CharField(max_length=30, choices=TYPE_CHOICES, default='general')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)
    email_sent = models.BooleanField(default=False)

    class Meta:
        # -pk breaks the tie. sent_at is auto_now_add, and several
        # notifications raised in the same request (a round completing, a
        # tournament finishing) can land on the same timestamp, leaving their
        # order undefined: the newest could appear below the older ones, and
        # differently on each page load. Same reason the ratings page breaks
        # ties on username.
        ordering = ['-sent_at', '-pk']

    def __str__(self):
        return f"[{self.type}] → {self.recipient.username}"

    def mark_read(self):
        self.is_read = True
        self.save(update_fields=['is_read'])
