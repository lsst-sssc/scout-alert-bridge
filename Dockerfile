FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY bridge ./bridge
COPY scout_publisher ./scout_publisher
COPY manage.py ./

RUN pip install --no-cache-dir .

# One poll cycle: reconcile Scout state, then derive + publish candidate events.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py bootstrap_scout_query && python manage.py ingest_scout --query-name ${SCOUT_QUERY_NAME:-scout-bridge-broad} && python manage.py publish_scout_events"]
