# Preparing the bridge for LCO ArgoCD deployment (M4 groundwork)

State as of 2026-09-11, ahead of talking to LCO DevOps about the deploy repo. What LCO's
GitOps pattern actually expects from an application repository, where this repo currently
diverges from it, and what is worth doing here *before* that conversation so the questions
we bring are the right ones.

Sources: `LCOGT/deploy-repo-copier-template` (`copier.yaml`), `LCOGT/mop` + `LCOGT/mop-deploy`
(a TOM Toolkit project on ArgoCD with eight CronJobs — the closest comparator),
`LCOGT/reusable-workflows/.github/workflows/continuous-deployment.yaml`, and
`LCOGT/kpt-pkg-catalog`. NEOexchange is *not* a reference: it has not been migrated to ArgoCD
(Jenkinsfile, crontab baked into the image).

## 1. How LCO's pattern works

It is not "the deploy repo references the app by image URL", which is how the feasibility
study (§8) describes it. The application repo carries more than the Dockerfile:

| In the **app repo** | Purpose |
|---|---|
| `k8s/base/kustomization.yaml` | Base Kustomization: one kpt package per workload, plus a `configMapGenerator` named `env` for non-secret environment |
| `k8s/base/cronjob-<name>/` | Each CronJob is a copy of `LCOGT/kpt-pkg-catalog/cronjob` (`kpt pkg get`), customised in `cronjob.yaml` |
| `skaffold.yaml` + `flake.nix` | `skaffold build` (custom `skaffold-builder-buildx` from LCO's devenv) builds the image; tag policy is `gitCommit` variant `Tags`, i.e. `git describe` (e.g. `6.0.2-6-gf381b37`) |
| `.github/workflows/cd.yaml` | Calls `LCOGT/reusable-workflows/.github/workflows/continuous-deployment.yaml@main` with `skaffoldBuildModules` and `secrets: inherit` |

The reusable workflow then pushes to `ghcr.io/<owner>/<repo>/<artifact>` and, authenticating
as the **lco-deploy-bot GitHub App**, opens a PR in `<repo>-deploy` that updates:

- `staging/cd-set-images/kustomization.yaml` — a kustomize `Component` pinning `newName`,
  `newTag` and `digest` for the image ("edited by a bot, do not edit");
- `staging/base/` — a kpt copy of the app repo's `k8s/base` (`Kptfile` upstream ref = the app
  commit), updated with `kpt pkg update`.

Staging PRs auto-merge; `v*` release tags produce a draft PR for prod. ArgoCD watches the
*rendered* `output/<env>/manifest.yaml`, kept current by a pre-commit hook in the deploy repo's
nix devenv, not the Kustomization itself.

Deploy-repo conventions seen in `mop-deploy` that constrain us:

- CronJob pods run with `runAsNonRoot: true`, `runAsUser/Group: 1000`,
  `readOnlyRootFilesystem: true`, capabilities `drop: [ALL]`, and a memory-backed `emptyDir`
  mounted at `/tmp`. `XDG_HOME=/tmp` is set in the `env` ConfigMap.
- Configuration is `envFrom` a ConfigMap `env` and a Secret `env`; the DB password is spliced
  in as `DB_PASS` via a kustomize replacement. Names: `DB_HOST`, `DB_NAME`, `DB_USER`,
  `DB_PORT`, `DB_PASS`.
- Postgres is the `cnpg-postgres` kpt package (CloudNativePG) in the app namespace
  (`DB_HOST=db-rw.staging-mop.svc`), not an external managed instance.
- CronJob spec fields in use: `concurrencyPolicy: Forbid`, `startingDeadlineSeconds`,
  `backoffLimit`, `activeDeadlineSeconds`, `restartPolicy: Never`, a `set-lco-karpenter-attrs`
  annotation selecting the node pool.
- Secrets are sealed-secrets (`sealedsecrets` kpt package, per-environment).

## 2. Where this repo diverges today

### 2.1 The ghcr image is private

