FROM python:3.12-slim

WORKDIR /app

RUN adduser --disabled-password --gecos "" vantage

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/
COPY static/ ./static/

RUN mkdir -p data && chown -R vantage:vantage /app

USER vantage

EXPOSE 50224

ENV VANTAGE_TELEMETRY_PORT=50224
ENV GATEWAY_HOST=0.0.0.0

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:50224/health', timeout=4)"

CMD ["python", "-m", "app.main"]
