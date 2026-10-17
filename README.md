# Valheim Dedicated Server for Community Scripts

A development candidate for an amd64, unprivileged Proxmox VE LXC running
the official stable Valheim Dedicated Server (Steam App ID `896660`). The shared
Community Scripts Core creates the container; these scripts install the game
directly, without Docker, nesting, device passthrough, or privileged-container requirements.

**Status:** the project owner has validated the installer working on Proxmox, and
GitHub Actions validation passes. See the [setup walkthrough](docs/setup.md) to
install your own server and the [validation record](docs/validation.md) for the
scope of reported testing. No existing VM is inspected, changed, stopped, or
migrated. Use fresh containers and disposable worlds for development.

The installer is MIT licensed. Valheim and SteamCMD retain their respective
licenses; game binaries are downloaded from Valve, never redistributed here.
Players need a compatible licensed game client.

To publish this standalone development repository to your own GitHub account,
follow [the GitHub setup instructions](docs/github-setup.md). Publishing the
repository does not install anything on Proxmox or submit a Community Scripts PR.

## Install from the Proxmox node shell

This is an independent project using Community Scripts Core, not an official
Community Scripts release. It creates a new LXC for your Valheim server.

Open your **Proxmox node → Shell**, log in as root, and run:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/20ArmstrongD/valheim-community-script/main/ct/valheim.sh)"
```

New to Proxmox? Follow the [step-by-step setup guide](docs/setup.md), including
where to paste the command, the settings to choose, and how to join afterward.

Run this on the Proxmox node, not inside your existing game VM or an existing
container. It invokes the Community Scripts wizard to create a new LXC and fetches
`install/valheim-install.sh` from this same repository. Review the scripts before
running a command that downloads code as root. The repository must be public for
unauthenticated users to download it.

1. Use the wizard to choose a new container ID, storage, networking and resources.
2. Supply the server name, world name, password, visibility and crossplay settings
   when prompted. Blank/noninteractive values use the defaults below; an omitted
   password is randomly generated and stored in the protected configuration.
3. Open the **new container's console**, run `valheim logs`, and wait for
   `Game server connected`. Read the generated password locally as root from
   `/etc/valheim/server.conf` if needed. Never paste that file into an issue.
4. Configure the [game networking](#networking-and-joining) and test joining.

After installation, management commands such as `valheim update`, `valheim backup`
and `valheim restart` run **inside the new LXC**. They do not run on the Proxmox
node or manage an existing VM.

The command follows `main`, so future executions fetch its current contents.
A tagged release can provide an install command pinned to a tested version for
repeatable distribution.

## Testing a fork or preparing an upstream contribution

Review [the submission notes](docs/submission.md) and run the local checks before
testing another branch. Core supports an explicit application-script root:

   ```bash
   export COMMUNITY_SCRIPTS_URL='https://raw.githubusercontent.com/YOUR_ACCOUNT/DevScripts/YOUR_BRANCH'
   bash -c "$(curl -fsSL "$COMMUNITY_SCRIPTS_URL/ct/valheim.sh")"
   ```

Replace the account, repository and branch as appropriate. Before submitting the
two scripts to DevScripts, change `_CS_DEFAULT_URL` in `ct/valheim.sh` to
`https://raw.githubusercontent.com/community-scripts/DevScripts/main`. Never run
the guest installer directly or use an existing production container as a test target.

Recommended starting allocation for 6–10 players: **4 vCPUs, 8192 MiB RAM,
32 GiB disk**, Debian 13 amd64, unprivileged LXC. These are configurable starting
values, not measured performance guarantees. Large worlds and retained archives
need more disk space. The installer also accepts amd64 Ubuntu with systemd;
only Debian 13 is the initial test target. ARM64 and Alpine are unsupported.

## Configuration

For optional world rules, portal materials, difficulty, resource drops, raids,
admin/access lists and maintenance choices, see the
[server customization guide](docs/customization.md). The installer leaves those
choices to the owner; examples are not applied automatically.

Moving an existing Linux server? The optional [VM-to-LXC migration guide](docs/migration.md)
covers stopped-source snapshots, SSH/local imports, reviewed BepInEx/plugins,
curl downloads or offline `--mod-package-dir` packages, and staging recovery/rollback. It is a separate add-on; fresh installs remain vanilla and do not
automatically connect to or alter an existing VM.

