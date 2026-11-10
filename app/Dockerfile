FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=3787
ENV DETOUR_DATA_DIR=/data

# Apply Debian security fixes published after the pinned base image was built.
RUN apt-get update \
  && apt-get upgrade -y --no-install-recommends \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt
COPY server.py /app/server.py
COPY mcp_server.py /app/mcp_server.py
COPY run.py /app/run.py
COPY portal.css /app/portal.css
COPY brand /app/brand

RUN addgroup --system --gid 10001 detour \
  && adduser --system --uid 10001 --ingroup detour detour \
  && mkdir -p /data \
  && chown 10001:10001 /data

USER 10001:10001

EXPOSE 3787 3788
CMD ["python", "/app/run.py"]
