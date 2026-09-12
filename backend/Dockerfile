FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Exact, locked dependencies first so this layer is cached across code changes.
# psycopg[binary] ships its own libpq, so no system packages are needed.
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps .

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
