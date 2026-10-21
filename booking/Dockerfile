# The public backend of the Serenity demo: website, calendar and signed voice tools on one https origin (the host adds the https).
# Build from this folder (booking/):   docker build -t serenity-demo .
# It holds NO secret: the Retell key and the tool token are host secrets read from the environment at start (see DEPLOY.md).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY aria_booking ./aria_booking
COPY voice_agent_draft ./voice_agent_draft

# The appointment file belongs on the host's persistent volume, mounted at /data. One machine only: it is a single SQLite file.
ENV PORT=8080 ARIA_STORE_PATH=/data/appointments.sqlite3
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"

CMD ["python", "-m", "aria_booking", "demo-public-serve"]
