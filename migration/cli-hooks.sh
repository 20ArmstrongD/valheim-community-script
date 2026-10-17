#!/usr/bin/env bash
# Optional add-on: sourced only after explicit migration-tool installation.
if [[ "${1:-}" == migrate ]]; then
  exec /usr/bin/python3 /usr/local/libexec/valheim_migrate.py "${@:2}"
fi
if [[ "${1:-}" == update && -f /etc/valheim/mod-lock.json ]]; then
  valheim_error "Migrated game build is pinned. Review mod compatibility and docs/migration.md before updating; automatic updates are disabled."
fi
