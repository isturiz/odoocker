#!/bin/bash
# Backup script for Odoo database
# Usage: ./backup_database.sh [database_name]
# Example: ./backup_database.sh rea

set -e

DB_NAME="${1:-rea}"
BACKUP_DIR="/workspace/backups"
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_backup_$(date +%Y%m%d_%H%M%S).sql"

# Create backups directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "🔄 Creating backup for database: $DB_NAME"
echo "📁 Backup location: $BACKUP_FILE"

# Perform the backup
PGPASSWORD=odoo pg_dump -h pgdb -U odoo -d "$DB_NAME" > "$BACKUP_FILE"

# Get file size
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)

echo "✅ Backup completed successfully"
echo "📊 File size: $SIZE"
echo "💾 Full path: $(ls -lh "$BACKUP_FILE" | awk '{print $NF, "(" $5 ")"}')"
