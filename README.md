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
./manage.py ingest_scout --query-name scout-bridge-broad
./manage.py publish_scout_events           # --dry-run to preview, --no-publish for outbox only
```

Run every 10 minutes (deployed as a Kubernetes CronJob with `concurrencyPolicy: Forbid`).

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

A full containerized poll cycle (build image, migrate, ingest, publish):

```sh
docker compose run --rm bridge
```
