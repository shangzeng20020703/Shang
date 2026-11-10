FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /app/data /app/uploads && useradd --uid 10001 --create-home service && chown -R service:service /app
USER service
# API is reachable only through the private Compose network; the edge strips client forwarding headers.
CMD ["sh","-c","python -m app.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --forwarded-allow-ips='*'"]
