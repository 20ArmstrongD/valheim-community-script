# Backup, restoration and recovery

For full destination rollback after an optional VM import, including the prior
game/mod tree and launcher, use the [migration recovery procedure](migration.md#recovery-and-rollback).
The archive commands below protect worlds/configuration, not executable mod trees.

All commands here run as root **inside the newly created Valheim LXC**. Never run
these procedures against an existing production VM as part of testing.

## What is protected

| Layer | Location | Purpose |
|---|---|---|
| Valheim built-in world backups | `/var/lib/valheim/saves/` | Game-managed world versions (`-backups`, `-backupshort`, `-backuplong`) |
| Management archives | `/var/backups/valheim/` | Consistent, stopped-server copies of all saves and the current configuration |
| Proxmox guest backup | Your configured Proxmox backup storage | Entire container, subject to mount-point backup settings |

The active saves directory includes `worlds_local/`, permission lists and other
game-managed state. Management archives contain `etc/valheim/server.conf` and
`var/lib/valheim/saves/` relative to the archive root. Steam's binaries and its
client cache are excluded; reinstall them from Steam instead of backing them up.

## Create and verify an archive

```bash
valheim backup
```

The command records whether the service is running, creates a persistent
maintenance inhibition file, sends SIGINT via systemd, and confirms the process
has exited. It then creates a gzip tar archive, verifies tar readability, and
writes a SHA-256 sidecar. It resumes a previously running server. A server that
was stopped stays stopped. Expect a brief interruption to players.

Archives are named `valheim-YYYYMMDDTHHMMSSZ-NNNNNN.tar.gz` in UTC. The sequence
avoids collisions and preserves ordering for multiple backups in one second.
Both archive and checksum are mode `0600`, within a root-owned `0700` directory.
Each archive includes the server password; treat it as a credential.

Copy the exact archive path printed by `valheim backup` and its `.sha256` sidecar
to backup storage outside the LXC. Verify the copied archive on that storage:

```bash
cd /path/to/backup-copy
sha256sum --check valheim-TIMESTAMP-SEQUENCE.tar.gz.sha256
tar --list --gzip --file valheim-TIMESTAMP-SEQUENCE.tar.gz
```

Replace the example filename with the actual generated name. SHA-256 detects
accidental damage; it does not authenticate an archive if an attacker can replace
both files. Keep copies on independently protected storage.

`backup_retention` limits the number of completed local archive/checksum pairs
after a **successful** operation. Failed updates/restores do not prune backups.
Ordering uses UTC filename timestamps; clock changes can affect which archive is
oldest. A pre-restore backup counts toward the same retention. Verify and copy
important restore points elsewhere before repeated maintenance. No backup is
automatically copied off-container. Disk exhaustion aborts maintenance.

For scheduled independent archives, create a local systemd timer that runs
`/usr/local/bin/valheim backup` during your chosen maintenance window. This is
separate from the optional update timer; no additional scheduling system is
required. Every filesystem backup briefly stops the game to obtain consistency.

## Restore worlds into this container

1. Select a trusted archive created by this utility. For an off-container copy,
   put both files back in `/var/backups/valheim/` under their original filenames,
   owned by root, mode `0600`. Never restore an untrusted archive.
2. Ensure the update timer cannot race with your inspection:

   ```bash
   systemctl stop valheim-update.timer
   ```

3. Run the command below with the archive's full path:

   ```bash
   valheim restore /var/backups/valheim/valheim-TIMESTAMP-SEQUENCE.tar.gz
   ```

   The filename above is a placeholder; pass the real generated filename.
   The command checks the checksum **before stopping the server**, takes a
   pre-restore backup, extracts only saves as the unprivileged `valheim` account,
   refuses links and special files, then replaces the saves directory. Root never
   extracts archive entries. Restoration preserves the current configuration.
   A running server resumes; a previously stopped server remains stopped.
4. Ensure `world_name` matches the restored `.db`/`.fwl` basename. Changing the
   value requires `valheim check-config` and `valheim restart`. Do not rename one
   file in a pair independently.
5. Inspect `valheim logs`, join the disposable world and verify expected content.
   Re-enable the timer with `systemctl start valheim-update.timer` if desired.

Local tests exercise real tar creation/extraction and compare binary world files
before and after restoration. They do not prove that a real Valheim build loads
those worlds; that remains a fresh-LXC acceptance test.

## Restore after container loss

Build a **fresh** LXC with the same script and a disposable world. Configure its
settings, stop its update timer, copy a trusted archive/checksum into the protected
backup directory, and follow the restore procedure above. Select the correct
`world_name` afterward. The current public stable server is installed: old world
formats may require game migration, and an upgraded world may not load with an
older binary. Retain off-container restore points.

To recover an old configuration, extract the one named member as data into a
root-only candidate (not into a shell and not directly over the live file):

```bash
umask 0077
tar --extract --gzip --to-stdout --file /var/backups/valheim/ARCHIVE.tar.gz \
  etc/valheim/server.conf >/root/valheim-server.conf.candidate
```

Inspect privately, then edit the current configuration with the desired values.
Apply `chown root:valheim /etc/valheim/server.conf`, `chmod 0640` and
`valheim check-config`. Never `source` either configuration file. Restoring worlds
does not silently restore an old password, visibility or automation setting.

## Interrupted maintenance

A failed shutdown, update or restore leaves `/etc/valheim/maintenance` in place,
preventing automatic startup even across reboots. `valheim stop`, `status` and
`logs` remain usable. Do not remove the marker while maintenance is still running.
Check `systemctl status valheim-update.service`, running SteamCMD processes, and
the operation's diagnostic output first.

1. Stop the update timer and run `valheim stop`. If SIGINT did not stop the game,
   inspect `systemctl status valheim.service` and its `MainPID`; do not force-kill
   it merely to get a backup. Resolve the hang before proceeding.
2. Inspect the newest archive and checksum. Failed SteamCMD output is retained as
   a root-only `.steamcmd.*` file in the backup directory. Correct network/disk
   problems before retrying. Do not share logs without review.
3. If restoration failed, the printed `.restore.*` staging directory is preserved.
   If it contains `previous-saves` and the normal saves directory is missing,
   move `previous-saves` back to `/var/lib/valheim/saves` before doing anything
   else. Keep the pre-restore archive. Do not recursively chown/extract unknown
   archive contents as root.
4. Confirm the server process and SteamCMD are stopped. Remove **only** the
   inhibition marker, then retry the appropriate operation:

   ```bash
   rm /etc/valheim/maintenance
   valheim update
   ```

   `valheim update` preserves the stopped state, takes another backup and
   validates/redownloads binaries. For a world rollback, use `valheim restore`
   with a known-good archive instead. Run `valheim start` only after recovery is
   complete, then inspect readiness in the logs.

A failed backup before data modification removes its partial file and resumes a
previously running game; it does not run SteamCMD. A failed update never starts
possibly mixed binaries. `valheim restart` is a graceful restart, not a backup.
The generated helpers/units are installed by guest setup; framework/game updates
do not replace your configuration. If guest setup was interrupted, Core's
`dev_mode=keep` option can retain the failed test container for inspection and
exporting data. Correct the cause, build another fresh LXC through the normal
workflow, then recover a trusted archive there. Do not guess an in-place framework
retry command or invoke the guest installer manually. Do not use trace mode with
real credentials.

## Inspecting a reference deployment

No migration is automatic. If a future migration is separately authorized, these
read-only commands identify the actual service and save path on that deployment:

```bash
systemctl cat valheim.service
systemctl show valheim.service -p User -p ExecStart -p WorkingDirectory
```

Inspect the startup script referenced by `ExecStart` privately for `-savedir` and
`-world`; it may contain the password. Never paste its full contents into an issue.
Do not assume historical save paths or world names are still active. A consistent
copy of a live reference world needs a separately approved backup/shutdown plan.
