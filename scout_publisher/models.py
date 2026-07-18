from django.db import models


class PublishedEvent(models.Model):
    """Transactional outbox for Rubin ToO candidate events.

    Rows are created (unpublished) when an event is derived from the Scout state, and
    stamped ``published_at`` only after the Kafka broker accepts the message — so a
    failed publish is retried on the next cycle, and the unique key makes replays
    idempotent.
    """

    class EventType(models.TextChoices):
        NEW_CANDIDATE = 'new_candidate', 'New candidate'
        UPDATED = 'updated', 'Updated'
        CANCELLED = 'cancelled', 'Cancelled'
        LEFT_NEOCP = 'left_neocp', 'Left NEOCP'

    tdes = models.CharField(max_length=32, help_text='NEOCP temporary designation (Kafka message key)')
    last_run = models.DateTimeField(null=True, blank=True, help_text='Scout lastRun the event derives from')
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    payload = models.JSONField(help_text='Full JSON message as published')
    created = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('tdes', 'last_run', 'event_type')
        ordering = ['created', 'pk']

    def __str__(self):
        return f'{self.tdes} {self.event_type} @ {self.last_run}'

    @property
    def in_candidate_set(self):
        """Whether this event leaves the object in the active candidate set."""
        return self.event_type in (self.EventType.NEW_CANDIDATE, self.EventType.UPDATED)
