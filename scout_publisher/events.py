"""Derive Rubin ToO candidate events from the Scout state maintained by ``tom_jpl``.

State machine per object (keyed by NEOCP temporary designation ``tdes``):

- not in candidate set + active + passes all filters  -> ``new_candidate``
- in candidate set + active + passes + new ``last_run`` with tracked changes -> ``updated``
- in candidate set + active + no longer passes (SSSC doc section 2.3) -> ``cancelled``
- in candidate set + no longer on Scout (``ScoutDetail.active`` False) -> ``left_neocp``

"in candidate set" is determined from the most recent :class:`PublishedEvent` for the
object *in the same* ``filter_mode``, so that relaxed test runs and strict runs keep
separate lineages; objects that never pass the filters generate no events. Pure-ephemeris changes
(``HISTORY_UNTRACKED_FIELDS``) do not generate ``updated`` events.

``updatescout``'s MPC pass renames a designated Target to its IAU designation (the trksub
survives as an alias), and an object can be designated while still active on Scout — so
the previous event is looked up under *every* name the target has carried, and the trksub
that keyed the lineage stays the ``tdes`` (and Kafka message key) for the rest of it.
"""

from django.conf import settings
from django.utils import timezone
from tom_jpl.models import ScoutDetailHistory

from .filters import RUBIN_TOO_FILTERS, evaluate_filters
from .models import PublishedEvent

ALL_FILTER_KEYS = tuple(key for key, _label, _func in RUBIN_TOO_FILTERS)

SCOUT_OBJECT_URL = 'https://cneos.jpl.nasa.gov/scout/#/object/'


def build_payload(event_type, tdes, scout_detail, filter_results, changes, iau_designation=None,
                  filter_mode='strict'):
    sd = scout_detail
    last_run = sd.last_run.isoformat() if sd.last_run else None
    return {
        'schema_version': settings.SCHEMA_VERSION,
        'event_type': event_type,
        'event_id': f'{tdes}:{last_run}:{event_type}',
        'tdes': tdes,
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
            'h_mag': getattr(sd.target, 'abs_mag', None),
            't_ephem': sd.t_ephem.isoformat() if sd.t_ephem else None,
            'url': SCOUT_OBJECT_URL + tdes,
        },
        'mpc_status': sd.mpc_status,
        'mpc_reference': sd.mpc_reference,
        'filters': {
            'version': settings.FILTER_CRITERIA_VERSION,
            # Honest full-criteria result regardless of filter_mode - relaxed test runs can
            # still emit events where this is False (see provenance.filter_mode).
            'passes': all(filter_results.values()),
            'results': filter_results,
        },
        'changes': changes,
        'provenance': {
            'source': 'JPL Scout API',
            'api_signature': settings.SCOUT_API_VERSION,
            'bridge_version': settings.BRIDGE_VERSION,
            'polled_at': timezone.now().isoformat(),
            'filter_mode': filter_mode,
        },
    }


def _latest_changes(target):
    """Tracked-field changes between the target's two most recent Scout history rows."""
    rows = list(ScoutDetailHistory.objects.filter(target=target).order_by('-last_run')[:2])
    if len(rows) < 2:
        return {}
    return rows[0].changes_from(rows[1])


def _iau_designation(scout_detail, tdes):
    """The IAU designation once ``updatescout``'s MPC pass has settled it, else None.

    A designated object's Target *is* renamed to the designation (trksub kept as an
    alias), so a primary name differing from the lineage's trksub is the designation. A
    submission the MPC folded into another object carries its surviving identity in
    ``merged_into`` instead — names are never copied onto the retired Target.
    """
    if scout_detail.merged_into:
        return scout_detail.merged_into
    name = scout_detail.target.name
    return name if name != tdes else None


def derive_event(scout_detail, required_filter_keys=None):
    """Return an unsaved :class:`PublishedEvent` for this object's current state, or None.

    Compares the current Scout state against the object's most recent published event to
    decide which (if any) transition to emit. At most one event is derived per object per
    cycle; multi-step transitions resolve over consecutive cycles.

    ``required_filter_keys``: normally ``None``, meaning a candidate must pass every
    Section 2.1 filter (see :data:`scout_publisher.filters.RUBIN_TOO_FILTERS`). Pass a
    reduced key set (e.g. :data:`scout_publisher.filters.CORE_FILTER_KEYS`) to gate on
    only those filters instead - used by ``publish_scout_events --relaxed-filters`` for
    isolation testing against real Scout data, since a real ``impact_rating>=3`` object is
    genuinely rare. The payload's ``filters.results``/``filters.passes`` always reflect the
    full, honest evaluation regardless of this parameter; only the gating decision changes.
    """
    target = scout_detail.target
    filter_results = evaluate_filters(scout_detail)
    gating_keys = required_filter_keys or ALL_FILTER_KEYS
    filter_mode = 'strict' if required_filter_keys is None else 'relaxed_test'
    passes = all(filter_results[key] for key in gating_keys)

    # Candidate-set membership is tracked per filter_mode. The two modes ask different
    # questions of the same object, so a shared history makes them overwrite each other's
    # answers: a relaxed run admits an object the strict criteria reject, the next strict
    # run reads that as passing -> failing and emits `cancelled`, the next relaxed run
    # emits `new_candidate` again, and so on for as long as the modes alternate. Production
    # only ever runs strict, where this filter matches every row and changes nothing.
    #
    # Matched under every name the target has carried: `updatescout` renames a designated
    # Target to its IAU designation (possibly while it is still active on Scout), and the
    # lineage must survive the rename rather than restart under the new name.
    names = [target.name] + [alias.name for alias in target.aliases.all()]
    last_event = (PublishedEvent.objects
                  .filter(tdes__in=names, payload__provenance__filter_mode=filter_mode)
                  .order_by('-created', '-pk').first())
    in_set = last_event.in_candidate_set if last_event else False
    tdes = last_event.tdes if last_event else target.name

    event_type = None
    changes = {}
    iau_designation = None

    if not scout_detail.active:
        if in_set:
            event_type = PublishedEvent.EventType.LEFT_NEOCP
            iau_designation = _iau_designation(scout_detail, tdes)
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

    payload = build_payload(event_type, tdes, scout_detail, filter_results, changes, iau_designation,
                            filter_mode=filter_mode)
    return PublishedEvent(
        tdes=tdes,
        last_run=scout_detail.last_run,
        event_type=event_type,
        payload=payload,
    )


def _serializable(changes):
    return {field: [old, new] for field, (old, new) in changes.items()}
