# Stage 1: build the portal UI (React + Carbon) into static files.
FROM node:26-bookworm-slim@sha256:add5c17c14752c54d2f3d519ec46a3815684d41c33f198bcb09fb998f7dcbf68 AS web

# Carbon's packages run an IBM telemetry script on install. Install scripts stay off.
ENV IBM_TELEMETRY_DISABLED=true
WORKDIR /web
COPY web/package.json web/package-lock.json web/.npmrc /web/
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/index.html web/vite.config.js /web/
COPY web/src /web/src
RUN npm run build

# Stage 2: the runtime. Python only; Node never ships in the image.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=3787
ENV DETOUR_DATA_DIR=/data
ENV DETOUR_WEB_DIR=/app/web/dist

# Apply Debian security fixes published after the pinned base image was built.
RUN apt-get update \
  && apt-get upgrade -y --no-install-recommends \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt
COPY store.py server.py mcp_server.py run.py /app/
COPY brand /app/brand
COPY --from=web /web/dist /app/web/dist

RUN addgroup --system --gid 10001 detour \
  && adduser --system --uid 10001 --ingroup detour detour \
  && mkdir -p /data \
  && chown 10001:10001 /data

USER 10001:10001

EXPOSE 3787 3788
CMD ["python", "/app/run.py"]
