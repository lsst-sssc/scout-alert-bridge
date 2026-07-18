from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from tom_jpl.models import ScoutDetail, ScoutDetailHistory
from tom_targets.models import Target

from scout_publisher.events import derive_event
from scout_publisher.models import PublishedEvent

T0 = datetime(2026, 7, 15, 10, 0, 0, tzinfo=dt_timezone.utc)

# Values passing every Section 2.1 filter (dec > 0 branch).
PASSING = dict(
    num_obs=12, neo_score=100, geocentric_score=0, impact_rating=3, rms=0.4,
    arc=0.31, vmag=22.1, ra=187.3, dec=12.4, rate=4.2,
    uncertainty=30.0, uncertainty_p1=240.0, ca_dist=0.8, last_run=T0,
)


def make_candidate(name='P12test', active=True, **overrides):
    target = Target.objects.create(name=name, type=Target.NON_SIDEREAL)
    if hasattr(target, 'abs_mag'):
        target.abs_mag = 27.9
        target.save()
    fields = {**PASSING, **overrides}
    detail = ScoutDetail.objects.create(target=target, active=active, **fields)
    ScoutDetailHistory.objects.create(target=target, **fields)
    return detail


def advance(detail, **changed):
    """Simulate a new Scout run: bump last_run, apply changes, append a history row."""
    fields = {f: getattr(detail, f) for f in PASSING}
    fields.update(changed)
    fields['last_run'] = detail.last_run + timedelta(hours=1)
    for key, value in fields.items():
        setattr(detail, key, value)
    detail.save()
    ScoutDetailHistory.objects.create(target=detail.target, **fields)
    return detail


def record_event(detail, event_type):
    event = derive_event(detail)
    assert event is not None and event.event_type == event_type, event
    event.save()
    return event


class DeriveEventTests(TestCase):
    def test_new_candidate_when_first_passing(self):
        detail = make_candidate()
        event = derive_event(detail)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, PublishedEvent.EventType.NEW_CANDIDATE)
        self.assertEqual(event.payload['tdes'], 'P12test')
        self.assertTrue(event.payload['filters']['passes'])

    def test_no_event_when_never_passing(self):
        detail = make_candidate(neo_score=50)
        self.assertIsNone(derive_event(detail))

    def test_updated_on_tracked_change(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        advance(detail, num_obs=20, rms=0.3)
        event = derive_event(detail)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, PublishedEvent.EventType.UPDATED)
        self.assertEqual(event.payload['changes']['num_obs'], [12, 20])

    def test_no_updated_on_ephemeris_only_change(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        advance(detail, vmag=22.3, ra=188.0)  # untracked, ephemeris-only fields
        self.assertIsNone(derive_event(detail))

    def test_cancelled_when_filter_stops_passing(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        advance(detail, rms=2.5)
        event = derive_event(detail)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, PublishedEvent.EventType.CANCELLED)

    def test_left_neocp_when_departed(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        detail.active = False
        detail.save()
        event = derive_event(detail)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, PublishedEvent.EventType.LEFT_NEOCP)

    def test_no_event_when_departed_but_never_candidate(self):
        detail = make_candidate(active=False, neo_score=50)
        self.assertIsNone(derive_event(detail))

    def test_recandidacy_after_cancellation(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        advance(detail, rms=2.5)
        record_event(detail, PublishedEvent.EventType.CANCELLED)
        advance(detail, rms=0.5)
        event = derive_event(detail)
        self.assertEqual(event.event_type, PublishedEvent.EventType.NEW_CANDIDATE)


class PublishCommandTests(TestCase):
    def test_dry_run_writes_nothing(self):
        make_candidate()
        out = StringIO()
        call_command('publish_scout_events', '--dry-run', stdout=out)
        self.assertIn('new_candidate', out.getvalue())
        self.assertEqual(PublishedEvent.objects.count(), 0)

    def test_derive_is_idempotent(self):
        make_candidate()
        out = StringIO()
        call_command('publish_scout_events', '--no-publish', stdout=out)
        call_command('publish_scout_events', '--no-publish', stdout=out)
        self.assertEqual(PublishedEvent.objects.count(), 1)
