FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn chromadb

COPY server.py .

EXPOSE 9002

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9002"]
