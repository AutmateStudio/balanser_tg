FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY discovery_api /app/discovery_api

RUN mkdir -p /app/discovery_api/data

EXPOSE 8000

CMD ["uvicorn", "discovery_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
