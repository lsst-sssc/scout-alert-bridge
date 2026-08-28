from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

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
        self.assertIsNone(event.payload['iau_designation'])
        self.assertIsNone(event.payload['mpc_status'])

    def rename_to_designation(self, detail, designation):
        """Simulate updatescout's MPC pass: rename the Target, alias the trksub."""
        target = detail.target
        trksub = target.name
        target.name = designation
        target.save()
        target.aliases.create(name=trksub)
        detail.mpc_status = 'designated'
        detail.mpc_reference = 'MPEC 2026-Q53'
        detail.save()

    def test_lineage_survives_iau_rename(self):
        # updatescout can rename a still-active candidate to its IAU designation; the
        # event lineage (and Kafka key) must stay on the trksub, not restart.
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        self.rename_to_designation(detail, '2026 QQ1')
        detail.active = False
        detail.save()
        event = derive_event(detail)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, PublishedEvent.EventType.LEFT_NEOCP)
        self.assertEqual(event.tdes, 'P12test')
        self.assertEqual(event.payload['tdes'], 'P12test')
        self.assertEqual(event.payload['iau_designation'], '2026 QQ1')
        self.assertEqual(event.payload['mpc_status'], 'designated')
        self.assertEqual(event.payload['mpc_reference'], 'MPEC 2026-Q53')

    def test_no_new_candidate_after_rename_of_tracked_object(self):
        # A renamed, still-passing, still-active candidate must not restart its lineage
        # with a second new_candidate under the designation.
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        self.rename_to_designation(detail, '2026 QQ1')
        self.assertIsNone(derive_event(detail))

    def test_left_neocp_merged_submission_reports_survivor(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        detail.active = False
        detail.mpc_status = 'designated'
        detail.merged_into = '2026 QQ2'
        detail.save()
        event = derive_event(detail)
        self.assertEqual(event.event_type, PublishedEvent.EventType.LEFT_NEOCP)
        self.assertEqual(event.payload['tdes'], 'P12test')
        self.assertEqual(event.payload['iau_designation'], '2026 QQ2')

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


class RelaxedFilterTests(TestCase):
    def test_strict_mode_ignores_object_failing_only_impact_rating(self):
        detail = make_candidate(impact_rating=0)
        self.assertIsNone(derive_event(detail))

    def test_relaxed_mode_admits_object_failing_only_impact_rating(self):
        from scout_publisher.filters import CORE_FILTER_KEYS

        detail = make_candidate(impact_rating=0)
        event = derive_event(detail, required_filter_keys=CORE_FILTER_KEYS)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, PublishedEvent.EventType.NEW_CANDIDATE)
        # Honest evaluation is preserved even though relaxed mode let it through.
        self.assertFalse(event.payload['filters']['passes'])
        self.assertFalse(event.payload['filters']['results']['impact_rating'])
        self.assertEqual(event.payload['provenance']['filter_mode'], 'relaxed_test')

    def test_strict_mode_stamps_filter_mode_strict(self):
        event = derive_event(make_candidate())
        self.assertEqual(event.payload['provenance']['filter_mode'], 'strict')

    def test_relaxed_mode_still_requires_core_filters(self):
        from scout_publisher.filters import CORE_FILTER_KEYS

        detail = make_candidate(impact_rating=0, neo_score=50)
        self.assertIsNone(derive_event(detail, required_filter_keys=CORE_FILTER_KEYS))

    def test_modes_do_not_flap_against_each_other(self):
        """Alternating relaxed and strict runs must not oscillate new_candidate/cancelled.

        The two modes ask different questions of the same object, so candidate-set
        membership is tracked per filter_mode. Sharing it made a relaxed admission look to
        the next strict run like an object that had stopped passing.
        """
        from scout_publisher.filters import CORE_FILTER_KEYS

        # Passes the core filters but fails the full Section 2.1 set.
        detail = make_candidate(impact_rating=0)

        relaxed = derive_event(detail, required_filter_keys=CORE_FILTER_KEYS)
        self.assertEqual(relaxed.event_type, PublishedEvent.EventType.NEW_CANDIDATE)
        relaxed.save()

        # The strict run must not read the relaxed admission as a cancellation.
        self.assertIsNone(derive_event(detail))

        # And the relaxed lineage still considers the object announced, so it does not
        # re-announce it either.
        self.assertIsNone(derive_event(detail, required_filter_keys=CORE_FILTER_KEYS))


class PublishCommandTests(TestCase):
    def test_dry_run_writes_nothing(self):
        make_candidate()
        out = StringIO()
        call_command('publish_scout_events', '--dry-run', stdout=out)
        self.assertIn('new_candidate', out.getvalue())
        self.assertEqual(PublishedEvent.objects.count(), 0)

    def test_relaxed_filters_flag_admits_impact_rating_holdout(self):
        make_candidate(impact_rating=0)
        out = StringIO()
        call_command('publish_scout_events', '--relaxed-filters', '--dry-run', stdout=out)
        text = out.getvalue()
        self.assertIn('RELAXED FILTER MODE', text)
        self.assertIn('new_candidate', text)

    def test_without_relaxed_filters_flag_impact_rating_holdout_derives_nothing(self):
        make_candidate(impact_rating=0)
        out = StringIO()
        call_command('publish_scout_events', '--dry-run', stdout=out)
        self.assertIn('Dry run: 0 event(s)', out.getvalue())

    def test_derive_is_idempotent(self):
        make_candidate()
        out = StringIO()
        call_command('publish_scout_events', '--no-publish', stdout=out)
        call_command('publish_scout_events', '--no-publish', stdout=out)
        self.assertEqual(PublishedEvent.objects.count(), 1)


class PublishToBrokerTests(TestCase):
    """Exercise the publish phase with a stand-in for the Kafka producer."""

    class FakeStream:
        def __init__(self):
            self.writes = []

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def write(self, message, key=None):
            self.writes.append((message, key))

        def flush(self):
            pass

    def _publish_with_fake_stream(self, *args):
        from scout_publisher.management.commands.publish_scout_events import Command

        stream = self.FakeStream()
        with patch.object(Command, '_open_stream', return_value=stream):
            call_command('publish_scout_events', *args, stdout=StringIO())
        return stream

    def test_message_is_keyed_by_tdes(self):
        """Kafka orders only within a partition, so events for one object must share a key."""
        make_candidate(name='P12keyed')
        stream = self._publish_with_fake_stream()

        self.assertEqual(len(stream.writes), 1)
        message, key = stream.writes[0]
        self.assertEqual(key, 'P12keyed')
        self.assertEqual(message['tdes'], 'P12keyed')

    def test_published_rows_are_stamped(self):
        make_candidate()
        self._publish_with_fake_stream()
        self.assertEqual(PublishedEvent.objects.filter(published_at__isnull=True).count(), 0)
