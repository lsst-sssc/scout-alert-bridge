"""Per-filter diagnostics over the current Scout population.

Shows how many active candidates pass each SSSC section 2.1 filter, how many pass
everything, and which objects are near misses (fail exactly one filter) — useful for
sanity-checking trigger-rate expectations and tuning during the soak period.
"""

from django.core.management.base import BaseCommand
from tom_jpl.models import ScoutDetail

from scout_publisher.filters import RUBIN_TOO_FILTERS, evaluate_filters


class Command(BaseCommand):
    help = 'Show per-filter Rubin ToO pass counts for the current (active) Scout population.'

    def handle(self, *args, **options):
        details = list(ScoutDetail.objects.filter(active=True).select_related('target'))
        total = len(details)
        self.stdout.write(f'Active Scout candidates: {total}')
        if not total:
            return

        pass_counts = dict.fromkeys((key for key, _label, _f in RUBIN_TOO_FILTERS), 0)
        passing_all = []
        near_misses = []
        for detail in details:
            results = evaluate_filters(detail)
            for key, ok in results.items():
                pass_counts[key] += ok
            failing = [key for key, ok in results.items() if not ok]
            if not failing:
                passing_all.append(detail.target.name)
            elif len(failing) == 1:
                near_misses.append((detail.target.name, failing[0]))

        self.stdout.write('\nPer-filter pass counts:')
        for key, label, _func in RUBIN_TOO_FILTERS:
            self.stdout.write(f'  {key:<18} {pass_counts[key]:>4}/{total}   ({label})')

        self.stdout.write(f'\nPassing all filters: {len(passing_all)}'
                          + (f'  ({", ".join(passing_all)})' if passing_all else ''))
        self.stdout.write(f'Near misses (fail exactly one): {len(near_misses)}')
        for name, failing_key in near_misses:
            self.stdout.write(f'  {name}: fails {failing_key}')
