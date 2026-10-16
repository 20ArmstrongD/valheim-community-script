# Customize your Valheim server

The installer provides a working baseline. World rules, difficulty and community
policies are the owner's choice; none of the examples below are applied for you.
Choose individual changes with your players rather than copying every example.

All Linux commands in this guide run as root **inside the Valheim LXC**, not on
the Proxmox node or an existing VM. Discuss maintenance with connected players
and take a backup before changing world rules.

## Choose the right place to make a change

| What you want to change | Where to change it |
|---|---|
| Server name, password, visibility, crossplay and port | `/etc/valheim/server.conf` |
| Save frequency and backup retention | `/etc/valheim/server.conf` |
| Difficulty presets, portal rules, resources, raids and world checkbox options | Startup arguments in `/usr/local/libexec/valheim-launch` |
| Admin, banned and permitted players | Text files under `/var/lib/valheim/saves/` |
| Optional automatic updates and maintenance schedule | Configuration and `valheim-update.timer` override |

`server.conf` accepts only the keys listed in the [configuration reference](../README.md#configuration).
Do not add lines such as `portals=casual` or `extra_args=...` there: the current
validator rejects unknown keys. The game supports additional startup options
without those options becoming new installer settings.

This guide uses built-in Valheim features. Mods and their client/server
compatibility requirements need separate planning.

## Server identity, privacy and crossplay

Edit the existing values in `/etc/valheim/server.conf`, then validate and restart:

```bash
nano /etc/valheim/server.conf
valheim check-config
valheim restart
```

- Choose a recognizable `server_name` and a private `server_password`. Do not
  include the password in the name or publish the configuration.
- Set `public=0` to hide the server listing. Players still need the correct
  connection details and password; this setting is not a firewall.
- Set `crossplay=1` for the PlayFab backend or `crossplay=0` for Steam only.
  See [joining and networking](../README.md#networking-and-joining) before changing
  it: crossplay does not support joining by a local/loopback address.
- Change `port` when needed, and update the relevant firewall/NAT rules for the
  base UDP port and the next one when using the Steam backend.
- Changing `world_name` selects or creates a world. It does not rename or copy
  your existing world files.

## Apply optional world modifiers

Iron Gate documents `-preset`, `-modifier` and `-setkey` as dedicated-server
startup arguments. These are arguments to the game, not commands to type at the
Linux prompt. Check the [official dedicated-server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/)
and the manual shipped in `/opt/valheim/` for the installed game version.

### Back up and stop

```bash
valheim backup
valheim stop
cp -p /usr/local/libexec/valheim-launch "/root/valheim-launch.$(date -u +%Y%m%dT%H%M%SZ).bak"
nano /usr/local/libexec/valheim-launch
```

The first command creates a consistent world/config archive; it resumes a running
server before the second command stops it for editing. The copy separately
preserves the launch wrapper, which is not part of the world archive.

Near the bottom of the wrapper, find:

```bash
[[ "$crossplay" == 0 ]] || args+=(-crossplay)
exec ./valheim_server.x86_64 "${args[@]}"
```

Add only your desired `args+=(...)` lines **between those two lines**. Keep the
existing argument array, credential handling and final `exec` intact. Do not edit
Steam's own `start_server.sh`, which can be replaced by a game update.

### Examples to choose from

| Goal | Line to add before the final `exec` |
|---|---|
| Allow normally restricted materials such as ore/metals through portals | `args+=(-modifier portals casual)` |
| Increase resource drops | `args+=(-modifier resources more)` |
| Use an even higher resource setting | `args+=(-modifier resources muchmore)` |
| Reduce raid frequency | `args+=(-modifier raids less)` |
| Disable raids through the world raid setting | `args+=(-modifier raids none)` |
| Reduce combat difficulty | `args+=(-modifier combat easy)` |
| Increase combat difficulty | `args+=(-modifier combat hard)` |
| Use the casual death-penalty setting | `args+=(-modifier deathpenalty casual)` |
| Enable passive-enemy mode | `args+=(-setkey passivemobs)` |
| Enable no-build-cost mode | `args+=(-setkey nobuildcost)` |
| Enable player-based raid events | `args+=(-setkey playerevents)` |
| Disable the map | `args+=(-setkey nomap)` |

An individual world modifier changes that category; choosing casual portal rules
does not require selecting the whole Casual preset. No-build-cost mode is not a
general promise of free crafting, and disabling raids is not a promise that all
other enemy encounters disappear. Check actual behavior in your installed build.

For example, an owner might choose only these three changes:

```bash
args+=(-modifier portals casual)
args+=(-modifier resources more)
args+=(-modifier raids less)
```

### Available presets and values

The official guide lists these options. Labels in the game UI may differ from the
startup tokens; do not substitute UI text or a multiplier such as `2x` for a token.

| Category | Documented values |
|---|---|
| Preset (`-preset`) | `normal`, `casual`, `easy`, `hard`, `hardcore`, `immersive`, `hammer` |
| Combat (`-modifier combat`) | `veryeasy`, `easy`, `hard`, `veryhard` |
| Death penalty (`-modifier deathpenalty`) | `casual`, `veryeasy`, `easy`, `hard`, `hardcore` |
| Resources (`-modifier resources`) | `muchless`, `less`, `more`, `muchmore`, `most` |
| Raids (`-modifier raids`) | `none`, `muchless`, `less`, `more`, `muchmore` |
| Portals (`-modifier portals`) | `casual`, `hard`, `veryhard` |
| Checkbox keys (`-setkey`) | `nobuildcost`, `playerevents`, `passivemobs`, `nomap` |

Some slider defaults have no explicit token in the official list. Do not invent
`-modifier portals normal` or assume every category accepts `none`.

A preset resets other modifiers. If you deliberately want a complete preset plus
overrides, place the preset **first**, followed by individual modifiers:

```bash
args+=(-preset easy)
args+=(-modifier portals casual)
args+=(-modifier raids less)
```

Do not add a preset merely to change one setting. Keep one intended value per
category to make the resulting rules understandable. Flags left in the launch
wrapper are supplied on every start; avoid combining conflicting changes from
multiple administration methods.

### Validate, start and verify in-game

Save the wrapper, then run:

```bash
bash -n /usr/local/libexec/valheim-launch
valheim check-config
valheim start
valheim logs
```

`bash -n` checks shell syntax; `check-config` checks `server.conf`. Neither proves
that Valheim recognizes or applies a game modifier. Wait for readiness, reconnect
and test the specific change. For portal rules, test carrying a normally restricted
material through a suitable portal. Review startup logs for errors.

Game updates through `valheim update` retain this wrapper because it lives outside
Steam's installation directory. Re-running the guest installer can replace the
wrapper, so record your chosen lines separately. Normal configuration/world
backups do not include this custom wrapper.

### Undo a change

Stop the server before editing again. Remove unwanted startup lines and restore
the previous wrapper if needed; use the timestamped copy you created above.
Run `bash -n` before starting it.

Do not rely on removing a startup argument alone to reverse a world setting that
the game may have saved. Check the world in-game. If you intentionally want to
reset the modifier set, the documented `-preset normal` resets other modifiers
too; reapply any desired overrides afterward. That is a broad reset, not a
portal-only undo command.

For a full world rollback, use the [restoration procedure](backup-restore.md)
with the pre-change archive and restore the earlier wrapper separately. A world
rollback also discards progress since that archive. The game-specific modifier
examples have not been individually validated by this project's automated tests.

## Admins and player access

Valheim keeps these files in the configured save directory, which this installer
sets to `/var/lib/valheim/saves/`:

| File | Purpose |
|---|---|
| `adminlist.txt` | Players allowed to use the game's supported administrator commands |
| `bannedlist.txt` | Players blocked from joining |
| `permittedlist.txt` | Players allowed to join when a permit list is in use |

Use one platform user ID per line, in the case-sensitive format shown in the
server logs or the game's F2 panel: `[Platform]_[User ID]`. Do not substitute a
display name or guess an ID. Adding entries to the permitted list excludes other
players, so plan it before enabling a private community allowlist.

Back up and stop before editing these files. Preserve `valheim:valheim` ownership
and private permissions when creating a missing file, then start the server and
test with the intended account. Granting admin status does not automatically
enable every single-player cheat or debug command on a dedicated server.

## Saves, backups and update choices

Edit these existing `server.conf` keys to suit your community:

| Key | Purpose / installer default |
|---|---|
| `saveinterval` | World save interval, `1800` seconds |
| `world_backups` | Valheim's built-in backup count, `6` |
| `backupshort` | Built-in short backup interval, `7200` seconds |
| `backuplong` | Built-in long backup interval, `43200` seconds |
| `backup_retention` | Completed local management archive count, `14` |
| `auto_update` | Optional scheduled game updates, `0` (off) |

Shorter save intervals trade more frequent disk work for a shorter interval
between saves. More retained archives need more disk. Built-in backups, the
`valheim backup` archives and Proxmox guest backups are separate layers; copy
important archives off-container.

Run `valheim check-config` after editing. Restart for game save-setting changes.
Changing `auto_update` takes effect at the next timer check without a game restart.
Use the [automatic-update guide](../README.md#optional-automatic-updates) to choose
a suitable maintenance window instead of surprising connected players.

## Further references

- [Official dedicated-server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/): launch options, modifier tokens and player-access lists.
- [Configuration reference](../README.md#configuration): keys supported by this installer.
- [Setup guide](setup.md): initial installation and joining.
- [Backup and recovery](backup-restore.md): protection before changing your world.

Reviewed against Iron Gate's online guide on 2026-10-16. Recheck the manual
bundled with your server after updates; do not assume a UI option has an identical
startup flag or that a setting's behavior never changes.
