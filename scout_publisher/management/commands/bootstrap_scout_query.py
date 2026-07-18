"""Idempotently create the saved broad Scout query and service user that ``ingest_scout`` needs."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from tom_dataservices.models import DataServiceQuery


class Command(BaseCommand):
    help = 'Create (if missing) the saved broad Scout DataServiceQuery and service superuser.'

    def add_arguments(self, parser):
        parser.add_argument('--name', default=settings.SCOUT_QUERY_NAME,
                            help='Name for the saved query (default: settings.SCOUT_QUERY_NAME).')
        parser.add_argument('--parameters',
                            default=json.dumps(self.BROAD_PARAMETERS),
                            help='JSON dict of Scout query parameters (default: no cuts — the whole list).')

    # Deliberately-broad defaults: every ScoutForm key present (the threshold code requires
    # them), all cuts wide open so the whole current Scout list is ingested.
    BROAD_PARAMETERS = {
        'tdes': '',
        'neo_score_min': 0,
        'pha_score_min': 0,
        'geo_score_max': None,
        'impact_rating_min': None,
        'ca_dist_min': None,
        'pos_unc_min': None,
        'pos_unc_max': None,
    }

    def handle(self, *args, **options):
        user_model = get_user_model()
        if not user_model.objects.filter(is_superuser=True).exists():
            user = user_model.objects.create_superuser(username='scout_bridge', email='')
            user.set_unusable_password()
            user.save()
            self.stdout.write(self.style.SUCCESS('Created service superuser "scout_bridge".'))

        query, created = DataServiceQuery.objects.get_or_create(
            name=options['name'],
            defaults={
                'data_service': 'Scout',
                'parameters': json.loads(options['parameters']),
            },
        )
        verb = 'Created' if created else 'Found existing'
        self.stdout.write(self.style.SUCCESS(f'{verb} saved Scout query "{query.name}" (id={query.pk}).'))
