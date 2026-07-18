# Scout NEO ToO event stream — message schema v1.0

Proposal for comment by the Rubin ToO team and the SSSC NEOs WG.
Machine-readable version: [`schema/event-1.0.json`](../schema/event-1.0.json)
(JSON Schema, validated in CI against the payloads the bridge generates).

## Stream semantics

- **Transport**: Kafka (SCiMMA Hopskotch), JSON messages, one event per message.
- **Message key**: `tdes` — the NEOCP temporary designation, so per-object ordering holds.
- **Idempotency**: `(tdes, scout.last_run, event_type)` uniquely identifies an event
  (`event_id` is its string form). Consumers must treat redelivery as a no-op.
- **Candidacy contract**: an object enters the candidate set with `new_candidate` and
  leaves it with `cancelled` or `left_neocp`. While in the set, `updated` events carry
  orbit-quality changes. Objects that never pass the filter criteria are never published.

## Event types

| `event_type` | Meaning |
|---|---|
| `new_candidate` | Object passes **all** SSSC §2.1 filters (first time, or again after leaving the set) |
| `updated` | In-set object recomputed by Scout with tracked-field changes (pure ephemeris churn — ra/dec/vmag/rate/t_ephem — is suppressed) |
| `cancelled` | In-set object no longer passes ≥1 filter (SSSC §2.3) but is still on Scout |
| `left_neocp` | In-set object left the Scout list (designated / removed / impacted); `iau_designation` carries the IAU provisional designation when the MPC has assigned one |

## Fields

Top level: `schema_version` (`"1.0"`), `event_type`, `event_id`, `tdes`,
`iau_designation` (nullable), `scout`, `filters`, `changes`, `provenance`.

**`scout`** — the Scout snapshot the event derives from. All values nullable
(Scout omits fields for poorly constrained objects): `last_run` (ISO 8601),
`neo_score`, `neo1km_score`, `pha_score`, `ieo_score`, `geocentric_score` (0–100 digest
scores), `impact_rating` (0–4), `rms` (arcsec), `num_obs`, `arc_days`, `vmag`, `ra_deg`,
`dec_deg`, `rate` (arcsec/min), `uncertainty_arcmin`, `uncertainty_p1_arcmin` (1σ
plane-of-sky, now / +1 day), `ca_dist_ld` (lunar distances), `h_mag`, `t_ephem`,
`url` (CNEOS Scout object page).

**`filters`** — `version` (criteria document version, e.g. `"SSSC-NEO-WG-v0.2"`),
`passes` (boolean AND), `results` (per-filter booleans keyed `neo_score`,
`geocentric_score`, `abs_mag`, `impact_rating`, `rms`, `obs_arc`, `vmag`, `unc_p1`,
`rate`). The §2.2 airmass/observability filter is deliberately left to the consumer.

**`changes`** — `{field: [old, new]}` for tracked fields that differ from the previous
Scout run; empty except on `updated` events.

**`provenance`** — `source` (`"JPL Scout API"`), `api_signature` (Scout API version),
`bridge_version`, `polled_at`.

## Example

```json
{
  "schema_version": "1.0",
  "event_type": "new_candidate",
  "event_id": "P12abcd:2026-07-15T10:31:00+00:00:new_candidate",
  "tdes": "P12abcd",
  "iau_designation": null,
  "scout": {
    "last_run": "2026-07-15T10:31:00+00:00", "neo_score": 100,
    "geocentric_score": 0, "impact_rating": 3, "rms": 0.4, "num_obs": 12,
    "arc_days": 0.31, "vmag": 22.1, "ra_deg": 187.3, "dec_deg": -12.4,
    "rate": 4.2, "uncertainty_p1_arcmin": 240.0, "ca_dist_ld": 0.8,
    "h_mag": 27.9, "url": "https://cneos.jpl.nasa.gov/scout/#/object/P12abcd"
  },
  "filters": {
    "version": "SSSC-NEO-WG-v0.2", "passes": true,
    "results": {"neo_score": true, "geocentric_score": true, "abs_mag": true,
                 "impact_rating": true, "rms": true, "obs_arc": true,
                 "vmag": true, "unc_p1": true, "rate": true}
  },
  "changes": {},
  "provenance": {"source": "JPL Scout API", "bridge_version": "0.1.0",
                  "polled_at": "2026-07-15T10:40:12+00:00"}
}
```

## Versioning

`schema_version` follows semver-lite: additive, backward-compatible fields bump the
minor version; anything breaking bumps the major version and is announced ahead of
time. The schema is provider-neutral by design so a future JPL-operated feed could be
drop-in compatible.
