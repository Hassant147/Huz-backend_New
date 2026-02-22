from django.db import models
from common.models import UserProfile
from partners.models import PartnerProfile
import uuid


class ChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    SENDER_TYPE_CHOICES = [('User', 'user'), ('Operator', 'operator'), ('Admin', 'admin')]
    user = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, related_name="user")
    partner = models.ForeignKey(PartnerProfile, on_delete=models.SET_NULL, null=True, related_name="partner")
    sender = models.CharField(max_length=50, choices=SENDER_TYPE_CHOICES)
    message = models.CharField(max_length=1000)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True)
    is_delivered = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'partner', '-date']),
            models.Index(fields=['is_delivered']),
            models.Index(fields=['is_read']),
        ]
        ordering = ['-date']

    def save(self, *args, **kwargs):
        # Ensure ID exists before saving
        if not self.id:
            self.id = uuid.uuid4()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.partner} ({self.date})"