Edit `/etc/valheim/server.conf` as root, then run `valheim check-config` and
`valheim restart`. File owner/group must be `root:valheim`, mode `0640`;
`/etc/valheim` is `root:valheim` mode `0750`. The service account has no login shell.

The format is literal `key=value`: **no shell quotes, export, expansion, inline
comments or whitespace around `=`**. Values may contain spaces and punctuation;
they are never evaluated as shell code. Empty lines and full-line `#` comments
are accepted. Duplicate, unknown and missing keys fail validation without
printing their values. ASCII-only names are a deliberate first-release limit.

| Configuration key | Installation input | Default / accepted values |
|---|---|---|
| `server_name` | `var_valheim_name` | `Valheim Community`; 1–64 printable ASCII characters |
| `world_name` | `var_valheim_world` | `Dedicated`; 1–64 letters, digits, `_`, `-`; first character alphanumeric |
| `server_password` | `var_valheim_password` | Generated; 5–64 printable ASCII characters; cannot occur in server name |
| `public` | `var_valheim_public` | `1`; `0` private, `1` listed |
| `crossplay` | `var_valheim_crossplay` | `1`; `0` Steam only, `1` PlayFab crossplay |
| `port` | `var_valheim_port` | `2456`; 1024–65534 |
| `saveinterval` | `var_valheim_saveinterval` | `1800`; 60–86400 seconds |
| `world_backups` | `var_valheim_backups` | `6`; 1–100 built-in backups |
| `backupshort` | `var_valheim_backupshort` | `7200`; 60–31536000 seconds |
| `backuplong` | `var_valheim_backuplong` | `43200`; 60–31536000 seconds |
| `backup_retention` | `var_valheim_retention` | `14`; 1–365 independent archives |
| `auto_update` | `var_valheim_autoupdate` | `0`; opt in with `1` |

All listed `var_*` inputs are exported by the CT entry point and can be set before
running it for unattended deployment. Avoid passwords in command-line literals,
shell history, shared `.vars` files, issue reports or tracing. Prefer the hidden
guest prompt or the generated password. Do not use Core trace mode or `bash -x`
with real credentials. These scripts disable xtrace around credential handling;
they cannot sanitize logs collected by an externally instrumented shell/framework.
Unattended public/crossplay values use `0`/`1`, not `yes`/`no`.

Changing `world_name` selects/creates a world; it does not rename existing world
files. Existing saves and configuration are not overwritten by application
updates. A repeated guest setup preserves an existing configuration and performs
a normal backed-up game update. It refuses an existing `valheim` user with a
different home rather than guessing how to migrate it.

## Service and management

Run commands as root inside the new LXC (`sudo valheim ...` from another account).

| Command | Behavior |
|---|---|
| `valheim start` | Validate configuration and start; inspect logs for game readiness |
| `valheim stop` | Send SIGINT through systemd and verify shutdown |
| `valheim restart` | Gracefully stop, then start |
| `valheim status` | Show systemd status; inactive services return a nonzero status |
| `valheim logs` | Follow the last 100 journal lines; Ctrl+C exits |
| `valheim check-config` | Validate configuration without disclosing values |
| `valheim backup` | Stop, archive saves/config, verify, resume only if previously running |
| `valheim update` | Stop, back up, run anonymous SteamCMD stable update/validation, resume if appropriate |
| `valheim restore /var/backups/valheim/ARCHIVE.tar.gz` | Verify checksum, take a pre-restore backup, replace saves, retain current configuration |

The framework's standard `update` entry point calls the same `valheim update`
implementation. SteamCMD manages application versions rather than GitHub release
helpers. An update validates the stable distribution even if no new version is
available, so every invocation has maintenance downtime. Mods and public-test
branches are outside the first release's scope.

`valheim.service` runs as `valheim`, starts at boot, and restarts on failure with
rate limiting. It uses journal logging, a restricted writable filesystem, no new
privileges and an empty capability set. SIGINT allows Valheim to save. After 180
seconds, a failed stop is reported without automatically sending SIGKILL; inspect
the process instead of copying a potentially active world.

