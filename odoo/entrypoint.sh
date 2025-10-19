#!/bin/bash
set -e

# Ensure common paths
export PATH="/usr/bin:/usr/local/bin:$PATH"

# Root where we mount our extra addons
EXTRA_ADDONS_ROOT="/mnt/extra-addons"

# Odoo core addons path from Debian package
ODOO_CORE_ADDONS="/usr/lib/python3/dist-packages/odoo/addons"

# Build ADDONS_PATH including only real addon dirs (folders containing modules).
ADDONS_PATHS=()

# 1) Optionally include root if it contains modules directly
if find "${EXTRA_ADDONS_ROOT}" -mindepth 1 -maxdepth 1 -type d -exec test -f "{}/__manifest__.py" \; -print -quit | grep -q .; then
  ADDONS_PATHS+=("${EXTRA_ADDONS_ROOT}")
fi

# 2) Include first-level subdirs that contain at least one module (folder with __manifest__.py)
while IFS= read -r -d '' dir; do
  if find "${dir}" -mindepth 1 -maxdepth 1 -type d -exec test -f "{}/__manifest__.py" \; -print -quit | grep -q .; then
    ADDONS_PATHS+=("${dir}")
  fi
done < <(find "${EXTRA_ADDONS_ROOT}" -mindepth 1 -maxdepth 1 -type d -print0)

# 3) Always include Odoo core addons
ADDONS_PATHS+=("${ODOO_CORE_ADDONS}")

# Join with commas
ADDONS_PATH="$(IFS=, ; echo "${ADDONS_PATHS[*]}")"
export ADDONS_PATH

echo "ADDONS_PATH resolved as: ${ADDONS_PATH}"

# Expand environment variables in odoo.conf using envsubst
envsubst < /etc/odoo/odoo.conf > /etc/odoo/odoo.conf.expanded
mv /etc/odoo/odoo.conf.expanded /etc/odoo/odoo.conf

# Print the used config for debugging
echo "---- odoo.conf used ----"
cat /etc/odoo/odoo.conf
echo "------------------------"

# Install Python requirements declared by addons
echo "Scanning addons for requirements.txt..."
find /mnt/extra-addons -type f -name "requirements.txt" | while read -r reqfile; do
    echo "Installing Python requirements from $reqfile"
    pip3 install --no-cache-dir -r "$reqfile"
done

echo "Entrypoint running as $(whoami)"
echo "Odoo version:"
which odoo
odoo --version

echo "CMD: $*"
exec "$@"
