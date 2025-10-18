#!/bin/bash
set -e

# Ensure common paths
export PATH="/usr/bin:/usr/local/bin:$PATH"

# Standard addons path like upstream
ADDONS_PATH="/mnt/extra-addons"
export ADDONS_PATH

echo "ADDONS_PATH resolved as: $ADDONS_PATH"

# Expand environment variables in odoo.conf using envsubst
envsubst < /etc/odoo/odoo.conf > /etc/odoo/odoo.conf.expanded
mv /etc/odoo/odoo.conf.expanded /etc/odoo/odoo.conf

# Print the used config for debugging
echo "---- odoo.conf used ----"
cat /etc/odoo/odoo.conf
echo "------------------------"

# Install requirements from addons (now scanning /mnt/extra-addons)
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
