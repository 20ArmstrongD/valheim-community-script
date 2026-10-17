# Optional Linux VM-to-LXC migration

Migration is an explicit add-on. The ordinary fresh installer still starts a
vanilla server without Python or mod-loader dependencies. Nothing here connects
to an existing VM automatically, deletes it, or decommissions it. These tools
were developed using fixtures; **a real migration and real mod compatibility have
not been validated by the automated tests**. Rehearse with disposable copies first.

## Safety contract

- Source capture is a separate operation on an already-stopped Linux source.
  The tool never stops, starts, masks or edits the source service for you.
- Local and SSH imports consume the same consistent-snapshot format. SSH reads
  an existing archive with strict host-key checking and key-based authentication;
  it does not perform remote shutdowns, install software, or run imported scripts.
- Every world, world backup and access-list file under the chosen save root is
  preserved. The selected world must contain its `.db` and `.fwl` pair.
- Startup settings are parsed as data, never sourced or executed. The source
  game password is discarded. A terminal-only hidden prompt requests and confirms
  a replacement password; it is not accepted as a command-line option.
- Game binaries come from a **fresh anonymous SteamCMD installation** in staging.
  Source game executables, BepInEx core, patchers and native loaders are not copied
  into the executable tree.
- All imported plugin files and configuration files are retained in root-only
  quarantine. Only approved assemblies from fresh, pinned Thunderstore packages
  are enabled. Unknown plugins remain inactive. BepInEx's source loader config is
  retained in quarantine; fresh loader defaults are used instead of old paths or
  arbitrary preloader settings.
- Activation takes a normal destination `valheim backup`, stops it through the
  existing CLI, takes its management lock, inhibits starts, then replaces staged
  paths. The previous destination is retained for rollback. It never replaces an
  existing VM.
- Successful imports remain stopped unless `--start` was explicitly selected.
  Automatic updates are disabled. A migration lock blocks ordinary game updates
  until the owner reviews compatibility and chooses how to maintain the new build.

## 1. Install the optional tools on the destination LXC

Complete the ordinary fresh installation first. Inside the destination LXC,
obtain a reviewed checkout/copy of this repository, then run from its root:

```bash
bash migration/install.sh
valheim migrate --help
```

The add-on explicitly installs Python 3 and the OpenSSH client, copies the
standalone migration module/hooks, and enables the `valheim migrate` dispatch in
older copies of the management CLI. It does not migrate data or stop the game.
An existing CLI is saved as `/usr/local/libexec/valheim-before-migration` before
adding its hook. New fresh installers contain only an inactive hook check; they
do not download or enable the add-on. No framework container logic is duplicated.

Use the destination's existing `valheim` account and directories. Custom CLI or
launch wrappers that cannot be recognized are refused rather than overwritten
by guessing. Atomic activation requires game, saves, configuration and helper
paths on the same filesystem; game/save bind mounts are refused. Keep enough
free disk for the incoming archive, quarantine, fresh game and backups. The
current archive limit is 32 GiB / 100,000 files; larger migrations need a reviewed
operator-led procedure.

## 2. Inspect the source privately and schedule a snapshot

On the source, inspect the actual service and startup script. These can contain
the old password, so do not paste them into public issues. Choose the game root,
save root, startup script and service name explicitly; there are no personal
directory or world-name defaults in the migration code.

Common source layouts can be passed with `--game-dir`, `--saves-dir` and
`--startup-script`; the selected world comes from the parsed startup flags.
Alternative roots and additional retained worlds require no code change.

For a planned cutover, ask players to disconnect and deliberately stop the source
service during the agreed maintenance window. Stop any other writers or scripts
that can change these saves, and prevent operators/automation from restarting
the source during capture. Do not copy a running world's `.db` and `.fwl` pair.
Do not assume that a storage snapshot taken while the game is running is
application-consistent.

The capture module requires Python 3.8+ on the source. Copy the reviewed
`migration/valheim_migrate.py` there as a standalone file; do **not** install the
destination add-on on the source VM. As root on the source, after your approved
shutdown, capture to a protected path outside the save directory:

```bash
umask 077
systemctl cat valheim.service >/root/valheim-source.service
python3 /root/valheim_migrate.py snapshot \
  --game-dir /srv/valheim/server \
  --saves-dir /srv/valheim/saves \
  --startup-script /srv/valheim/start-valheim.sh \
  --unit-file /root/valheim-source.service \
  --service valheim.service \
  --output /root/valheim-snapshot.tar.gz
```

Replace the example paths with the **actual** paths you inspected. There is no
source shutdown command embedded in this example or the capture tool. The module
checks inactive/MainPID=0 and for remaining game processes, verifies file hashes
while copying and again across the full source inventory, and rechecks the
stopped service. It refuses observed concurrent writes and symlinks/special files.
The source must stay quiescent throughout capture; filesystem hashing does not
provide a kernel lock against an administrator deliberately racing the tool.

