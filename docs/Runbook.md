# Runbook: checking the bridge is working, and debugging it when it isn't

Everything here is command-line; the project is headless by design (the Django admin at
`/admin/` exists for inspection but nothing serves it in production, where the bridge is a
CronJob that runs and exits).

## Setup

Every command below assumes the venv and the database environment:

```sh
cd ~/git/scout-alert-bridge && source .venv/bin/activate
export DB_HOST=localhost DB_PORT=5433 DB_PASSWORD=scout_bridge   # local docker compose db
```

In the cluster, exec into the pod instead; the DB and topic settings come from the
deployment's environment.

## The three-command health check

### 1. Is the stream healthy end to end?

```sh
./manage.py publish_scout_events --status
```

```
Topic:       kafka://kafka.scimma.org/Scout.test-tim
Outbox rows: 98   published: 98   pending: 0
Outbox fully drained.

By event type:
  new_candidate     50
  cancelled         25
  left_neocp        23

Most recent 10:
  sent     ZTF10Fc    new_candidate   relaxed_test  2026-08-26 22:11:11Z
  sent     orA5076    cancelled       strict        2026-08-25 16:51:54Z
```

**Good** = `pending: 0`. Anything else that does not clear on the next cycle is a publish
problem, not a derivation problem — see "Outbox is not draining" below. The command reads
only; it derives and publishes nothing.

### 2. Why is the stream quiet?

```sh
./manage.py scout_stats
```

```
Active Scout candidates: 45

Per-filter pass counts:
  neo_score            23/45   (NEO score >= 98)
  impact_rating         0/45   (Impact rating >= 3)
  vmag                 18/45   (V > 21.6 (N) / 21.8 (S))
  ...
Passing all filters: 0
Near misses (fail exactly one): 2
  ST26H52: fails impact_rating
  ZTF10FL: fails impact_rating
```

Zero events is usually **correct**, not broken. `impact_rating >= 3` is the binding
constraint and typically sits at 0/45 — a genuine impact-rated object is rare. The
near-miss list tells you how close the population is.

### 3. Did the message actually reach the broker?

```sh
hop subscribe -s EARLIEST -j kafka://kafka.scimma.org/Scout.test-tim
```

This is ground truth, independent of our database. `-s EARLIEST` replays retained
messages; drop it to tail only new ones. Add `-t` to also show Hermes-style test messages,
which are otherwise filtered out.

A dry run of what *would* be derived, without writing or publishing anything:

```sh
./manage.py publish_scout_events --dry-run [--relaxed-filters]
```

## Symptoms

### Outbox is not draining (`pending` > 0 across cycles)

The events are derived and safe — the transactional outbox means nothing is lost — but
nothing is reaching Rubin. `--status` prints how long the oldest one has been stuck:

```
Oldest pending: ZTF10Fc new_candidate, stuck for 3h07m (derived 2026-08-26 19:50:38Z)
```

Check, in order:

1. **Credentials present?** `hop auth list` should show a credential for `kafka.scimma.org`.
   Absent → `hop auth add <credential>.csv` (get one from <https://my.hop.scimma.org/hopauth/>).
2. **ACL correct?** `hop list-topics kafka://kafka.scimma.org/` lists what the credential can
   see. If the topic is missing, the credential lacks permission on it — add Read/Write to
   the credential in hopauth.
3. **Broker reachable?** The publish step prints `Could not connect to <topic>: <error>` and
   leaves the rows for retry.

Retry is automatic: the next cycle re-attempts every unpublished row, and the unique key
`(tdes, last_run, event_type)` makes redelivery idempotent.

### `column "shared_by" of relation "tom_targets_basetarget" does not exist`

Schema drift after a `tom_base`/`tom_targets` bump — migrations shipped with the dependency
were never applied to this database.

```sh
./manage.py showmigrations | grep '\[ \]'
./manage.py migrate --noinput
```

The container `CMD` runs `migrate` at the start of every cycle, so this bites local
development far more than deployment.

### Nothing shows up in Hermes

Expected, and not a bridge fault. Hermes only displays messages from topics its own
ingester consumes into Hermes' database:

```sh
curl -s https://hermes.lco.global/api/v0/topics/    # 204 topics; self-serve ones are absent
```

A topic created in hopauth is not registered with Hermes, so submitting *to* it works
(Hermes writes to Kafka fine) while browsing it shows nothing. Use `hop subscribe` to
verify. Getting Hermes visibility needs the Hermes team to add the topic to its ingest
config *and* to grant Hermes' Hopskotch credential read permission on it.

### Objects flapping between `new_candidate` and `cancelled` (fixed)

Historic — `derive_event` now tracks candidate-set membership per `filter_mode`, so relaxed
and strict runs keep separate lineages. Described here because **outbox history predating
that fix still contains the artefact**:

```
ST26H93  new_candidate  2026-08-25 14:12  relaxed_test
ST26H93  cancelled      2026-08-25 14:12  strict      <- same last_run
```

Membership used to be read from the object's last event regardless of `filter_mode`, so a
relaxed run admitted an object the strict criteria reject, the next strict run read that as
passing -> failing and emitted `cancelled`, and the next relaxed run re-announced it. The
idempotency key `(tdes, last_run, event_type)` does not collide across differing event
types, so both landed at the same `last_run`. Alternating the modes oscillated forever.

Rows from that period are not a trustworthy record of what Scout did. Filter on
`provenance.filter_mode` when reading history — `--status` shows it per event.

### Scout returns nothing, or a partial list

Guarded — `updatescout` skips reconciliation rather than retiring the whole candidate pool:

```
Scout returned no candidates at all; skipping reconciliation rather than retiring every target.
Scout reported 45 candidate(s) but returned 12; skipping reconciliation rather than acting on a partial list.
```

Both are warnings, not errors, and the cycle continues. No `left_neocp` storm fires.

### The stream goes quiet after a JPL API change

**Known gap.** `ScoutDataService.expected_signature` is `{'source': 'NASA/JPL Scout API',
'version': '1.3'}`. On a mismatch, `query_service()` only calls `logger.warning` and leaves
`query_results` untouched (`tom_jpl/jpl.py:123-126`) — it does **not** raise. The bridge then
quietly ingests nothing and publishes nothing, and the reconciliation guard above (correctly)
declines to retire anything.

So signature drift looks exactly like "a quiet week on the NEOCP". Until a dead-man heartbeat
exists (feasibility study §8, not yet built), the only detection is the log line:

```
Signature of response from Scout API does not match expected signature. Expected {...}, got {...}.
```

Note this contradicts §8 of the feasibility study, which describes a hard stop on signature
mismatch. The guard is softer than documented.

## Reference

| Variable | Purpose |
|---|---|
| `SCOUT_TOPIC_URL` | Kafka topic URL. Defaults to `kafka://kafka.scimma.org/Scout.scout-test`; production sets `Scout.scout-prod` |
| `SCIMMA_USERNAME` / `SCIMMA_PASSWORD` | Hopskotch credential; else the `hop auth` store is used |
| `SCOUT_NO_AUTH=1` | Skip SASL entirely — local plaintext broker only, **never** against kafka.scimma.org |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Postgres; unset falls back to local SQLite |

A full poll cycle, in the order the container runs it:

```sh
./manage.py migrate --noinput
QUERY_ID=$(./manage.py bootstrap_scout_query --print-id)
./manage.py rundataquery "$QUERY_ID"        # ingest current candidates
./manage.py updatescout --skip-designations # retire departures (one Scout request)
./manage.py publish_scout_events            # derive + publish
```

`./manage.py updatescout --skip-reconcile` is the separate, less frequent MPC designation
pass (see README).
