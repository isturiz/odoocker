FROM ubuntu:jammy

ENV LANG=C.UTF-8
USER root

ENV DEBIAN_FRONTEND=noninteractive

# Create odoo user and filestore
RUN useradd -md /home/odoo -s /bin/false odoo && \
    mkdir -p /var/lib/odoo /home/odoo/data && \
    chown -R odoo:odoo /var/lib/odoo /home/odoo/data && \
    sync

# Install base system dependencies
RUN set -e && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git watchdog curl apt-transport-https build-essential ca-certificates \
    ffmpeg file flake8 fonts-freefont-ttf fonts-noto-cjk gawk gnupg gsfonts \
    libldap2-dev libjpeg9-dev libsasl2-dev libxslt1-dev lsb-release ocrmypdf \
    sed sudo unzip xfonts-75dpi zip zlib1g-dev \
    gettext \
    && rm -rf /var/lib/apt/lists/*

# Install Python 3 dependencies and Odoo requirements
RUN set -e && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-dbfread python3-dev python3-opencv python3-gevent \
    python3-pip python3-setuptools python3-wheel python3-markdown python3-mock \
    python3-phonenumbers python3-websocket python3-google-auth libpq-dev \
    python3-asn1crypto python3-jwt publicsuffix python3-xmlsec python3-aiosmtpd \
    pylint && \
    rm -rf /var/lib/apt/lists/*


RUN if [ "$(uname -m)" = "x86_64" ]; then \
        echo "Installing package for AMD64"; \
        curl -sSL https://nightly.odoo.com/deb/jammy/wkhtmltox_0.12.5-2.jammy_amd64.deb -o /tmp/wkhtml.deb; \
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y install --no-install-recommends --fix-missing -qq /tmp/wkhtml.deb; \
    elif [ "$(uname -m)" = "arm64" ]; then \
        echo "Installing package for ARM64"; \
        curl -sSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_arm64.deb -o /tmp/wkhtml.deb; \
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y install --no-install-recommends --fix-missing -qq /tmp/wkhtml.deb; \
    else \
        echo "Architecture not supported for wkhtmltox or unknown"; \
    fi; \
        rm -rf /var/lib/apt/lists/* /tmp/wkhtml.deb

# Install Node.js 20.x
RUN curl -s https://deb.nodesource.com/gpgkey/nodesource.gpg.key | gpg --dearmor | tee /usr/share/keyrings/nodesource.gpg >/dev/null && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x $(lsb_release -c -s) main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install global npm packages for Odoo frontend development
RUN npm install --force -g rtlcss@3.4.0 es-check@6.0.0 eslint@8.1.0 prettier@2.7.1 eslint-config-prettier@8.5.0 eslint-plugin-prettier@4.2.1

# Install extra system requirements from Odoo's debian/control file
# ADD https://raw.githubusercontent.com/odoo/odoo/17.0/debian/control /tmp/control.txt
# RUN curl -sSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /etc/apt/trusted.gpg.d/psql_client.asc && \
#     echo "deb http://apt.postgresql.org/pub/repos/apt/ $(lsb_release -s -c)-pgdg main" > /etc/apt/sources.list.d/pgclient.list && \
#     apt-get update && \
#     sed -n '/^Depends:/,/^[A-Z]/p' /tmp/control.txt | \
#     awk '/^ [a-z]/ { gsub(/,/,""); gsub(" ", ""); print $NF }' | sort -u | \
#     DEBIAN_FRONTEND=noninteractive xargs apt-get install -y -qq --no-install-recommends && \
#     apt-get clean && \
#     rm -rf /var/lib/apt/lists/*

# Install Odoo 17.0 from official nightly build

# Define Odoo version as a build argument
ARG ODOO_VERSION=17.0
ENV ODOO_VERSION=${ODOO_VERSION}

# Download and install Odoo using the version argument
RUN set -e && \
    curl -o odoo.deb -sSL "http://nightly.odoo.com/${ODOO_VERSION}/nightly/deb/odoo_${ODOO_VERSION}.latest_all.deb" && \
    apt-get update && \
    apt-get -y install --no-install-recommends ./odoo.deb && \
    rm -rf /var/lib/apt/lists/* odoo.deb


# Install Google Chrome (amd64) or Chromium (arm64) for PDF export
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then \
        echo "Installing Google Chrome for AMD64"; \
        curl -sSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb && \
        apt-get update && \
        apt-get -y install --no-install-recommends /tmp/chrome.deb && \
        rm -rf /tmp/chrome.deb; \
    elif [ "$ARCH" = "arm64" ]; then \
        echo "Installing Chromium for ARM64"; \
        apt-get update && \
        apt-get -y install --no-install-recommends chromium-browser; \
    else \
        echo "Unsupported architecture: $ARCH"; \
        exit 1; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Install pip packages required for Odoo and custom development
RUN python3 -m pip install --no-cache-dir \
    ebaysdk==2.1.5 \
    pdf417gen==0.7.1 \
    ruff==0.4.7 \
    pandas

# Install Odoo official requirements
ADD --chown=odoo https://raw.githubusercontent.com/odoo/odoo/17.0/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

# Install core Python dependencies (single RUN for layer optimization)
# RUN python3 -m pip install --no-cache-dir \
#     watchdog \
#     xmltodict \
#     pandas \
#     bravado_core \
#     swagger_spec_validator \
#     jsonschema==3.2.0 \
#     pdf2image \
#     git+https://github.com/binaural-dev/python-sdk.git \
#     cryptography==35.0.0 \
#     fsspec \
#     Unidecode \
#     pymupdf \
#     schwifty \
#     sortedcontainers \
#     microsoftgraph-python \
#     requests

# Environment variables for standard directories
ENV SOURCES=/home/odoo/src \
    CONFIG_DIR=/home/odoo/.config \
    DATA_DIR=/home/odoo/data \
    ODOO_RC=/home/odoo/odooo/odoo.conf

# Default environment variables for scripts and Odoo
ENV ODOO_SERVER=odoo \
    UNACCENT=True \
    PROXY_MODE=True \
    WITHOUT_DEMO=True \
    WAIT_PG=true \
    PGUSER=odoo \
    PGPASSWORD=odoo \
    PGHOST=postgres \
    PGPORT=5432 \
    ADMIN_PASSWORD=admin

# Useful aliases for odoo-shell
# RUN echo "alias odoo-shell='odoo shell --shell-interface ipython --no-http --limit-memory-hard=0 --limit-memory-soft=0'" >> /home/odoo/.bashrc

# Install pip dependencies
COPY odooo/requirements.txt /home/odoo/odooo/requirements.txt
RUN pip3 install --no-cache-dir -r /home/odoo/odooo/requirements.txt

COPY odooo/odoo.conf /home/odoo/odooo/odoo.conf
RUN chown odoo:odoo /home/odoo/odooo/odoo.conf
RUN chown -R odoo:odoo /home/odoo/odooo

# Copy static entrypoint and config
COPY odooo/entrypoint.sh /home/odoo/odooo/entrypoint.sh
RUN chmod +x /home/odoo/odooo/entrypoint.sh

# Expose Odoo service ports
EXPOSE 8069 8071 8072


# Entrypoint and default command
WORKDIR "/home/odoo"
USER odoo

ENTRYPOINT ["/home/odoo/odooo/entrypoint.sh"]
CMD ["odoo"]


