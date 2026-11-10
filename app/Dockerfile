FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=3787
ENV DETOUR_DATA_DIR=/data

WORKDIR /app
COPY requirements-mcp.txt /app/requirements-mcp.txt
RUN pip install --no-cache-dir -r /app/requirements-mcp.txt
COPY server.py /app/server.py
COPY mcp_server.py /app/mcp_server.py
COPY run.py /app/run.py
COPY portal.css /app/portal.css

RUN addgroup --system detour && adduser --system --ingroup detour detour \
  && mkdir -p /data \
  && chown -R detour:detour /app /data

USER detour

EXPOSE 3787 3788
CMD ["python", "/app/run.py"]