Mutation commands share an exclusive lock. A persistent root-owned
`/etc/valheim/maintenance` file inhibits starts during backup/update/restore,
including after a failed update and a reboot. A backup failure before modifying
anything resumes a previously running server; update/restore/shutdown failures
leave inhibition in place for inspection. Use these commands consistently;
direct service/configuration changes must not be made during maintenance.

Configuration and archives contain the password and must remain private. Valheim's
upstream binary requires the password as a process argument: container root,
same-user processes and administrators with host access can inspect it. The game
account is not a security boundary from those administrators. Do not grant it
interactive access. Journal content comes from the upstream game; review/redact
logs before sharing them. No server password is printed by our management code.

## Optional automatic updates

Set `auto_update=1`, then run `valheim check-config`. The enabled
`valheim-update.timer` checks the setting each day at **05:00 container local
time**, with up to 15 minutes of random delay. When `0`, its ExecCondition skips
the update. Missed runs are not replayed on boot. This can disconnect players;
choose a maintenance window for your community.

```bash
systemctl list-timers valheim-update.timer
systemctl edit valheim-update.timer
```

Example override:

```ini
[Timer]
OnCalendar=
OnCalendar=Sun *-*-* 06:00:00
RandomizedDelaySec=0
```

Then `systemctl daemon-reload && systemctl restart valheim-update.timer`.
Disable with `auto_update=0` or `systemctl disable --now valheim-update.timer`.
Inspect automated maintenance with `journalctl -u valheim-update.service`.
Failed SteamCMD diagnostics remain as root-only `.steamcmd.*` files in the backup
directory; successful runs remove them. Changing `auto_update` requires no server
restart. This timer updates the game only, not Debian/SteamCMD's OS dependencies.

## Networking and joining

For the Steam backend, allow **UDP base port and base port + 1** to this LXC
(default `2456–2457`). Account for the datacenter, node and container Proxmox
firewalls, guest firewall if enabled, and router NAT when serving Internet users.
Create narrowly scoped inbound UDP allow rules for those ports targeting this
container. Do not change the Proxmox management firewall or forward SSH/web
management ports for game clients. No firewall is changed automatically.

With `crossplay=1`, Iron Gate documents relay-based connectivity without inbound
router forwarding. Clients join using a public address, join code, or server
list; **local/loopback IP joins do not work with crossplay**. Do not diagnose that
limitation as an LXC failure. Allow outbound DNS/HTTPS and Steam/PlayFab traffic;
restrictive egress firewalls need current provider rules. Private visibility
does not disable authentication. Steam clients can join the crossplay backend.

Check readiness/join code in `valheim logs`, ensure clients and server have
compatible game versions, and inspect `ss -lunp` inside the container. See the
[official dedicated-server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/)
for backend behavior and ports.

## Backups, restore, recovery and removal

Follow [backup and restoration procedures](docs/backup-restore.md). Built-in world
backups, management archives, and Proxmox guest backups are three separate layers.
Copy verified archives off the container; local archives cannot survive loss of
its disk. Filesystem backups are manual or pre-maintenance; no independent daily
backup timer is enabled by this installer.

For removal, first stop the update timer, run `valheim backup`, copy the archive
and checksum off-container, and verify that copy. Shut down and delete **only the
disposable Valheim LXC** through Proxmox after checking its ID and backup. Container
deletion removes all local saves and archives. There is deliberately no one-line
host deletion command or automatic cleanup of an existing VM. To keep the guest,
`systemctl disable --now valheim-update.timer valheim.service` disables the application;
its files remain available for recovery.

## Development checks

Requires Bash, Python 3.10+, GNU coreutils/tar on Linux, and ShellCheck. On macOS,
the behavioral harness also accepts Homebrew coreutils (`gsha256sum`). No Python
packages, Steam account, root privileges, or Proxmox connection are needed.

```bash
python3 -m unittest discover -s tests -v
bash tools/check.sh
```

`tools/check.sh` extracts every generated runtime script and checks those alongside
the installer, avoiding the common gap of linting only the heredoc wrapper. It
fails if ShellCheck is unavailable. CI also verifies generated systemd units on
an isolated Ubuntu runner. These checks do not establish real game connectivity
or Proxmox compatibility; complete the [acceptance checklist](docs/testing.md)
before representing this as production-ready.
