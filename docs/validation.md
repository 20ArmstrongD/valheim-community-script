# Validation record

## Optional migration development (2026-10-17)

Migration was developed with synthetic local fixtures only. The full suite now
contains the original 29 installer/management tests plus 23 migration tests.
Migration coverage includes multi-world archives and built-in backups, access
lists, five configuration files, static translation without source credentials,
dry-run, SSH command construction/streaming, package/assembly integrity,
quarantine, idempotent imports/retries, rename failure recovery and rollback.

No production VM was contacted, inspected, stopped or migrated. SSH, systemd,
Steam and package downloads were mocked; ZIP/tar extraction, hashing and
transaction filesystem operations used real disposable local files. Real
Steam/BepInEx installation and plugin/gameplay compatibility remain unverified.
ShellCheck is unavailable locally and must be run by CI.

## Reported Proxmox and CI validation

The project owner reports that GitHub Actions validation passes and that the
Community Script works as intended in their Proxmox environment. The CI output
shared during development shows all 29 behavioral tests passing; subsequent
ShellCheck configuration fixes were followed by a reported successful workflow.

The Proxmox version, template/build identifiers and results for each gameplay,
crossplay, reboot, update and restoration check have not been recorded. The
[acceptance checklist](testing.md) remains available to document those details;
unchecked items do not negate the owner's reported successful installation.

## Initial local development checks

Date: 2026-10-16. Environment: macOS (Darwin), Bash 3.2.57, Python 3.14.
No Proxmox host, production VM, Steam login or game download was used.

| Check | Result |
|---|---|
| Behavioral suite | 28 tests passed |
| Real filesystem backup/restore round trip | Passed within the suite; synthetic `.db`/`.fwl` fixtures |
| Mocked fresh guest setup, exported inputs and generated password | Passed |
| Mocked repeated guest setup | Passed; original configuration and save bytes retained |
| Bash syntax | Passed for both entry points, the check script, and all four emitted shell scripts |
| Metadata JSON parsing/input declaration consistency | Passed |
| Repository whitespace and reference-secret/name scan | Passed |
| ShellCheck | Not run: executable absent and shell network access unavailable |
| Full `tools/check.sh` | Exits 1 after successful syntax/tests because ShellCheck is required |
| GitHub Actions/Linux systemd unit verification | Defined, not run locally or remotely |
| Real Proxmox/Core installation, Steam download, reboot, gameplay and crossplay | Not run; explicit host approval and disposable-LXC testing required |

After the full suite, the ownership repair additions were checked with targeted
installation/reinstallation tests, and syntax was rechecked after the final error
message change. At the time of validation, no remote was configured, nothing had
been committed or pushed, and no pull request had been opened. Subsequent GitHub
repository preparation uses `main`; these results describe the implementation
checks, not its publishing state.

The table above preserves the initial local results. The newer owner-reported
results are recorded at the top. These reports do not imply upstream acceptance;
record detailed environment and acceptance evidence before submission.
