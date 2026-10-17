# Optional migration add-on

Read [the migration and recovery guide](../docs/migration.md) before installing
or running these tools. No migration is enabled by a normal fresh install.

- `install.sh`: explicit add-on installation inside the destination LXC.
- `valheim_migrate.py`: standalone source snapshot and destination import/rollback.
- `cli-hooks.sh`: optional `valheim migrate` dispatch and migrated-build update guard.
- `mod-plan.example.json`: deliberately unapproved template; replace every
  placeholder and establish compatibility before enabling packages.

No production migration, live package installation or plugin execution has been
performed during development. A filename, PE/CLI header or package hash is not a
proof of plugin compatibility or safety.
