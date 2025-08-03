#!/bin/bash
set -e

# Ensure common paths
export PATH="/usr/bin:/usr/local/bin:$PATH"

# Compose ADDONS_PATH dynamically from its components
ROOT_PATH="/home/odoo/src"
CUSTOM_ADDONS="${ROOT_PATH}/custom-addons"
ADDONS_PATH="${CUSTOM_ADDONS}"
export ADDONS_PATH

echo "ADDONS_PATH resolved as: $ADDONS_PATH"

# Expand environment variables in odoo.conf using envsubst
envsubst < /home/odoo/odooo/odoo.conf > /home/odoo/odooo/odoo.conf.expanded
mv /home/odoo/odooo/odoo.conf.expanded /home/odoo/odooo/odoo.conf

# Print the used config for debugging
echo "---- odoo.conf used ----"
cat /home/odoo/odooo/odoo.conf
echo "------------------------"

# Install requirements.txt from all addons (recursive in /home/odoo/src)
echo "Scanning addons for requirements.txt..."
find /home/odoo/src -type f -name "requirements.txt" | while read -r reqfile; do
    echo "Installing Python requirements from $reqfile"
    pip3 install --no-cache-dir -r "$reqfile"
done

echo "Entrypoint running as $(whoami)"
echo "Odoo version:"
which odoo
odoo --version

echo "CMD: $*"
exec "$@"
