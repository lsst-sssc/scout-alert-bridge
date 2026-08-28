"""Derive Rubin ToO candidate events from the current Scout state and publish them.

Runs after ``rundataquery`` + ``updatescout --skip-designations`` in each poll cycle. Two phases:

1. Derive: for every :class:`tom_jpl.models.ScoutDetail`, compute the state transition
   (if any) against the object's most recent published event and write an outbox row.
2. Publish: send every unpublished outbox row to the Kafka topic via ``hop-client``,
   stamping ``published_at`` on success. Failures leave rows unpublished for retry on
   the next cycle.
"""

import os
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone
from tom_jpl.models import ScoutDetail

from scout_publisher.events import derive_event
from scout_publisher.filters import CORE_FILTER_KEYS
from scout_publisher.models import PublishedEvent


class Command(BaseCommand):
    help = 'Derive Rubin ToO candidate events from Scout state and publish them to Kafka.'

    RECENT_LIMIT = 10

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Derive and print events without writing or publishing anything.')
        parser.add_argument('--topic', default=settings.SCOUT_TOPIC_URL,
                            help='Kafka topic URL (default: settings.SCOUT_TOPIC_URL).')
        parser.add_argument('--no-publish', action='store_true',
                            help='Write outbox rows but skip the publish phase.')
        parser.add_argument('--status', action='store_true',
                            help='Print outbox publication state and exit, without deriving or publishing.')
        parser.add_argument('--relaxed-filters', action='store_true',
                            help='TESTING ONLY: gate on neo_score/geocentric_score/abs_mag (H) only, '
                                 'waiving impact_rating and the other Section 2.1 filters (a real '
                                 'impact_rating>=3 object is genuinely rare, so this is the only '
                                 'practical way to exercise a real candidate end-to-end). Payloads '
                                 'still honestly report the full filter results and are stamped '
                                 "provenance.filter_mode='relaxed_test'. Never pass this to the "
                                 'scheduled/production run.')

    def handle(self, *args, **options):
        if options['status']:
            self._status(options['topic'])
            return
        if options['relaxed_filters']:
            self.stdout.write(self.style.WARNING(
                'RELAXED FILTER MODE: gating on neo_score/geocentric_score/abs_mag only. '
                'For isolation testing against a -test topic - do not use in production.'))
        derived = self._derive(dry_run=options['dry_run'], relaxed=options['relaxed_filters'])
        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'Dry run: {derived} event(s) derived, nothing written.'))
            return
        self.stdout.write(f'{derived} new event(s) written to the outbox.')
        if not options['no_publish']:
            self._publish(options['topic'])

    def _status(self, topic):
        """Report outbox publication state: the operational health check for the poll cycle.

        A non-zero pending count that does not drain between cycles is the signature of a
        broker outage or a credential/ACL problem — the events are derived and safe, but
        nothing is reaching Rubin.
        """
        events = PublishedEvent.objects.all()
        total = events.count()
        pending = events.filter(published_at__isnull=True).order_by('created')
        pending_count = pending.count()

        self.stdout.write(f'Topic:       {topic}')
        self.stdout.write(f'Outbox rows: {total}   published: {total - pending_count}   pending: {pending_count}')

        if pending_count:
            oldest = pending.first()
            age = timezone.now() - oldest.created
            style = self.style.ERROR if age > timedelta(hours=1) else self.style.WARNING
            self.stdout.write(style(f'Oldest pending: {oldest.tdes} {oldest.event_type}, stuck for '
                                    f'{self._format_age(age)} (derived {oldest.created:%Y-%m-%d %H:%M:%SZ})'))
        elif total:
            self.stdout.write(self.style.SUCCESS('Outbox fully drained.'))

        if not total:
            self.stdout.write('No events derived yet.')
            return

        self.stdout.write('\nBy event type:')
        for row in events.values('event_type').annotate(n=Count('pk')).order_by('-n'):
            self.stdout.write(f'  {row["event_type"]:<15} {row["n"]:>4}')

        self.stdout.write(f'\nMost recent {self.RECENT_LIMIT}:')
        for event in events.order_by('-created')[:self.RECENT_LIMIT]:
            state = 'sent' if event.published_at else 'PENDING'
            mode = event.payload.get('provenance', {}).get('filter_mode', '?')
            self.stdout.write(f'  {state:<8} {event.tdes:<10} {event.event_type:<15} {mode:<13} '
                              f'{event.created:%Y-%m-%d %H:%M:%SZ}')

    @staticmethod
    def _format_age(age):
        total_minutes = int(age.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        return f'{hours}h{minutes:02d}m' if hours else f'{minutes}m'

    def _derive(self, dry_run=False, relaxed=False):
        required_filter_keys = CORE_FILTER_KEYS if relaxed else None
        count = 0
        for scout_detail in ScoutDetail.objects.select_related('target').iterator():
            event = derive_event(scout_detail, required_filter_keys=required_filter_keys)
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
                    # Key by tdes so every event for one object lands on the same partition:
                    # Kafka orders messages only within a partition, so an unkeyed stream can
                    # deliver an object's `cancelled` ahead of the `new_candidate` it follows.
                    s.write(event.payload, key=event.tdes)
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
        elif os.environ.get('SCOUT_NO_AUTH', '').lower() in ('1', 'true', 'yes'):
            # For local testing against a plaintext broker (e.g. docker compose --profile
            # localkafka) before real SCiMMA credentials exist.
            stream = Stream(auth=False)
        else:
            stream = Stream()  # falls back to `hop auth` credentials
        return stream.open(topic, 'w')
