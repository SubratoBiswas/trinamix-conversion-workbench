# Stage 1 — Oracle Instant Client via official Oracle Linux 8 repos
# oraclelinux:8-slim is public on Docker Hub; oracle-instantclient21-basiclite
# is in Oracle's pre-configured yum repos inside that image.
FROM oraclelinux:8-slim AS oracle-ic
RUN microdnf install -y oracle-instantclient21-basiclite && microdnf clean all

# Stage 2 — Application
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy Oracle Instant Client libraries from Stage 1
COPY --from=oracle-ic /usr/lib/oracle /usr/lib/oracle

# Install system deps.
# libaio1t64 satisfies Oracle IC's libaio.so.1 requirement on Debian Trixie.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev libaio1t64 \
    && echo "/usr/lib/oracle/21/client64/lib" > /etc/ld.so.conf.d/oracle-instantclient.conf \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

ENV LD_LIBRARY_PATH=/usr/lib/oracle/21/client64/lib:${LD_LIBRARY_PATH}

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

RUN mkdir -p /app/uploads /app/outputs

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
