FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
       postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 research \
    && mkdir -p /app/data/runs /app/data/evaluations \
    && chown -R research:research /app

USER research

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD ["python", "-m", "app.health", "live"]

CMD ["python", "-m", "app.ops", "--help"]