Retain the printed SHA-256 through a trusted channel. The `.sha256` sidecar holds
the same digest. Hashes establish transfer integrity, not authenticity against
someone able to replace both archive and digest. The destination trusts the
operator's snapshot attestation; arbitrary third-party tarballs are not imports.

Keep the source stopped for the **final** capture/cutover. If it resumes after
a rehearsal snapshot, take a new consistent snapshot before final transfer so
later player progress is not lost. Never run both servers against shared live
world files. Retain the original VM and its independent backup afterward.

### Complex startup scripts

The parser supports literal/quoted settings, simple assignments, known variable
references and one direct game invocation (or `start_game_bepinex.sh` followed
by the game executable). Source library/bootstrap paths are discarded. It carries
over name, world, visibility, crossplay, port, save intervals, built-in backup
settings, presets/modifiers and world keys, preserving modifier ordering.

It refuses shell control flow, command substitution, unresolved variables,
arbitrary wrapper scripts, additional unit arguments/hooks and environment files.
This is intentional: there is no safe generic way to execute an arbitrary source
script merely to discover its settings.

For such deployments, create a protected, reviewed JSON file with this shape and
add `--settings /root/reviewed-settings.json` to capture:

```json
{
  "config": {
    "server_name": "Community Server",
    "world_name": "Dedicated",
    "public": "1",
    "crossplay": "1",
    "port": "2456",
    "saveinterval": "1800",
    "world_backups": "6",
    "backupshort": "7200",
    "backuplong": "43200"
  },
  "arguments": ["-modifier", "portals", "casual"]
}
```

Use the actual settings, not the illustrative values. Do not put the old or new
password in this JSON. Explicitly review settings omitted by a wrapper. When
source flags are absent, the translator uses documented game defaults (including
four built-in backups), rather than assuming the fresh installer's defaults.

## 3. Dry-run an import

Copy the archive into a root-only destination directory, or read it over SSH.
Use the actual trusted 64-character digest in place of `EXPECTED_SHA256`:

```bash
valheim migrate import \
  --archive /root/valheim-snapshot.tar.gz \
  --sha256 EXPECTED_SHA256 \
  --dry-run
```

Or, from the destination LXC, using an SSH account allowed to read the protected
snapshot and a verified entry in `known_hosts`:

```bash
valheim migrate import \
  --ssh migration-user@source.example \
  --remote-archive /protected/valheim-snapshot.tar.gz \
  --ssh-port 22 \
  --sha256 EXPECTED_SHA256 \
  --dry-run
```

Establish host-key trust out of band and configure key/agent authentication
first. SSH passwords are not command options and host-key checks are not bypassed.
Remote archives are streamed read-only using the source's Python interpreter.

Dry-run verifies the complete archive/manifest, selected world pair and translated
settings, and reports plugin hashes, gameplay flags and configuration-file count.
It uses temporary extraction space but does not stop services, install packages,
write destination configuration or prompt for a password. It does **not** claim
mod compatibility or run game/loader/plugin code.

## 4. Review BepInEx and plugins

For a modded source, `--mod-plan` is required for an actual import. A plan must pin:

- The fresh Steam build ID you tested in a disposable destination, plus an
  explicit `compatibility_confirmed: true` decision.
- The Valheim-specific `denikson-BepInExPack_Valheim` 5.x package ID and ZIP SHA-256.
- Every enabled plugin/dependency package ID and ZIP SHA-256, with `reviewed: true`.
- Each enabled managed DLL's exact package path, fresh DLL SHA-256 and the source
  DLL SHA-256 it is replacing. Source hashes are reported by dry-run.

Start with [the deliberately unapproved plan template](../migration/mod-plan.example.json).
Its version examples were observed during research; they are **not** a promise
of compatibility, and placeholder hashes/review flags must be replaced after
review. Download packages from their publisher's Thunderstore page over HTTPS,
inspect their manifests, release notes and DLLs without loading them, and compute
hashes locally. Set the exact case-sensitive path as present in the ZIP, not a
guessed filename. Pin all transitive dependencies at the exact versions referenced
by their manifests; unresolved or conflicting dependencies fail closed.

The importer downloads fresh pinned ZIPs, verifies hashes/manifest identities and
dependencies, checks Linux x64 Doorstop and PE/CLI header structure, and enables
only listed assemblies. A matching hash and header do not prove absence of
malicious behavior or game compatibility. Approval means trusting those publishers
and testing that specific mod set against that Steam build. Do not set the flags
merely to silence an error. An unrecognized DLL remains preserved and inactive;
it is never copied blindly into the active plugin directory.

