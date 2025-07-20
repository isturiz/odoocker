#!/bin/bash
set -e

# Ensure common paths
export PATH="/usr/bin:/usr/local/bin:$PATH"

# Compose ADDONS_PATH dynamically from its components
ROOT_PATH="/home/odoo/src"
# COMMUNITY_ADDONS="${ROOT_PATH}/odoo/addons"
# COMMUNITY_ADDONS2="${ROOT_PATH}/odoo/odoo/addons"
# ENTERPRISE_ADDONS="${ROOT_PATH}/enterprise"
# THIRD_PARTY_ADDONS="${ROOT_PATH}/third-party-addons"
# EXTRA_ADDONS="${ROOT_PATH}/extra-addons"
CUSTOM_ADDONS="${ROOT_PATH}/custom-addons"
# If you want to prioritize custom, move it to the front
# ADDONS_PATH="${CUSTOM_ADDONS},${COMMUNITY_ADDONS},${COMMUNITY_ADDONS2},${ENTERPRISE_ADDONS},${EXTRA_ADDONS},${THIRD_PARTY_ADDONS},/usr/lib/python3/dist-packages/odoo/addons"
# ADDONS_PATH="${CUSTOM_ADDONS},/usr/lib/python3/dist-packages/odoo/addons"
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

echo "Entrypoint running as $(whoami)"
echo "Odoo version:"
which odoo
odoo --version

echo "CMD: $*"
exec "$@"

# #!/bin/bash
# set -e
# export LANG=C.UTF-8
#
# # Logging helpers
# log_info()   { echo -e "\033[1;32mINFO:\033[0m $*" >&2; }
# log_warn()   { echo -e "\033[1;33mWARN:\033[0m $*" >&2; }
# log_error()  { echo -e "\033[1;31mERROR:\033[0m $*" >&2; }
#
# # ---- 1. Esperar por Postgres si se solicita ----
# db_is_listening() {
#     psql --list > /dev/null 2>&1 || (sleep 1 && db_is_listening)
# }
# pg_user_exist() {
#     psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='${PGUSER:-odoo}'" | grep -q 1 || (sleep 1 && pg_user_exist)
# }
# if [[ "${WAIT_PG,,}" == "true" ]]; then
#     log_info "Waiting until the database server is listening..."
#     db_is_listening
#     log_info "Waiting until the pg user ${PGUSER:-odoo} is created..."
#     pg_user_exist
# fi
#
# # ---- 2. Instalar requirements python custom ----
# if [[ -n "$CUSTOM_REQUIREMENTS" ]]; then
#     log_info "Installing custom pip requirements..."
#     TMP_REQS=$(mktemp)
#     echo "$CUSTOM_REQUIREMENTS" > "$TMP_REQS"
#     pip install --user --no-cache-dir -r "$TMP_REQS"
#     rm -f "$TMP_REQS"
# fi
#
# # ---- 3. Crear configuración custom (fragmento) ----
# if [[ -n "$CUSTOM_CONFIG" ]]; then
#     log_info "Injecting custom Odoo configuration..."
#     mkdir -p /home/odoo/conf.d
#     echo "$CUSTOM_CONFIG" > /home/odoo/conf.d/999-entrypoint.conf
# fi
#
# # ---- 4. Detectar y configurar rutas de addons ----
# add_addons_path_conf() {
#     local sources="${SOURCES:-/home/odoo/src}"
#     local custom="${sources}/custom"
#     local enterprise_path="${sources}/enterprise"
#     local addons=""
#     local custom_enabled="${CUSTOM_ENABLED:-true}"
#     local ignore_repo="${IGNORE_REPO:-}"
#
#     # Enterprise va primero si existe
#     [[ -d "$enterprise_path" ]] && addons="$enterprise_path"
#
#     # Custom repos
#     if [[ "$custom_enabled" == "true" && -d "$custom" ]]; then
#         for d in "$custom"/*; do
#             b=$(basename "$d")
#             [[ -d "$d" && ! ",$ignore_repo," =~ ",$b," ]] && addons="$addons,$d"
#             [[ -f "$d/requirements.txt" ]] && pip install --user --no-cache-dir -r "$d/requirements.txt" || true
#         done
#     fi
#
#     # Guardar en conf.d
#     mkdir -p /home/odoo/conf.d
#     echo "[options]
# addons_path = ${addons#,}" > /home/odoo/conf.d/10-addons.conf
# }
# add_addons_path_conf
#
# # ---- 5. Instalar extensión unaccent si se solicita ----
# if [[ "${UNACCENT,,}" == "true" && -n "$PGDATABASE" ]]; then
#     log_info "Trying to install unaccent extension on $PGDATABASE"
#     psql -d "$PGDATABASE" -c 'CREATE EXTENSION IF NOT EXISTS unaccent;' || log_warn "Could not create unaccent extension (maybe DB not ready yet)"
# fi
#
# # ---- 6. Fix databases si se solicita ----
# if [[ "${FIXDBS,,}" == "true" && -n "$PGDATABASE" ]]; then
#     log_info "Trying to fix databases (click-odoo-update)"
#     click-odoo-update --if-exists --watcher-max-seconds "${CLICK_ODOO_UPDATE_WATCHER_MAX_SECONDS:-30}" || log_warn "click-odoo-update failed"
# fi
#
# # ---- 7. Generar archivo final de configuración ----
# generate_odoo_conf() {
#     local target_file="${ODOO_RC:-/home/odoo/odoo.conf}"
#     local conf_dir="/home/odoo/conf.d"
#     local tmp_conf=$(mktemp)
#     log_info "Merging configuration fragments from $conf_dir into $target_file"
#     cat $conf_dir/*.conf > "$tmp_conf"
#
#     # Expande variables de entorno
#     envsubst < "$tmp_conf" > "$target_file"
#     rm -f "$tmp_conf"
# }
# generate_odoo_conf
#
# # ---- 8. Ejecutar comando: Odoo o custom ----
# log_info "Running command: $*"
# if [[ "$1" == "--" ]]; then
#     shift
#     exec odoo "$@"
# elif [[ "$1" == "-"* ]]; then
#     exec odoo "$@"
# else
#     exec "$@"
# fi
