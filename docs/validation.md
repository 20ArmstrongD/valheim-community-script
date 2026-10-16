# Local validation record

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

This is a development candidate, not a claim of production readiness or upstream
acceptance. Complete [the real environment checklist](testing.md) and replace
this record with actual results before submission.
