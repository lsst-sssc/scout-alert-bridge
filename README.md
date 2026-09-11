# scout-alert-bridge

Polls the [JPL Scout](https://cneos.jpl.nasa.gov/scout/) NEOCP hazard-assessment system
and publishes new / updated / cancelled **Rubin ToO candidate** NEO events as a Kafka
stream (SCiMMA Hopskotch) for the Vera C. Rubin Observatory Target-of-Opportunity system.

A headless [TOM Toolkit](https://tom-toolkit.readthedocs.io/) Django project:
[`tom_jpl`](https://github.com/TOMToolkit/tom_jpl) provides Scout ingestion and change
reconciliation; the `scout_publisher` app applies the SSSC NEOs WG "Filter Criteria for
NEO Rubin ToO Triggers" (v0.2) and publishes passing candidates through a transactional
outbox. Design and feasibility study:
[lsst-sssc/fomo `docs/scout_kafka_bridge_feasibility.md`](https://github.com/lsst-sssc/fomo/pull/44).

## One poll cycle

```sh
./manage.py migrate
./manage.py bootstrap_scout_query          # idempotent: saved broad Scout query + service user
QUERY_ID=$(./manage.py bootstrap_scout_query --print-id)
./manage.py rundataquery "$QUERY_ID"       # ingest the current Scout candidate list
./manage.py publish_scout_events           # --dry-run to preview, --no-publish for outbox only
```

Run every 10 minutes (deployed as a Kubernetes CronJob with `concurrencyPolicy: Forbid`).

`rundataquery` (from `tom_dataservices`) identifies a saved query by **numeric id, not
name**. That id is assigned per-database, so it differs between dev, staging and prod and
must not be baked into an image or manifest — hence resolving it at runtime from the
query name with `bootstrap_scout_query --print-id`, which writes the bare id to stdout and
its progress messages to stderr. Interactively, `./manage.py listqueries` prints a table of
saved queries and their ids.

### Reconciliation and designations

Retiring candidates that have left Scout — which is what makes `left_neocp` events fire —
is `tom_jpl`'s `updatescout`. It runs *inside* the 10-minute cycle, between `rundataquery`
and `publish_scout_events`, so a departure and its event land in the same pass:

```sh
./manage.py updatescout --skip-designations   # each cycle: retire departed candidates
./manage.py updatescout --skip-reconcile      # daily: promote new IAU designations from the MPC
```

Reconciliation costs a **single** Scout request however many candidates we track. Scout
applies no cuts of its own — the score thresholds are ours, applied client-side — so one
unconstrained call returns the entire roster, and absence from it unambiguously means the
object has left. (An earlier version re-queried Scout once per active candidate, ~50–100
requests per run, on the assumption that a query's cuts were applied server-side. They are
not; see TOMToolkit/tom_jpl#23.)

The MPC designation pass is what stays on its own schedule. It scrapes the Previous NEOCP
Objects page and can make throttled per-object fallback lookups, and a new IAU designation
tolerates a day's delay far better than a new candidate appearing does.

## Checking it works

```sh
./manage.py publish_scout_events --status   # outbox: pending vs published, recent events
./manage.py scout_stats                     # per-filter pass counts over the live population
hop subscribe -s EARLIEST -j $SCOUT_TOPIC_URL   # ground truth, straight off the broker
```

`docs/Runbook.md` covers these plus the known failure modes — a stuck outbox, migration
drift, why Hermes shows nothing, and the relaxed/strict filter-mode flap.

## Configuration (environment)

| Variable | Purpose | Default |
|---|---|---|
| `SCOUT_TOPIC_URL` | Kafka topic URL | `kafka://kafka.scimma.org/Scout.scout-test` (production sets `Scout.scout-prod`) |
| `SCIMMA_USERNAME` / `SCIMMA_PASSWORD` | Hopskotch credential (else `hop auth` config) | — |
| `DB_HOST` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_PORT` | Postgres (unset → local SQLite) | — |
| `SCOUT_QUERY_NAME` | Saved broad query name | `scout-bridge-broad` |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Django basics | dev defaults |

## Events

`new_candidate`, `updated`, `cancelled` (a filter stopped passing), `left_neocp`
(designated / removed / impacted). Kafka key = NEOCP temporary designation (`tdes`);
idempotency key = `(tdes, last_run, event_type)`. Objects that never pass the filters
are tracked but never published.

## Development

```sh
uv venv && uv pip install -e . --group dev
./manage.py test          # SQLite by default
ruff check .
```

Against Postgres (as deployed):

```sh
docker compose up -d db
export DB_HOST=localhost DB_PORT=5433 DB_PASSWORD=scout_bridge
./manage.py migrate && ./manage.py bootstrap_scout_query && ./manage.py test
```

> **Note:** the Scout support this relies on shipped in `tom-jpl` 0.3.0 (PyPI, 2026-09-10).
> During review of TOMToolkit/tom_jpl#23 the old combined `ingest_scout` command was split
> into `rundataquery` (ingest) + `updatescout` (reconcile/designations); any older notes
> referring to `ingest_scout --query-name ...` predate that split.

A full containerized poll cycle (build image, migrate, ingest, publish):

```sh
docker compose run --rm bridge
```

### Testing the publish path without real SCiMMA credentials

`hop-client` (and thus `Stream(auth=False)`) works against any Kafka-protocol broker, not
just Hopskotch — so the actual publish code path (topic-URL parsing, producer, ack/retry)
can be exercised against a local single-node broker:

```sh
docker compose --profile localkafka up -d kafka
# hop's producer expects the topic to already exist (no reliance on broker auto-create):
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create --topic scout-test --partitions 1 --replication-factor 1

export SCOUT_TOPIC_URL=kafka://localhost:9094/scout-test SCOUT_NO_AUTH=1
./manage.py publish_scout_events

# verify receipt:
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic scout-test --from-beginning
```

`SCOUT_NO_AUTH=1` makes `_open_stream()` use `Stream(auth=False)` instead of loading (or
requiring) a SCiMMA credential — local-only, never set it against the real Hopskotch broker.
This validates everything except SASL auth and the real Hopskotch ACLs/retention, which need
real credentials (M2).
