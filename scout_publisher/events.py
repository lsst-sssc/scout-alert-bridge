"""Derive Rubin ToO candidate events from the Scout state maintained by ``tom_jpl``.

State machine per object (keyed by NEOCP temporary designation ``tdes``):

- not in candidate set + active + passes all filters  -> ``new_candidate``
- in candidate set + active + passes + new ``last_run`` with tracked changes -> ``updated``
- in candidate set + active + no longer passes (SSSC doc section 2.3) -> ``cancelled``
- in candidate set + no longer on Scout (``ScoutDetail.active`` False) -> ``left_neocp``

"in candidate set" is determined from the most recent :class:`PublishedEvent` for the
object; objects that never pass the filters generate no events. Pure-ephemeris changes
(``HISTORY_UNTRACKED_FIELDS``) do not generate ``updated`` events.
"""

from django.conf import settings
from django.utils import timezone
from tom_jpl.models import ScoutDetailHistory

from .filters import evaluate_filters
from .models import PublishedEvent

SCOUT_OBJECT_URL = 'https://cneos.jpl.nasa.gov/scout/#/object/'


def build_payload(event_type, target, scout_detail, filter_results, changes, iau_designation=None):
    sd = scout_detail
    last_run = sd.last_run.isoformat() if sd.last_run else None
    return {
        'schema_version': settings.SCHEMA_VERSION,
        'event_type': event_type,
        'event_id': f'{target.name}:{last_run}:{event_type}',
        'tdes': target.name,
        'iau_designation': iau_designation,
        'scout': {
            'last_run': last_run,
            'neo_score': sd.neo_score,
            'neo1km_score': sd.neo1km_score,
            'pha_score': sd.pha_score,
            'ieo_score': sd.ieo_score,
            'geocentric_score': sd.geocentric_score,
            'impact_rating': sd.impact_rating,
            'rms': sd.rms,
            'num_obs': sd.num_obs,
            'arc_days': sd.arc,
            'vmag': sd.vmag,
            'ra_deg': sd.ra,
            'dec_deg': sd.dec,
            'rate': sd.rate,
            'uncertainty_arcmin': sd.uncertainty,
            'uncertainty_p1_arcmin': sd.uncertainty_p1,
            'ca_dist_ld': sd.ca_dist,
            'h_mag': getattr(target, 'abs_mag', None),
            't_ephem': sd.t_ephem.isoformat() if sd.t_ephem else None,
            'url': SCOUT_OBJECT_URL + target.name,
        },
        'filters': {
            'version': settings.FILTER_CRITERIA_VERSION,
            'passes': all(filter_results.values()),
            'results': filter_results,
        },
        'changes': changes,
        'provenance': {
            'source': 'JPL Scout API',
            'bridge_version': settings.BRIDGE_VERSION,
            'polled_at': timezone.now().isoformat(),
        },
    }


def _latest_changes(target):
    """Tracked-field changes between the target's two most recent Scout history rows."""
    rows = list(ScoutDetailHistory.objects.filter(target=target).order_by('-last_run')[:2])
    if len(rows) < 2:
        return {}
    return rows[0].changes_from(rows[1])


def _iau_designation(target):
    alias = target.aliases.first() if hasattr(target, 'aliases') else None
    return alias.name if alias else None


def derive_event(scout_detail):
    """Return an unsaved :class:`PublishedEvent` for this object's current state, or None.

    Compares the current Scout state against the object's most recent published event to
    decide which (if any) transition to emit. At most one event is derived per object per
    cycle; multi-step transitions resolve over consecutive cycles.
    """
    target = scout_detail.target
    last_event = PublishedEvent.objects.filter(tdes=target.name).order_by('-created', '-pk').first()
    in_set = last_event.in_candidate_set if last_event else False

    filter_results = evaluate_filters(scout_detail)
    passes = all(filter_results.values())

    event_type = None
    changes = {}
    iau_designation = None

    if not scout_detail.active:
        if in_set:
            event_type = PublishedEvent.EventType.LEFT_NEOCP
            iau_designation = _iau_designation(target)
    elif passes and not in_set:
        event_type = PublishedEvent.EventType.NEW_CANDIDATE
    elif passes and in_set:
        if last_event.last_run and scout_detail.last_run and scout_detail.last_run > last_event.last_run:
            changes = _serializable(_latest_changes(target))
            if changes:
                event_type = PublishedEvent.EventType.UPDATED
    elif not passes and in_set:
        event_type = PublishedEvent.EventType.CANCELLED

    if event_type is None:
        return None

    payload = build_payload(event_type, target, scout_detail, filter_results, changes, iau_designation)
    return PublishedEvent(
        tdes=target.name,
        last_run=scout_detail.last_run,
        event_type=event_type,
        payload=payload,
    )


def _serializable(changes):
    return {field: [old, new] for field, (old, new) in changes.items()}
