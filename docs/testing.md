# Validation and fresh-LXC acceptance

See [the local validation record](validation.md) for executed checks and pending work.

## Local automated checks

Run `bash tools/check.sh`. It extracts literal runtime heredocs without running
installation, checks Bash syntax, runs Python unittest behavioral tests, then runs
ShellCheck on both entry points and every emitted shell script. Missing ShellCheck
is a failure, not a silently skipped check. CI repeats this on Linux and verifies
the emitted systemd units without starting the game.

The harness rewrites fixed production paths into a temporary directory and mocks
root identity/metadata, systemd, SteamCMD, account switching and package operations.
It uses actual Bash, tar/gzip, SHA-256, file content comparisons and advisory locks.
No production runtime accepts a test-root environment override. Tests do not need
root privileges. Python is used only by development tooling, not installed in the LXC.

Covered: strict literal configuration, invalid/missing/duplicate keys, permission
checks, argument boundaries, stable Steam command, stop/backup/update/start order,
stopped-state preservation, shutdown/backup/Steam failures, persistent inhibition,
checksum rejection, restore round trip, link rejection, extraction failure,
retention after success, concurrent operation rejection and automation opt-in.

## Required real environment tests (not performed here)

Only execute after explicit host approval. Use a new amd64 unprivileged LXC on a
test Proxmox host, a dedicated test IP and disposable worlds. Record the Proxmox
version, kernel, Debian template version, Core/script commits, Steam build ID,
resource allocation, dates and anonymized outcomes. Never use the active VM.

- [ ] Fresh framework install on Debian 13, with no privileged features enabled.
- [ ] Interactive prompts and hidden password input work through the framework.
- [ ] Unattended exported inputs arrive; omitted password generates privately.
- [ ] Check ownership/permissions of configuration, binaries, saves and archives.
- [ ] `systemctl show valheim -p User` and process ownership show `valheim`.
- [ ] Service reaches `Game server connected`; no password appears in installer output.
- [ ] Join through Steam backend with UDP base/base+1 allowed.
- [ ] Enable crossplay; join through Steam and another supported client using a
      public address/join code; verify expected LAN-address limitation.
- [ ] Exercise start, stop, restart, status, logs and check-config.
- [ ] Create a recognizable test-world change, stop safely and reboot the LXC;
      verify the same world and configuration return.
- [ ] Update a running server; verify graceful saves, backup and readiness.
- [ ] Update a stopped server; verify it remains stopped.
- [ ] Block Steam egress during update; verify failure, preserved archives and
      inhibition across reboot; follow documented recovery.
- [ ] Trigger disk-full backup failure in the disposable container; verify SteamCMD
      never runs and existing archives are retained.
- [ ] Take a backup, change the test world, restore and verify the old world in-game.
- [ ] Restore an off-container copy into a second fresh LXC, retaining desired config.
- [ ] Configure retention=2; create three backups and verify oldest completed pair
      removed only after success.
- [ ] Enable timer with a temporary near-term calendar override; verify the same
      safe update path. Disable opt-in and verify no game stop/update occurs.
- [ ] Attempt concurrent management operations and verify lock rejection.
- [ ] Repeat guest setup in the disposable LXC through the framework; verify
      existing configuration/worlds and timer overrides survive.
- [ ] Run under ordinary unprivileged-LXC AppArmor/mount restrictions; verify systemd
      filesystem hardening works without nesting. If it fails, investigate and
      document the specific restriction instead of enabling privileges broadly.
- [ ] Observe CPU/RAM/disk under representative load; resource defaults are estimates.

Do not mark these boxes based on mocked local tests. Incus/Ubuntu compatibility
is unverified and needs separate testing before being advertised as supported.
