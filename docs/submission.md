# DevScripts submission preparation

Research date: 2026-10-16. Remote documentation is mutable; recheck before submission.

## Sources reviewed

- [ProxmoxVE contribution guide](https://github.com/community-scripts/ProxmoxVE/blob/main/CONTRIBUTING.md)
- [Current CT template](https://community-scripts.org/docs/contribution/templates_ct/appname)
- [Current install template](https://community-scripts.org/docs/contribution/templates_install/appname-install)
- [DevScripts AGENTS.md](https://github.com/community-scripts/DevScripts/blob/main/AGENTS.md)
- [Script creator instructions](https://github.com/community-scripts/DevScripts/blob/main/.github/agents/pve-script-creator.agent.md)
- [DevScripts PR template](https://github.com/community-scripts/DevScripts/blob/main/.github/pull_request_template.md)
- [Crafty Controller CT](https://github.com/community-scripts/ProxmoxVE/blob/main/ct/crafty-controller.sh)
  and [installer](https://github.com/community-scripts/ProxmoxVE/blob/main/install/crafty-controller-install.sh)
- [Core helper loader](https://github.com/community-scripts/core/blob/main/lib/tools.func),
  [system helpers](https://github.com/community-scripts/core/blob/main/lib/system.func),
  and [runtime helpers](https://github.com/community-scripts/core/blob/main/lib/runtime.func)
- [Iron Gate dedicated-server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/)
- [Valve SteamCMD documentation](https://developer.valvesoftware.com/wiki/SteamCMD)
  (browser returned HTTP 403; use it to recheck before real installation)

The current engine lives in `community-scripts/core`; old examples using
`misc/build.func` from the application repository are stale. The bootstrap uses
Core's documented local/remote fallback. This standalone distribution uses the
owner's GitHub repository as the application root; change that default to
DevScripts when preparing the upstream branch.
There is no copied container creation logic. The installer uses Core lifecycle,
prompt, random-password and download helpers. The helper sources have no SteamCMD
installer; the official bootstrap is fetched through `curl_with_retry` and
extracted as `valheim`. The working reference deployment supplied App ID `896660`
and the anonymous `app_update ... validate` procedure; live download is unverified.

Iron Gate documents the launch switches, configurable save directory, SIGINT
shutdown, runtime library requirements, UDP port pair and crossplay behavior.
Its original startup script can be overwritten by Steam, so our root-owned launch
wrapper and literal configuration live outside the Steam directory. There is no
source build or database. The runtime is the proprietary Linux server binary;
the script does not fabricate an upstream GitHub repository or release version.

## Explicit differences requiring maintainer review

1. **Non-root game account.** Current AGENTS.md favors root in single-application
   LXCs. The project owner's explicit requirement takes precedence locally.
   SteamCMD and the network-facing game run as `valheim`; management runs as root.
   This deviation is intentional and must be disclosed, not silently presented
   as full upstream compliance.
2. **Standalone maintenance archives.** Core's `create_backup` helper is designed
   for installation/update transactions, retains a per-path manifest across
   retries, can skip mount points, and removes its store on restore. It is not a
   rolling world archive/retention/restore facility usable without Core. The
   installed CLI therefore owns stopped-world archives for manual and scheduled
   maintenance. `update_script()` delegates to that single tested path rather
   than creating a second backup implementation or fetching Core on every command.
3. **Official Steam distribution.** SteamCMD handles application version/update
   semantics. GitHub release helpers are inapplicable. The PR template's 600-star
   source-repository and official-release-tarball requirements cannot be honestly
   checked for a proprietary Steam-distributed game. Maintainers may decline the
   application. Seek an eligibility decision before submitting a PR; do not invent
   star counts, links or compliance claims.
4. **Runtime helpers.** Bash functions are emitted as standalone runtime utilities;
   the two installer entry points define no custom installation helper functions.
   `valheim-config` is shared executable code, not a sourced user configuration.
5. **Metadata.** The current contribution guide directs website metadata through
   the website, whereas AGENTS.md still describes `json/<app>.json`. A proposed
   record is provided in `docs/valheim-metadata.json` for review. Confirm the desired
   submission route instead of modifying a repository's metadata system blindly.
6. **Daemon reload.** Guest setup explicitly reloads units because it supports
   rerunning setup and replacing existing units. Local timer overrides are not
   overwritten. This is necessary for repeated setup, not a new-service workaround.

## Suggested PR description (draft only)

Adds an unprivileged amd64 Valheim LXC installer using the shared Core workflow,
official anonymous SteamCMD stable distribution, non-root systemd game service,
and configurable multiplayer/crossplay settings. Includes a local management
utility for graceful lifecycle operations, verified world/config archives,
retention, world restoration and optional scheduled updates. Saves and config are
outside Steam's update target.

Validation: fill in the actual local check results and approved fresh-LXC test
evidence. No real Proxmox installation or gameplay has been tested at drafting.
Do not tick "Tested thoroughly" or platform checkboxes until those tests pass.

AI assistance: Codex, GPT-6, used to research conventions, implement scripts,
write behavioral tests and review failure handling. Exact UI reasoning setting
was not exposed; do not claim one. Human review and real-host testing remain
required. Disclose the differences listed above and eligibility question.

## Before requesting approval to open a PR

- Complete local lint/tests and approved fresh-LXC acceptance tests.
- Rebase/copy the two installer files onto a current DevScripts branch; avoid
  changing the upstream root README, LICENSE or shared CI merely to add this app.
- Change `_CS_DEFAULT_URL` to the DevScripts application root on that upstream branch.
- Include focused app documentation/tests where maintainers prefer them; the
  local README, CI and license belong to this standalone development repository.
- Obtain reviewer guidance on eligibility, non-root execution, maintenance
  archives and metadata; retain these notes in the PR description.
- Add the real testing-thread URL as `var_testurl` only after it exists.
- Verify fork URLs resolve both CT and install scripts from the same branch.
- Review the complete diff for secrets, real world names, saves, addresses and
  generated artifacts. Public test fixtures contain dummy values only.
- Ask the owner to approve the concrete PR title/body/diff. No PR, issue, fork,
  push, Discord message or host installation is authorized by this document.