`ci.yaml`'s `publish-image` job succeeds, but an anonymous manifest pull of
`ghcr.io/lsst-sssc/scout-alert-bridge:latest` returns 401/403 — new ghcr packages default to
private. The §8 caveat ("the ghcr package must be public, or the cluster needs an
`imagePullSecret`") is therefore live. LCO's `image-pull-secrets-ghcr-lcogt` kpt package
carries credentials for the *LCOGT* org and would not help.

**Fix**: make the package public in the `lsst-sssc` org package settings (needs a package
admin). Adding the `org.opencontainers.image.source` label (see §3.4) links the package to
the repo so its visibility and permissions can be managed alongside it.

### 2.2 Cross-org: the bridge cannot call LCO's reusable CD workflow

The `update` job of `continuous-deployment.yaml` needs `LCO_DEPLOY_BOT_APP_ID` /
`LCO_DEPLOY_BOT_APP_PRIVATE_KEY`, which are LCOGT organisation secrets. `secrets: inherit`
from a repo under `lsst-sssc` will not see them, and the App is installed on the LCOGT org.
So the automatic "build → bump the deploy repo" loop does not work for us as-is.

Options to put to DevOps (their call which they'd support):

1. **Mirror or move the repo into `LCOGT/`** and use the pattern unchanged. Simplest for
   them; the code stays SSSC-authored but the canonical repo (and ghcr path) becomes LCO's.
2. **Deploy repo owns the manifests** (`upstream_type: other` in the copier template): we
   keep building the image in `lsst-sssc` CI, and image bumps in the deploy repo are manual
   or done by a small workflow of our own. No lco-deploy-bot involvement.
3. **Keep `k8s/base` in this repo, pulled by kpt from a cross-org URL** (`upstream_type:
   kustomize`, `kustomize_base_repo=https://github.com/lsst-sssc/scout-alert-bridge`). The
   copier template allows this; only the bot-driven *update* step is missing, and `kpt pkg
   update` can be run by hand in the deploy repo.

Whichever is chosen, a `k8s/base/` in this repo is cheap, matches their layout, and is
usable under 1 and 3 directly (and as copy-paste under 2).

### 2.3 The image does not declare a non-root user

`Dockerfile` has no `USER`; the container runs as root by default. Verified 2026-09-11 that
the image *does* work under LCO's pod security context — run as `--user 1000:1000
--read-only --tmpfs /tmp` against the local Postgres, `manage.py check`, `from hop import
Stream`, and `publish_scout_events --status` all succeed. So this is a declaration problem,
not a functional one: add `USER 1000:1000` so the image is non-root without relying on the
manifest, and so `runAsNonRoot: true` passes admission cleanly.

Consequence to handle: under that security context `HOME` is `/` (no home directory exists),
so the `Stream()` fallback to `~/.config/hop/auth.toml` in
`scout_publisher/management/commands/publish_scout_events.py` (`_open_stream`) can never
succeed in-cluster. Missing `SCIMMA_USERNAME`/`SCIMMA_PASSWORD` should fail fast with a
clear message rather than surface as an obscure hop-client error deep in the cycle.

### 2.4 The poll cycle is a `sh -c` chain baked into `CMD`

LCO manifests run explicit commands (`command: [python, manage.py, <cmd>, ...]`), and the
Dockerfile comment already anticipates the daily job overriding `CMD`. Two problems with the
chain as deployed:

- It is a second copy of the sequence in `scripts/run_cycle.sh`; they will drift.
- `rundataquery` (tom_dataservices) catches its own failures and exits 0, as
  `run_cycle.sh` notes. On the host that is tolerable because we watch log freshness. In
  Kubernetes the Job exit code **is** the primary failure signal — kube-state-metrics'
  `kube_job_status_failed` / `kube_cronjob_status_last_successful_time` are what
  Prometheus/Alertmanager would alert on — so a cycle that silently ingested nothing looks
  healthy.

**Fix**: a `scout_cycle` management command that runs migrate → `bootstrap_scout_query` →
`rundataquery` → `updatescout --skip-designations` → `publish_scout_events` in-process
(`call_command`), and checks that ingestion actually produced fresh data (newest `last_run`
ingested this cycle, or the Scout roster was non-empty) before exiting 0. The two manifests
then become `command: [python, manage.py, scout_cycle]` and
`command: [python, manage.py, updatescout, --skip-reconcile]`; `run_cycle.sh` collapses to
calling the same command. Running `migrate` every cycle is fine for a CronJob-only service
(`concurrencyPolicy: Forbid` means no two cycles race).

### 2.5 Hygiene

- **No `.dockerignore`.** `COPY` is selective so the image is correct, but the build context
  ships `.venv/`, `db.sqlite3`, `logs/`, `.git/` and `__pycache__` on every build.
- **Tags and labels.** CI tags `:latest` and `:<sha>`. LCO's `cd-set-images` pins
  `newTag` + `digest`, with tags from `git describe`. Use `docker/metadata-action` to get
  `org.opencontainers.image.source`/`.revision`/`.version` labels (the `source` label is what
  links the ghcr package to the repo) and semver tags on `v*` releases.
- **Build provenance in events.** `bridge/settings.py` hardcodes `BRIDGE_VERSION = '0.1.0'`,
  which is what goes into `provenance`. Passing the git SHA / describe string as a build arg
  and reading it from the environment would make each published event traceable to an exact
  image.
- **DB env naming.** We use `DB_PASSWORD`; LCO's replacement injects `DB_PASS`. Either
  accept both in `settings.py` or set `DB_PASSWORD` explicitly in the manifest. Trivial,
  but decide once.
- **Log volume.** A cycle emits ~125 lines every 10 minutes, mostly one
  "The target, X, already exists" line per tracked candidate from `rundataquery`. Under
  cluster log aggregation that is noise; check whether `--verbosity 0` (or a quieter
  tom_dataservices path) is available before the one-week soak.

### 2.6 No web surface in the cluster

The Django admin is described in `bridge/settings.py` as the operational inspection
surface, but the deployment is CronJobs only — there is no Deployment, Service or Ingress,
so the admin is unreachable. Either add a `deploy-server` (gunicorn) + `svc-server` +
`ingress-lco-global-private` set as MOP has, with the auth that implies, or accept that
inspection is `kubectl exec`/`kubectl create job --from=cronjob/...` running
`publish_scout_events --status` and `scout_stats`. The Runbook's health checks already work
that way.

## 3. To do in this repo before the DevOps conversation

Ordered; the first three are the ones that change what we ask for.

1. **Make the ghcr package public** (§2.1) — or note that we need an `imagePullSecret`.
2. **`Dockerfile`: `USER 1000:1000`** (§2.3), and make `_open_stream` require the env
   credentials when neither `SCIMMA_*` nor `SCOUT_NO_AUTH` is set.
3. **`scout_cycle` management command** (§2.4) with a real exit code; `CMD` and
   `run_cycle.sh` call it.
4. **`k8s/base/`** (§1): `cronjob-cycle/` (`*/10 * * * *`, `activeDeadlineSeconds` well
   under 600) and `cronjob-designations/` (daily), each from `kpt pkg get
   https://github.com/LCOGT/kpt-pkg-catalog/cronjob`, with the LCO security context and
   `/tmp` volume copied from `mop`; a `kustomization.yaml` with the `env` ConfigMap
   (`PYTHONUNBUFFERED=1`, `XDG_HOME=/tmp`, `SCOUT_TOPIC_URL` left to the overlay).
5. **`.dockerignore`**, `docker/metadata-action` labels/tags, build-arg version stamp (§2.5).

## 4. Questions for DevOps (supersedes feasibility §11.4)

1. Cross-org: which of §2.2's options do they want — mirror into `LCOGT`, deploy-repo-owned
   manifests, or cross-org kpt upstream with manual `kpt pkg update`?
2. Cluster and namespace; whether `cnpg-postgres` in-namespace is the expected database (it
   is what `mop-deploy` does) and who owns backups for it.
3. Which node pool / `set-lco-karpenter-attrs` provisioner for a ~1 GB, sub-10-minute job.
4. What they alert on for CronJobs today (Job failures via kube-state-metrics? a dead-man
   check on last-successful-time?) so §2.4's exit-code semantics match it.
5. Sealed-secret workflow for the SCiMMA credential: who holds the production
   `Scout.scout-prod` write credential and seals it.
6. Whether an admin web surface (§2.6) is wanted, which decides if a Deployment + private
   ingress is in scope.
