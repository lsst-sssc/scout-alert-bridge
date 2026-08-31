# Rubin ToO meeting brief — Scout→Kafka bridge (2026-08-31)

Sources: feasibility doc §7 and §11 (fomo repo, commits `3bcd1ec`/`9785a3c`).

## How a Kafka ToO alert enters Rubin (the deployed pipeline, all public)

```
Hopskotch topic ──► rubin-ToO-producer ──► EFD (InfluxDB) ──► TooClient ──► scheduler
  (SCiMMA)          converts to HEALPix     lsst.scimma.too_alert   polls EFD,   TargetoO objects,
                    reward map              (+ .test variant)       8-day lookback  too_scripted_surveys
```

| Stage | Repo |
|---|---|
| Kafka receiver (`forward_alerts.py`, per-source `AlertFilter` classes) | `scimma/rubin-ToO-producer` — note: SCiMMA org, not lsst |
| Deployment + config (input topic, `filters:` map, Vault creds) | `lsst-sqre/phalanx` `applications/rubin-too-producer` |
| EFD topic declarations | phalanx `applications/sasquatch/charts/scimma` |
| Scheduler consumer (`TooClient` — polls the **EFD**, not Kafka) | `lsst-ts/ts_scheduler` |
| Scheduling (`TargetoO`) | `lsst/rubin_scheduler` |

**Key structural fact**: the ToO Producer doesn't forward payloads — it *converts*
them. Its output Avro schema is narrow (`source`, `alert_type`, `reward_map` HEALPix
bool array, `is_test`, `is_update`, timestamps). Everything else in our schema is
dropped on the Rubin path; the rich payload serves other subscribers and the record.

## The five points to raise

1. **A `ScoutAlertFilter` is needed** in `scimma/rubin-ToO-producer` (registered
   beside `lvk_gw`/`icecube_nu`/`superk_sn`), turning our RA/Dec + `unc_p1` into a
   binary HEALPix map, plus a phalanx `filters:` entry. Reviewable PR to existing
   repos, not new infra — **offer that we write it**; ask what sky-map convention
   they want.
2. **Single input topic limitation**: the deployed producer config takes *one* input
   topic (currently `rubin-too-dev.lvk-test-alerts`). Adding Scout needs multi-topic
   support or a second producer deployment — their call which.
3. **`alert_type` naming**: LVK uses per-approved-case names (`GW_case_B` etc.), so
   ours would be `NEO_case_*` — making SCOC approval a naming gate too.
4. **Test traffic**: they check the Hopskotch `_test` *message header* (base
   `AlertFilter.is_test`, and `TooClient` skips these independently) — more useful to
   them than our `-test` topic alone. We should set it.
5. **No retraction path downstream (possibly a latent bug — worth asking)**: every
   filter has retraction logic in `overrides_previous`, but `should_follow_up()`
   returns early on non-initial alert types so a retraction never reaches it, and the
   output schema can't express one. Our `cancelled`/`left_neocp` events have nowhere
   to land on the Rubin path today. Deliberate ("an exposure taken can't be
   un-taken") or an oversight?

## Decided on our side (defensible with precedent)

- **`left_neocp` publishes immediately and terminally**, MPC outcome fields included
  only if already settled — matching LVK `RETRACTION` (name-only, no reason) and GCN
  (transition type only; the "why" travels on a separate channel). If they ever want
  outcomes pushed, the answer is a separate informational topic, not a follow-up
  event.

## Status to report

- `Scout.scout-test` is **live**: 10-minute cycle running since 2026-08-31, 25
  `relaxed_test`-stamped messages published and verified off the broker.
- `rubin-too-dev` already has Read on `Scout.scout-test`.
- **Question for them**: does the *production* ToO Producer authenticate as
  `rubin-too-dev` or `rubin`?
