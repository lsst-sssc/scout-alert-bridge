"""Derive Rubin ToO candidate events from the current Scout state and publish them.

Runs after ``ingest_scout`` in each poll cycle. Two phases:

1. Derive: for every :class:`tom_jpl.models.ScoutDetail`, compute the state transition
   (if any) against the object's most recent published event and write an outbox row.
2. Publish: send every unpublished outbox row to the Kafka topic via ``hop-client``,
   stamping ``published_at`` on success. Failures leave rows unpublished for retry on
   the next cycle.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone
from tom_jpl.models import ScoutDetail

from scout_publisher.events import derive_event
from scout_publisher.models import PublishedEvent


class Command(BaseCommand):
    help = 'Derive Rubin ToO candidate events from Scout state and publish them to Kafka.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Derive and print events without writing or publishing anything.')
        parser.add_argument('--topic', default=settings.SCOUT_TOPIC_URL,
                            help='Kafka topic URL (default: settings.SCOUT_TOPIC_URL).')
        parser.add_argument('--no-publish', action='store_true',
                            help='Write outbox rows but skip the publish phase.')

    def handle(self, *args, **options):
        derived = self._derive(dry_run=options['dry_run'])
        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'Dry run: {derived} event(s) derived, nothing written.'))
            return
        self.stdout.write(f'{derived} new event(s) written to the outbox.')
        if not options['no_publish']:
            self._publish(options['topic'])

    def _derive(self, dry_run=False):
        count = 0
        for scout_detail in ScoutDetail.objects.select_related('target').iterator():
            event = derive_event(scout_detail)
            if event is None:
                continue
            if dry_run:
                self.stdout.write(f'  [dry-run] {event.tdes}: {event.event_type} (lastRun {event.last_run})')
                count += 1
                continue
            try:
                event.save()
                count += 1
                self.stdout.write(f'  {event.tdes}: {event.event_type} (lastRun {event.last_run})')
            except IntegrityError:
                # Already derived in a previous cycle (idempotency key) — nothing to do.
                pass
        return count

    def _publish(self, topic):
        pending = list(PublishedEvent.objects.filter(published_at__isnull=True))
        if not pending:
            self.stdout.write('Nothing to publish.')
            return

        try:
            stream = self._open_stream(topic)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'Could not connect to {topic}: {exc}; '
                                               f'{len(pending)} event(s) left in outbox for retry.'))
            return

        published = 0
        with stream as s:
            for event in pending:
                try:
                    s.write(event.payload)
                    s.flush()
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f'Publish failed for {event}: {exc}; will retry.'))
                    break
                event.published_at = timezone.now()
                event.save(update_fields=['published_at'])
                published += 1
        self.stdout.write(self.style.SUCCESS(f'Published {published}/{len(pending)} event(s) to {topic}.'))

    def _open_stream(self, topic):
        from hop import Stream
        from hop.auth import Auth

        username = os.environ.get('SCIMMA_USERNAME')
        password = os.environ.get('SCIMMA_PASSWORD')
        if username and password:
            stream = Stream(auth=Auth(username, password))
        else:
            stream = Stream()  # falls back to `hop auth` credentials
        return stream.open(topic, 'w')