The [Valheim BepInEx pack](https://thunderstore.io/c/valheim/p/denikson/BepInExPack_Valheim/)
is a customized BepInEx distribution. The
[Server Devcommands publisher](https://github.com/JereKuusela/valheim-dev) describes
admin-client and optional server installation; test both sides and required player
IDs. This feature does not claim that arbitrary client plugins support crossplay.
The example plan can represent Server Devcommands but code contains no
plugin-specific bypass or hardcoded private world names.

All source BepInEx configuration files are retained verbatim in quarantine.
Recognized text plugin configurations are copied to the fresh loader's config
directory; the old `BepInEx.cfg` remains quarantined and fresh loader defaults stay
active. Review that old file manually if you need selected settings. Source
patchers/core files are not imported. Non-text/unrecognized config formats require
manual review rather than automatic execution or silent deletion.

## 5. Perform the import and verify before joining

Repeat the successful local/SSH command **without** `--dry-run`, adding the reviewed
plan for a modded source:

```bash
valheim migrate import \
  --archive /root/valheim-snapshot.tar.gz \
  --sha256 EXPECTED_SHA256 \
  --mod-plan /root/reviewed-mod-plan.json
```

Enter and confirm the replacement password at the hidden prompt. It is validated
through the existing configuration validator. Never pass it on the command line.
World selection, access lists, crossplay, world modifiers, intervals and backup
settings transfer independently of the changed password. Existing destination
archive retention remains its local policy; automatic updates are forced off.

The command prints a transaction ID. Retain it. It stages new binaries, uses
existing CLI backup/stop controls, then activates under the existing lock and
persistent maintenance marker. A complete prior destination and every original
imported file remain under `/var/lib/valheim-migration/TRANSACTION/` (root-only).
Modes are normalized: private data files, root-owned configuration `0640` with
group `valheim`, game/save ownership `valheim:valheim`, root-owned launch code.
It does not trust source ownership, setuid bits, symlinks or executables.

The destination is left stopped. Coordinate cutover so the original server is
not advertised concurrently, then deliberately run:

```bash
valheim check-config
valheim start
valheim logs
```

`--start` can explicitly request this start as part of import, but it cannot decide
whether a source operator has restarted the original server. Confirm readiness,
BepInEx/plugin logs, correct world, permissions and Steam/crossplay connections
on a disposable copy before accepting production players. Static validation is
not a game-health test. No original VM shutdown/deletion happens after success.

Importing the same archive and plan again is a no-op after a committed transaction,
provided configuration/launcher/lock and recorded executable-code fingerprints
still match. Drift or an
incomplete transaction requires explicit review instead of overwriting new data.

## Recovery and rollback

Do not delete transaction directories or remove a maintenance marker to force an
incomplete activation to start. Most pre-activation failures leave the old game
untouched; activation exceptions attempt rollback automatically. A killed process
or power interruption can require the explicit recovery command:

```bash
valheim migrate rollback TRANSACTION_ID
```

Use the printed 24-character hexadecimal ID. Rollback uses the CLI's safe stop,
takes the management lock and restores recorded prior game/saves/configuration,
launcher and migration lock. Displaced post-import state is retained under
`after/`, not deleted. It leaves the destination stopped for inspection and is
idempotent. Review and use `valheim start` only when satisfied. A rollback can
return the destination to older world progress; preserve any wanted later progress
from `after/` before further changes. The original VM is never changed by rollback.

If rollback cannot obtain the lock or the game will not stop, resolve the reported
condition before retrying. Protected `journal.json`, the `old/`, `after/` and
`quarantine/` directories, and staging data under `/opt/.valheim-migration-ID/`
are recovery evidence. Do not share plugin configs/logs publicly without reviewing
them for credentials. Retained state is not automatically pruned; budget disk and
copy verified backups elsewhere before operator-led cleanup.

Normal game updates are blocked while `/etc/valheim/mod-lock.json` exists. Before
future updates, test a new game/mod combination in a separate disposable LXC,
retain full backups of the game/mod tree and custom launcher, and plan the update
explicitly. Removing the lock is an owner decision, not an automatic compatibility
check. World-only `valheim backup`/`restore` still work but do not replace full
migration rollback or back up your custom executable mod tree. Do not enable
unattended Steam updates for an unreviewed mod set.

## Development validation

`bash tools/check.sh` includes migration tests and add-on shell linting. Tests use
synthetic multi-world saves, access lists, five config files, generated PE/ELF
headers, local package ZIPs and mocked OS/Steam/SSH controls. They cover archive
tampering/traversal, stale/live snapshots, dry-run side effects, translation,
quarantine, approved fresh packages, transaction rollback and idempotency. They
never connect to a production source or execute a plugin.

Real SSH transfer, fresh Steam/BepInEx downloads, runtime mod loading and rehearsal
cutover/rollback still require controlled integration testing. Keep the original
VM recoverable throughout that process.
