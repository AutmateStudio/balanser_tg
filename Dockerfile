FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
COPY standalone_discovery/requirements.txt standalone_discovery/
RUN pip install --no-cache-dir -r requirements-dev.txt \
    && pip install --no-cache-dir -r standalone_discovery/requirements.txt

COPY . .

# SFTP с Windows часто заливает CRLF — bash в Linux-контейнере ломается без этого.
RUN find scripts -type f \( -name '*.sh' -o -name 'preflight_test_db.py' -o -path 'scripts/e2e_d12/*.py' \) \
      -exec sed -i 's/\r$//' {} + \
    && chmod +x scripts/*.sh

ENV PYTHONPATH=/app:/app/standalone_discovery
ENV PYTHONUNBUFFERED=1
