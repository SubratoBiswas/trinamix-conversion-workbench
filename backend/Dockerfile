FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Java runtime (needed by JPype1 / jaydebeapi to load the JDBC driver)
# ojdbc11.jar downloaded from Maven Central — public, no auth required
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless curl build-essential libffi-dev \
    && curl -fSL \
       "https://repo1.maven.org/maven2/com/oracle/database/jdbc/ojdbc11/21.13.0.0/ojdbc11-21.13.0.0.jar" \
       -o /app/ojdbc11.jar \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

RUN mkdir -p /app/uploads /app/outputs

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
