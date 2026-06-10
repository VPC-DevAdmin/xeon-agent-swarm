FROM python:3.11-slim

WORKDIR /app

# Build context is the repo root (see docker-compose.yml) so we can
# preserve the `backend/` directory as an importable package.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend

# Config is mounted at /app/config via docker-compose volume
ENV CONFIG_DIR=/app/config

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
