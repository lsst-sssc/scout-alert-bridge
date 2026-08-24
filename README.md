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

### Reconciliation (separate, less frequent)

Retiring candidates that have left Scout — which is what makes `left_neocp` events fire —
is `tom_jpl`'s `updatescout`, deliberately **not** part of the 10-minute cycle:

```sh
./manage.py updatescout --skip-designations   # hourly: retire departed candidates
./manage.py updatescout --skip-reconcile      # daily: promote new IAU designations from the MPC
```

`updatescout --skip-designations` re-queries Scout **once per active candidate** (rather
than diffing against one broad query) so that a candidate which merely stops matching a
narrow query's cuts is never mistaken for one that has actually left. That is the correct
semantics, but at ~50–100 active candidates it is ~50–100 Scout requests per run — far too
many to run every 10 minutes against an API whose fair-use policy is one request at a time.
An object leaving the NEOCP tolerates up to an hour's delay far better than a new candidate
appearing does, so the two run on separate schedules.

## Configuration (environment)

| Variable | Purpose | Default |
|---|---|---|
| `SCOUT_TOPIC_URL` | Kafka topic URL | `kafka://kafka.scimma.org/lco.scout-neo-too-test` |
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

> **Note:** `tom_jpl`'s Scout support is still on the PR #23 branch
> (`add-rubin-too-filter-fields`), where the old combined `ingest_scout` command was split
> in review into `rundataquery` (ingest) + `updatescout` (reconcile/designations). Any
> older notes referring to `ingest_scout --query-name ...` predate that split.

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
