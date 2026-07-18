import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from scout_publisher.events import derive_event
from scout_publisher.models import PublishedEvent

from .test_events import advance, make_candidate, record_event

SCHEMA = json.loads((Path(__file__).parents[2] / 'schema' / 'event-1.0.json').read_text())


def validate(payload):
    import jsonschema

    jsonschema.validate(payload, SCHEMA)


class SchemaTests(TestCase):
    def test_new_candidate_payload_matches_schema(self):
        event = derive_event(make_candidate())
        validate(event.payload)

    def test_updated_payload_matches_schema(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        advance(detail, num_obs=20)
        validate(derive_event(detail).payload)

    def test_left_neocp_payload_matches_schema(self):
        detail = make_candidate()
        record_event(detail, PublishedEvent.EventType.NEW_CANDIDATE)
        detail.active = False
        detail.save()
        validate(derive_event(detail).payload)


class ScoutStatsTests(TestCase):
    def test_stats_output(self):
        make_candidate(name='P12pass')
        make_candidate(name='P12miss', rms=2.5)  # near miss: fails only rms
        out = StringIO()
        call_command('scout_stats', stdout=out)
        text = out.getvalue()
        self.assertIn('Active Scout candidates: 2', text)
        self.assertIn('Passing all filters: 1  (P12pass)', text)
        self.assertIn('P12miss: fails rms', text)
