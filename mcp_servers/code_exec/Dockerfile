FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY server.py .

EXPOSE 9003

# Run as non-root for additional sandbox isolation
RUN useradd -m sandbox
USER sandbox

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9003"]
