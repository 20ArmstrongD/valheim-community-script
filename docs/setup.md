# Install and set up a Valheim server on Proxmox

This guide creates a new Valheim LXC using the Community Scripts wizard. Existing
VMs and game servers remain separate. The project owner reports a working Proxmox
installation; detailed environment information and individual acceptance results
are tracked in the [validation documentation](validation.md).

## 1. Open the Proxmox node shell

1. Sign in to your Proxmox web interface.
2. In the left sidebar, select the **Proxmox node** that will host the server.
   Select the node itself, not an existing VM or container.
3. Click **Shell**. Use a root shell for the installer.

## 2. Run the installer

Paste this into the node shell and press **Enter**:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/20ArmstrongD/valheim-community-script/main/ct/valheim.sh)"
```

The command downloads the CT entry point from this repository, loads Community
Scripts Core, and starts the container setup wizard. Review downloaded scripts
before executing them as root. The command follows the current `main` branch.
Do not run it inside your existing Valheim VM or an existing container.

## 3. Choose container settings

Follow the wizard to select a **new container ID**, storage, network bridge and
network configuration. The new container needs Internet access to download
SteamCMD and the game server.

Starting defaults for a community of approximately 6–10 players are:

| Setting | Default |
|---|---|
| Operating system | Debian 13, amd64 |
| Container type | Unprivileged LXC |
| CPU | 4 vCPUs |
| Memory | 8192 MiB |
| Disk | 32 GiB |

Adjust resources for your host and world size. These defaults are starting points,
not a performance guarantee. Leave capacity for your other guests. The installer
does not require privileged-container features or Docker.

## 4. Configure the game server

Answer the server prompts:

| Setting | What to enter |
|---|---|
| Server name | The name players see in the server list |
| World name | Your world identifier; default `Dedicated` |
| Password | A private password of 5–64 printable ASCII characters; empty generates one |
| Visibility | `1` to appear in the server list, `0` for a private listing |
| Crossplay | `1` for supported Steam/crossplay clients, `0` for Steam only |

World names accept letters, digits, underscores and hyphens, beginning with a
letter or digit. The server name must not contain the password. Hidden password
input does not show characters as you type. Unattended installations use defaults
when settings are omitted; see [all configuration options](../README.md#configuration).

Allow installation to finish. SteamCMD downloads the official dedicated server,
and systemd starts it under the dedicated `valheim` account.

## 5. Open the new container console

Select the **new Valheim container → Console** in Proxmox. Log in as root using
the container credentials configured through the framework.

From this point, run the following commands **inside that container**, not in the
Proxmox node shell:

```bash
valheim status
valheim logs
```

Wait for `Game server connected` in the logs before trying to join. Press
**Ctrl+C** to leave the log viewer; the game server keeps running. With crossplay
enabled, check the logs for the join code.

The configuration is `/etc/valheim/server.conf`. If the installer generated a
password, view the file privately as root to retrieve it. Do not share the file,
screenshots of its contents, or backup archives publicly.

## 6. Connect your players

Find the new container's IP address in its Proxmox **Summary** or **Network** view.
The base game port defaults to `2456`.

- **Steam backend (`crossplay=0`):** join using the server list or the container's
  reachable address. Allow inbound UDP `2456–2457` through applicable firewalls.
  Internet players also need router forwarding for that pair to the container's
  address. A custom base port uses that port and the next one.
- **Crossplay backend (`crossplay=1`):** use the join code, server list or public
  address. Local/loopback IP joins do not work with this backend. The relay does
  not require inbound router forwarding; outbound provider connectivity must work.

Enter the server password when joining. Clients and server need compatible game
versions. See [networking and joining](../README.md#networking-and-joining) for
additional firewall details. No firewall or router settings are changed for you.

## 7. Manage your server

Run these as root inside the Valheim container:

```bash
valheim status
valheim start
valheim stop
valheim restart
valheim logs
valheim backup
valheim update
```

Run only the command for the action you want. `backup` and `update` briefly stop
the game, make a consistent backup, and resume it only if it was running before.
The service starts automatically when the container boots. To start the container
automatically when the Proxmox node boots, enable the container's **Start at boot**
option in Proxmox as well.

To change settings later, edit `/etc/valheim/server.conf` as root, then run:

```bash
valheim check-config
valheim restart
```

Optional automatic updates are disabled by default. See the
[automatic-update instructions](../README.md#optional-automatic-updates) before
enabling them, since scheduled maintenance can disconnect players.

## 8. Keep a recoverable backup

Before important changes, run `valheim backup`. Copy the printed archive and its
`.sha256` sidecar from `/var/backups/valheim/` to protected storage outside the
container. Archives contain the password and world data. Local backups alone do
not protect against loss of the container disk.

Follow the [backup and restore guide](backup-restore.md) for verification,
retention, restoring worlds and recovering from an interrupted update. Valheim's
built-in world backups and Proxmox guest backups are separate protection layers.

## If setup does not complete

Inspect the failed command's error and the new container's console. If the game
service was installed, use `valheim status` and `valheim logs` there. Check free
disk space, network access, configuration validation and the selected backend's
connection method. Do not modify or stop an existing production VM to troubleshoot
the new container. Share only redacted diagnostics when reporting a problem.
