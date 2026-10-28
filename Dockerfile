# Shiplane: the browser UI and API to deploy sites and manage a Linux server. Python standard library only; the image adds just the OpenSSH client.
# Build:  docker build -t shiplane .      Run: see docker-compose.yml and the "Run with Docker" part of the Manual.
# Stage 1: minify the browser code (esbuild, no bundling, so the module layout is unchanged).
FROM node:22-slim AS js
WORKDIR /w
COPY web ./web
RUN cd web/js && npx --yes esbuild@0.21.5 $(find . -name '*.js') --minify --outdir=/w/min --outbase=. --log-level=warning && cp -r /w/min/. . 

# Stage 2: ship Python as bytecode only (no readable .py). The remote/ scripts are sent to servers as text, so they stay as they are.
FROM python:3.12-slim AS py
WORKDIR /b
COPY app.py ./
COPY coderunner ./coderunner
RUN python -m compileall -b -q app.py coderunner  && find . -name '*.py' ! -path './coderunner/remote/*' -delete  && find . -name __pycache__ -prune -exec rm -rf {} +

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --home-dir /data --shell /usr/sbin/nologin shiplane \
 && mkdir -p /data /app && chown shiplane:shiplane /data

WORKDIR /app
COPY LICENSE README.md ./
COPY --from=py /b ./
COPY web ./web
COPY --from=js /w/web/js ./web/js
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

# Everything that is yours (settings, SSH key, activity log, saved connections) lives in the /data volume; the image itself stays read-only.
ENV HOME=/data CODERUNNER_DATA=/data/state CODERUNNER_CONFIG=/data/config PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
VOLUME /data
USER shiplane
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8765), 3)"]
ENTRYPOINT ["entrypoint.sh"]
CMD ["python", "app.pyc", "--no-browser", "--bind", "0.0.0.0"]
