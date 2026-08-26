FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY bridge ./bridge
COPY scout_publisher ./scout_publisher
COPY manage.py ./

# git is only needed while the temporary tom-jpl git dependency exists (PR TOMToolkit/tom_jpl#23)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir . \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Default: the frequent poll cycle -- ingest new Scout candidates, retire the ones that have
# left, then derive and publish events.
#
# rundataquery takes a numeric query id, which is assigned per-database, so it is resolved
# from the query *name* at runtime via bootstrap_scout_query --print-id rather than
# hardcoded here.
#
# Reconciliation (retiring candidates that have left Scout, so left_neocp events fire) is
# part of this cycle. updatescout fetches the whole Scout roster in a single unconstrained
# call and diffs it against the active candidates, so it costs one request however many we
# track. It runs before publish_scout_events so that a departure and its left_neocp event
# land in the same pass.
#
# The MPC designation pass is excluded here: it scrapes the Previous NEOCP Objects page and
# can make throttled per-object fallback lookups, which is too much to repeat every 10
# minutes. Deploy it as a second, less frequent CronJob overriding this command, e.g. daily:
#   python manage.py updatescout --skip-reconcile
CMD ["sh", "-c", "python manage.py migrate --noinput && QUERY_ID=$(python manage.py bootstrap_scout_query --print-id) && python manage.py rundataquery \"$QUERY_ID\" && python manage.py updatescout --skip-designations && python manage.py publish_scout_events"]
