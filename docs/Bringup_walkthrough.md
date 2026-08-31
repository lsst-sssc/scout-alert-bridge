● Here's a walkthrough, in order, with what "good" looks like at each step. Run these from
  ~/git/scout-alert-bridge.

  0. cd ~/git/scout-alert-bridge/ and activate the .venv
  
  1. Start the local broker + database
  docker compose --profile localkafka up -d kafka
  docker compose up -d db
  - Check: docker compose ps shows both containers running/healthy.
  - If kafka doesn't come up, check logs: docker compose logs kafka | tail -20 — look for Kafka Server started.

  2. Create the test topic (hop's producer needs it to already exist — it won't rely on broker auto-create)
  docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server localhost:9092 --create --topic scout-test \
    --partitions 1 --replication-factor 1
  - Check: prints Created topic scout-test. Jump to bottom (ctrl+End) ↓  run this before — fine either way).
  - List topics anytime with: docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server 
  localhost:9092 --list 
 
  3. Point the bridge at the local broker instead of Hopskotch
  export DB_HOST=localhost DB_PORT=5433 DB_PASSWORD=scout_bridge
  export SCOUT_TOPIC_URL=kafka://localhost:9094/scout-test
  export SCOUT_NO_AUTH=1
  python ./manage.py migrate --noinput
  - SCOUT_NO_AUTH=1 is the important bit — it skips the SCiMMA credential lookup entirely. Never set this against 
  the real kafka.scimma.org broker.
  - Check: migrate reports no errors (may say "no migrations to apply" if already done).
 
  4. Run a real poll cycle
  python ./manage.py bootstrap_scout_query
  QUERY_ID=$(python ./manage.py bootstrap_scout_query --print-id)
  python ./manage.py rundataquery "$QUERY_ID"
  python ./manage.py publish_scout_events

  (Updated 2026-08-24: tom_jpl PR #23 review split the old combined `ingest_scout` command
  into tom_dataservices' `rundataquery` — which identifies a saved query by numeric id, not
  name — plus tom_jpl's new `updatescout` for reconciliation. `bootstrap_scout_query
  --print-id` resolves the name to that per-database id. `python ./manage.py listqueries`
  shows the same ids as a table interactively.

  Note (revised 2026-08-26): this cycle *does* retire candidates that have left Scout, via
  `python ./manage.py updatescout --skip-designations` between `rundataquery` and
  `publish_scout_events`. That costs one Scout request per run however many candidates are
  tracked — an earlier version issued one per candidate. Run it by hand here if you want to
  exercise the `left_neocp` path.)
  - Check the output of publish_scout_events: N new event(s) written to the outbox. followed by either Published 
  N/N event(s) to kafka://localhost:9094/scout-test. or Nothing to publish. (expected right now — live Scout data
  currently has 0/30 objects passing all filters, so an empty run is normal, not a bug).
  - To force something through without waiting for a real candidate, inject a fake outbox row and republish:
  python ./manage.py shell -c "
  from scout_publisher.models import PublishedEvent
  PublishedEvent.objects.get_or_create(
      tdes='LOCALTEST01', last_run=None, event_type='new_candidate',
      defaults={'payload': {'schema_version': '1.0', 'event_type': 'new_candidate', 'tdes': 'LOCALTEST01'}})
  "
  python ./manage.py publish_scout_events
  - Check: this time it should say Published 1/1 event(s)....
 
  5. Read the message back off the topic
  docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
    --bootstrap-server localhost:9092 --topic scout-test --from-beginning
  - Check: you see the JSON payload printed (e.g. {"tdes": "LOCALTEST01", ...}). Ctrl-C to stop — this consumer
  reads forever by default. 
 
  6. Clean up
  python ./manage.py shell -c "from scout_publisher.models import PublishedEvent; 
  PublishedEvent.objects.filter(tdes='LOCALTEST01').delete()"
  docker compose --profile localkafka down
  docker compose down

  What this does and doesn't prove:
  - ✅ Producer wiring, topic-URL parsing, payload serialization, ack/retry logic, the outbox-to-Kafka handoff —
  all genuine hop-client code paths.
  - ❌ SASL authentication, Hopskotch's actual ACLs/topic permissions, retention behavior — those only get
  exercised once you have real SCiMMA credentials (M2), by dropping SCOUT_NO_AUTH and setting
  SCIMMA_USERNAME/SCIMMA_PASSWORD (or running hop auth add) against SCOUT_TOPIC_URL=kafka://kafka.scimma.org/....

❯ Quick Q: what's the difference between `docker ps` and `docker compose ps`?

● - docker ps — lists running containers across your whole Docker daemon, regardless of which project/compose file
  started them. Shows raw container names (e.g. scout-alert-bridge-kafka-1), no grouping.
  - docker compose ps — scoped to the compose project in your current directory (from compose.yaml). Only shows
  containers belonging to that project, and adds compose-specific columns like SERVICE (the name from the yaml,
  e.g. kafka) and which PROFILES are active. It's basically docker ps pre-filtered and annotated for "the stuff
  this compose file manages."
 
  Practical difference for what you're doing: docker compose ps from ~/git/scout-alert-bridge will only show db
  and kafka (and bridge once you run it); docker ps would show those plus anything else running on your machine.

❯ These commands should be run in the local bash shell, not inside the containers db and kafka correct ?          
  Presumably there needs to be another (orchestrator?) container for these sort of commands or is the a startup   
  container thing in the Dockerfile / docker compose or later Helm/ArgoCD deployment

● Good question — worth untangling. Three different execution contexts, same code:

  1. What you're doing right now (fastest iteration): db and kafka are containers, but python manage.py ... runs
  directly in your host venv, talking to them over the forwarded ports (localhost:5433, localhost:9094). No "app
  container" involved at all — this is the quickest loop for testing logic changes.

  2. Full container parity (what I ran for you last time, and what CI effectively mirrors): there's a third
  service already defined in compose.yaml — bridge (profiles: ['cycle']). It builds the actual Dockerfile and runs
  the same command sequence as one shot, inside the container, on the Docker network (so it talks to db/kafka by
  service name, not localhost):

  docker compose --profile localkafka run --rm \
    -e SCOUT_TOPIC_URL=kafka://kafka:9092/scout-test -e SCOUT_NO_AUTH=1 \
    bridge
    
  That's the "orchestrator container" you're thinking of — not a persistent daemon, just a container that runs
  migrate && bootstrap_scout_query && ingest_scout && publish_scout_events (see the CMD in Dockerfile) and exits.
  No cron/loop inside it.

  3. Production (M4, not built yet): no docker compose at all. The same image gets deployed to LCO's Kubernetes
  cluster as a CronJob (schedule: "*/10 * * * *", concurrencyPolicy: Forbid) that runs that identical container
  command every 10 minutes against the real shared LCO Postgres and the real Hopskotch broker, then exits until
  the next tick. ArgoCD doesn't execute anything itself — it's a GitOps sync controller that just keeps the
  CronJob/Secret/etc. manifests in the cluster matching what's committed in the (not-yet-created) 
  LCOGT/scout-alert-bridge-deploy repo. No Helm in LCO's convention — kpt + kustomize per §5 of the plan.

  So: host-venv (fastest, what you're doing) → docker compose run bridge (container parity, closest local proxy to
  prod) → k8s CronJob (actual prod, Track D/M4, still gated on LCO infra answers).

