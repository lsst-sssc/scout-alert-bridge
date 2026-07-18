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

# One poll cycle: reconcile Scout state, then derive + publish candidate events.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py bootstrap_scout_query && python manage.py ingest_scout --query-name ${SCOUT_QUERY_NAME:-scout-bridge-broad} && python manage.py publish_scout_events"]
