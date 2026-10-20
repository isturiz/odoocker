FROM ghcr.io/astral-sh/uv:0.11.20 AS uv
FROM python:3.12.13-slim-bookworm

ARG POSTGRES_VERSION=16

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:/home/odoo/.opencode/bin:$PATH \
    PYTHONPATH=/opt/odoocker-engine

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && install -d /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/keyrings/postgresql.asc \
    && printf '%s\n' 'deb [signed-by=/usr/share/keyrings/postgresql.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential git openssh-client curl ca-certificates \
    libldap2-dev libsasl2-dev libpq-dev libxml2-dev libxslt1-dev \
    libjpeg-dev zlib1g-dev libffi-dev libssl-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    fonts-dejavu-core postgresql-client-${POSTGRES_VERSION} \
    && rm -rf /var/lib/apt/lists/*
# Odoo needs upstream's patched Qt for report headers/footers; Debian's build lacks it.
# Bookworm packages from wkhtmltopdf/packaging release 0.12.6.1-3, pinned per architecture.
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) sha256="98ba0d157b50d36f23bd0dedf4c0aa28c7b0c50fcdcdc54aa5b6bbba81a3941d" ;; \
        arm64) sha256="b6606157b27c13e044d0abbe670301f88de4e1782afca4f9c06a5817f3e03a9c" ;; \
        *) echo "Unsupported wkhtmltopdf architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 \
        "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_${arch}.deb" \
        -o /tmp/wkhtmltox.deb; \
    printf '%s  %s\n' "$sha256" /tmp/wkhtmltox.deb | sha256sum -c -; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/wkhtmltox.deb; \
    test "$(wkhtmltopdf --version)" = "wkhtmltopdf 0.12.6.1 (with patched qt)"; \
    rm -f /tmp/wkhtmltox.deb; \
    rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
RUN groupadd -g 1000 odoo && useradd -m -u 1000 -g 1000 -s /bin/bash odoo \
    && mkdir -p /opt/venv /var/lib/odoocker \
    /home/odoo/.local/share/Odoo /home/odoo/.config/opencode \
    /home/odoo/.local/share/opencode /home/odoo/.local/state/opencode \
    && chown -R odoo:odoo /opt/venv /var/lib/odoocker /home/odoo
USER odoo
ARG OPENCODE_VERSION
RUN test -n "$OPENCODE_VERSION" \
    && curl -fsSL https://opencode.ai/v2/install -o /tmp/opencode-install.sh \
    && bash /tmp/opencode-install.sh --no-modify-path --version "$OPENCODE_VERSION" \
    && rm /tmp/opencode-install.sh
USER root
COPY odoocker /opt/odoocker-engine/odoocker
RUN printf '#!/usr/local/bin/python3\nfrom odoocker.cli import main\nraise SystemExit(main())\n' \
    > /usr/local/bin/odx && chmod 755 /usr/local/bin/odx
USER odoo
WORKDIR /workspace
