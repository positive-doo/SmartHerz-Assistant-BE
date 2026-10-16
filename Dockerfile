FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       curl \
       gnupg \
       ca-certificates \
       fonts-dejavu-core \
       libreoffice-writer \
       unixodbc \
       unixodbc-dev \
  && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
  && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
  && apt-get update \
  && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Enable process-local unixODBC pooling while preserving the installed driver.
RUN python -c 'import configparser; from pathlib import Path; \
    path = Path("/etc/odbcinst.ini"); \
    config = configparser.ConfigParser(interpolation=None); config.optionxform = str; config.read(path); \
    driver = "ODBC Driver 18 for SQL Server"; assert config.has_section(driver), f"Missing driver: {driver}"; \
    config.add_section("ODBC") if not config.has_section("ODBC") else None; \
    config["ODBC"]["Pooling"] = "Yes"; config[driver]["CPTimeout"] = "120"; \
    output = path.open("w"); config.write(output); output.close()'

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
  && pip install -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${CONTAINER_APP_PORT:-${PORT:-8000}}"]
