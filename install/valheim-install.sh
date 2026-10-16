#!/usr/bin/env bash
# Copyright (c) 2026 Valheim Community Script contributors
# License: MIT
# Source: https://www.valheimgame.com/support/a-guide-to-dedicated-servers/

# Never run the guest installer on a Proxmox host or an existing VM.
set +x
if [[ "$EUID" -ne 0 || -z "${FUNCTIONS_FILE_PATH:-}" ]] || [[ "$(systemd-detect-virt --container 2>/dev/null)" != lxc ]]; then
  echo "Valheim: this installer must be invoked by Community Scripts inside a fresh LXC." >&2
  exit 1
fi
source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
catch_errors
setting_up_container
network_check
update_os
set +x

install -d -o root -g root -m 0700 /run/valheim-management
exec 8>/run/valheim-management/operation.lock
if ! flock -n 8; then
  msg_error "Another Valheim operation is running; retry after it finishes."
  exit 1
fi
if [[ "$(dpkg --print-architecture)" != amd64 ]] || [[ ! -f /etc/debian_version ]]; then
  msg_error "Valheim requires an amd64 Debian/Ubuntu container."
  exit 1
fi
if id valheim &>/dev/null; then
  if [[ "$(getent passwd valheim | cut -d: -f6)" != /var/lib/valheim || "$(id -u valheim)" == 0 ]]; then
    msg_error "Existing valheim account has a different home. Use a fresh container; no migration was attempted."
    exit 1
  fi
else
  useradd --system --user-group --home-dir /var/lib/valheim --shell /usr/sbin/nologin valheim
fi
install -d -o root -g valheim -m 0750 /etc/valheim
install -d -o valheim -g valheim -m 0750 /var/lib/valheim /var/lib/valheim/saves /opt/valheim /opt/steamcmd
install -d -o root -g root -m 0700 /var/backups/valheim
install -d -o root -g root -m 0755 /usr/local/libexec

cat <<'VALHEIM_CONFIG' >/usr/local/libexec/valheim-config
#!/usr/bin/env bash
# Configuration is data, never shell source. Keep this file root-owned.
valheim_error() { echo "Valheim: $*" >&2; exit 1; }

valheim_number() {
  local value="$1" minimum="$2" maximum="$3"
  [[ "$value" =~ ^[0-9]{1,8}$ ]] || return 1
  [[ "$value" == 0 || "$value" != 0* ]] || return 1
  (( value >= minimum && value <= maximum ))
}

valheim_validate() {
  local LC_ALL=C
  [[ "$server_name" =~ ^[[:print:]]{1,64}$ ]] || valheim_error "server_name must contain 1–64 printable ASCII characters."
  [[ "$world_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]] || valheim_error "world_name must contain 1–64 letters, digits, underscores or hyphens, starting with a letter or digit."
  [[ "$server_password" =~ ^[[:print:]]{5,64}$ ]] || valheim_error "server_password must contain 5–64 printable ASCII characters."
  [[ "$server_name" != *"$server_password"* ]] || valheim_error "server_name must not contain the server password."
  [[ "$public" =~ ^[01]$ && "$crossplay" =~ ^[01]$ && "$auto_update" =~ ^[01]$ ]] || valheim_error "public, crossplay and auto_update must each be 0 or 1."
  valheim_number "$port" 1024 65534 || valheim_error "port must be 1024–65534."
  valheim_number "$saveinterval" 60 86400 || valheim_error "saveinterval must be 60–86400 seconds."
  valheim_number "$world_backups" 1 100 || valheim_error "world_backups must be 1–100."
  valheim_number "$backupshort" 60 31536000 || valheim_error "backupshort must be 60–31536000 seconds."
  valheim_number "$backuplong" 60 31536000 || valheim_error "backuplong must be 60–31536000 seconds."
  valheim_number "$backup_retention" 1 365 || valheim_error "backup_retention must be 1–365 archives."
}

valheim_load_config() {
  local config=/etc/valheim/server.conf key value line seen='|'
  [[ -d /etc/valheim && ! -L /etc/valheim ]] || valheim_error "Configuration directory must not be a symbolic link."
  [[ "$(stat -c %u /etc/valheim)" == 0 && "$(stat -c %a /etc/valheim)" == 750 ]] || valheim_error "Configuration directory must be root-owned with mode 0750."
  [[ -f "$config" && ! -L "$config" ]] || valheim_error "Missing regular /etc/valheim/server.conf; see README recovery instructions."
  [[ "$(stat -c %u "$config")" == 0 && "$(stat -c %a "$config")" == 640 ]] || valheim_error "server.conf must be root-owned with mode 0640."
  [[ "$(stat -c %g "$config")" == "$(id -g valheim)" ]] || valheim_error "server.conf must belong to group valheim."
  server_name='' world_name='' server_password='' public='' crossplay='' port=''
  saveinterval='' world_backups='' backupshort='' backuplong='' backup_retention='' auto_update=''
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || valheim_error "Configuration lines must use key=value (without shell quoting)."
    key="${line%%=*}" value="${line#*=}"
    [[ "$seen" != *"|$key|"* ]] || valheim_error "Duplicate configuration key."
    seen+="$key|"
    case "$key" in
      server_name) server_name="$value" ;;
      world_name) world_name="$value" ;;
      server_password) server_password="$value" ;;
      public) public="$value" ;;
      crossplay) crossplay="$value" ;;
      port) port="$value" ;;
      saveinterval) saveinterval="$value" ;;
      world_backups) world_backups="$value" ;;
      backupshort) backupshort="$value" ;;
      backuplong) backuplong="$value" ;;
      backup_retention) backup_retention="$value" ;;
      auto_update) auto_update="$value" ;;
      *) valheim_error "Unknown configuration key; see README for supported keys." ;;
    esac
  done <"$config"
  valheim_validate
}
VALHEIM_CONFIG
chown root:root /usr/local/libexec/valheim-config
chmod 0644 /usr/local/libexec/valheim-config

if [[ ! -e /etc/valheim/server.conf ]]; then
  # Passwords must never enter Core's traced commands or error reports.
  set +x
  var_valheim_name=$(prompt_input_required "Valheim server name:" "Valheim Community" 60 "var_valheim_name")
  var_valheim_world=$(prompt_input_required "Valheim world name:" "Dedicated" 60 "var_valheim_world")
  var_valheim_public=$(prompt_input_required "Valheim visibility (0 private, 1 listed):" "1" 60 "var_valheim_public")
  var_valheim_crossplay=$(prompt_input_required "Valheim crossplay (0 off, 1 on):" "1" 60 "var_valheim_crossplay")
  if [[ -z "${var_valheim_password:-}" ]]; then
    var_valheim_password=$(prompt_password "Valheim password (empty generates one):" "$(random_password 24)" 60 5)
  fi
  # Validate before writing; never interpolate user input into executable code.
  source /usr/local/libexec/valheim-config
  server_name="$var_valheim_name" world_name="$var_valheim_world" server_password="$var_valheim_password"
  public="$var_valheim_public" crossplay="$var_valheim_crossplay" port="${var_valheim_port:-2456}"
  saveinterval="${var_valheim_saveinterval:-1800}" world_backups="${var_valheim_backups:-6}"
  backupshort="${var_valheim_backupshort:-7200}" backuplong="${var_valheim_backuplong:-43200}"
  backup_retention="${var_valheim_retention:-14}" auto_update="${var_valheim_autoupdate:-0}"
  valheim_validate
  umask 0077
  cat <<EOF >/etc/valheim/server.conf
server_name=$server_name
world_name=$world_name
server_password=$server_password
public=$public
crossplay=$crossplay
port=$port
saveinterval=$saveinterval
world_backups=$world_backups
backupshort=$backupshort
backuplong=$backuplong
backup_retention=$backup_retention
auto_update=$auto_update
EOF
  chown root:valheim /etc/valheim/server.conf
  chmod 0640 /etc/valheim/server.conf
  unset var_valheim_password server_password
  umask 0022
fi
source /usr/local/libexec/valheim-config
valheim_load_config
unset server_password

msg_info "Installing Valheim dependencies"
$STD apt install -y lib32gcc-s1 lib32stdc++6 libatomic1 libpulse0 libpulse-dev
msg_ok "Installed Valheim dependencies"

if [[ ! -x /opt/steamcmd/steamcmd.sh ]]; then
  curl_with_retry "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" "/opt/steamcmd/steamcmd_linux.tar.gz"
  chown valheim:valheim /opt/steamcmd/steamcmd_linux.tar.gz
  $STD runuser -u valheim -- tar -xzf /opt/steamcmd/steamcmd_linux.tar.gz -C /opt/steamcmd
  rm /opt/steamcmd/steamcmd_linux.tar.gz
fi

cat <<'VALHEIM_LAUNCH' >/usr/local/libexec/valheim-launch
#!/usr/bin/env bash
# shellcheck disable=SC2154 # valheim_load_config initializes the validated values.
set +x
set -euo pipefail
source /usr/local/libexec/valheim-config
valheim_load_config
[[ ! -e /etc/valheim/maintenance ]] || valheim_error "Maintenance in progress; use valheim start after recovery."
export HOME=/var/lib/valheim
export SteamAppId=892970
export LD_LIBRARY_PATH=/opt/valheim/linux64
cd /opt/valheim
args=(-nographics -batchmode -name "$server_name" -world "$world_name"
  -password "$server_password" -port "$port" -public "$public"
  -savedir /var/lib/valheim/saves -saveinterval "$saveinterval" -backups "$world_backups"
  -backupshort "$backupshort" -backuplong "$backuplong")
[[ "$crossplay" == 0 ]] || args+=(-crossplay)
exec ./valheim_server.x86_64 "${args[@]}"
VALHEIM_LAUNCH
chown root:root /usr/local/libexec/valheim-launch
chmod 0755 /usr/local/libexec/valheim-launch

cat <<'VALHEIM_CLI' >/usr/local/bin/valheim
#!/usr/bin/env bash
# shellcheck disable=SC2154 # backup_retention is supplied by valheim_load_config.
set +x
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 0077
source /usr/local/libexec/valheim-config

usage() {
  echo "Usage: valheim {update|restart|start|stop|status|logs|backup|restore ARCHIVE|check-config}"
}

[[ "$EUID" -eq 0 ]] || valheim_error "Run with root privileges (sudo valheim ...)."
command="${1:-}"
case "$command" in
  restore) [[ $# -eq 2 ]] || { usage >&2; exit 2; } ;;
  update|restart|start|stop|status|logs|backup|check-config) [[ $# -eq 1 ]] || { usage >&2; exit 2; } ;;
  *) usage >&2; exit 2 ;;
esac
case "$command" in
  status) exec systemctl --no-pager --full status valheim.service ;;
  logs) exec journalctl --unit=valheim.service --lines=100 --follow ;;
  stop) ;; # A broken configuration must never prevent a safe stop.
  *) valheim_load_config; unset server_password ;;
esac
[[ "$command" != check-config ]] || { echo "Valheim configuration is valid."; exit 0; }

install -d -o root -g root -m 0700 /run/valheim-management
exec 9>/run/valheim-management/operation.lock
flock -n 9 || valheim_error "Another Valheim operation holds the lock. Wait for it to finish."
[[ "$command" == stop || ! -e /etc/valheim/maintenance ]] || valheim_error "Interrupted maintenance detected. See docs/backup-restore.md before removing /etc/valheim/maintenance."

stop_server() {
  systemctl stop valheim.service || valheim_error "Shutdown failed; no backup or update attempted. Inspect valheim status and logs."
  [[ "$(systemctl show valheim.service --property=MainPID --value)" == 0 ]] || valheim_error "Valheim process remains alive; refusing to copy live worlds."
  [[ "$(systemctl show valheim.service --property=ActiveState --value)" == inactive ]] || valheim_error "Valheim did not become inactive; inspect the service before retrying."
}

start_server() {
  systemctl reset-failed valheim.service
  systemctl start valheim.service
  sleep 3
  systemctl is-active --quiet valheim.service || valheim_error "Valheim exited after starting. Inspect valheim logs; backups are preserved."
  echo "Valheim process started. Check valheim logs for Game server connected before joining."
}

was_running=0
partial='' stage='' maintenance=0 phase=preflight
cleanup() {
  local result=$?
  trap - EXIT
  [[ -z "$partial" ]] || rm -f -- "$partial"
  if [[ -n "$stage" ]]; then
    if [[ "$result" == 0 ]]; then
      rm -rf -- "$stage"
    else
      echo "Restore staging preserved for recovery: $stage" >&2
    fi
  fi
  if [[ "$maintenance" == 1 ]]; then
    if [[ "$result" == 0 || "$phase" == backup ]]; then
      rm -f /etc/valheim/maintenance
      if [[ "$was_running" == 1 ]]; then
        start_server || result=1
      fi
    else
      echo "Valheim maintenance failed during $phase. Server remains stopped; backups are in /var/backups/valheim." >&2
      echo "Inspect valheim logs and docs/backup-restore.md; /etc/valheim/maintenance blocks unsafe starts." >&2
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backup_worlds() {
  local stamp sequence=1 suffix
  [[ -d /var/lib/valheim/saves && ! -L /var/lib/valheim/saves ]] || valheim_error "Missing regular saves directory."
  partial=$(mktemp /var/backups/valheim/.partial.XXXXXX)
  tar --create --gzip --file "$partial" --directory / etc/valheim/server.conf var/lib/valheim/saves
  tar --list --gzip --file "$partial" >/dev/null
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  while :; do
    printf -v suffix '%06d' "$sequence"
    archive="/var/backups/valheim/valheim-$stamp-$suffix.tar.gz"
    [[ -e "$archive" ]] || break
    sequence=$((sequence + 1))
  done
  mv -- "$partial" "$archive"
  partial=''
  (cd /var/backups/valheim; sha256sum "${archive##*/}" >"${archive##*/}.sha256")
  echo "Backup: $archive"
}

prune_backups() {
  local archives=() item count
  shopt -s nullglob
  # ISO timestamps sort chronologically; no parsing of user-controlled ls output.
  for item in /var/backups/valheim/valheim-*.tar.gz; do
    [[ ! -L "$item" && -f "$item.sha256" && ! -L "$item.sha256" ]] || continue
    archives+=("$item")
  done
  count=${#archives[@]}
  for item in "${archives[@]}"; do
    (( count > backup_retention )) || break
    rm -- "$item" "$item.sha256"
    count=$((count - 1))
  done
}

case "$command" in
  start) start_server; exit ;;
  stop) stop_server; exit ;;
  restart) stop_server; start_server; exit ;;
esac

[[ -d /var/backups/valheim && ! -L /var/backups/valheim ]] || valheim_error "Missing regular backup directory."
[[ "$(stat -c %u /var/backups/valheim)" == 0 && "$(stat -c %a /var/backups/valheim)" == 700 ]] || valheim_error "Backup directory must be root-owned with mode 0700."
if [[ "$command" == update ]]; then
  [[ -x /opt/steamcmd/steamcmd.sh ]] || valheim_error "SteamCMD missing. See the recovery procedures for rebuilding in a fresh LXC."
fi
if [[ "$command" == restore ]]; then
  archive="$2"
  [[ "$archive" =~ ^/var/backups/valheim/valheim-[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9]+\.tar\.gz$ ]] || valheim_error "Restore requires an absolute archive path created by valheim backup."
  [[ -f "$archive" && ! -L "$archive" && -f "$archive.sha256" && ! -L "$archive.sha256" ]] || valheim_error "Archive and checksum must be regular files."
  (cd /var/backups/valheim; sha256sum --check --status "${archive##*/}.sha256") || valheim_error "Backup checksum failed; nothing has been stopped or restored."
fi

if systemctl is-active --quiet valheim.service; then
  was_running=1
else
  [[ "$(systemctl show valheim.service --property=ActiveState --value)" == inactive ]] || valheim_error "Service is transitioning or failed. Resolve valheim status before maintenance."
fi
# ConditionPathExists and the launcher both honor this root-owned inhibition file.
touch /etc/valheim/maintenance
maintenance=1
phase=shutdown
stop_server
phase=backup
backup_worlds
if [[ "$command" == update ]]; then
  phase=update
  echo "Updating the stable Valheim Steam distribution (896660)."
  steam_log=$(mktemp /var/backups/valheim/.steamcmd.XXXXXX)
  runuser -u valheim -- env HOME=/var/lib/valheim \
    /opt/steamcmd/steamcmd.sh +force_install_dir /opt/valheim \
    +login anonymous +app_update 896660 validate +quit >"$steam_log" 2>&1 9>&- || {
      echo "SteamCMD failed. Diagnostic output: $steam_log" >&2
      exit 1
    }
  grep -Eq "Success! App '896660' (fully installed|already up to date)\\." "$steam_log" || {
    echo "SteamCMD did not confirm success. Diagnostic output: $steam_log" >&2
    exit 1
  }
  rm -- "$steam_log"
  [[ -x /opt/valheim/valheim_server.x86_64 ]] || valheim_error "SteamCMD did not install the server executable."
elif [[ "$command" == restore ]]; then
  phase=restore
  # Never let root extract user-controlled archive entries or follow their links.
  restore_archive="$2"
  stage=$(mktemp -d /var/lib/valheim/.restore.XXXXXX)
  chown valheim:valheim "$stage"
  cat -- "$restore_archive" | runuser -u valheim -- tar --extract --gzip --file - \
    --directory "$stage" --no-same-owner --no-same-permissions var/lib/valheim/saves 9>&-
  restored="$stage/var/lib/valheim/saves"
  [[ -d "$restored" && ! -L "$restored" ]] || valheim_error "Archive contains no saves directory."
  [[ -z "$(find "$stage" -type l -print -quit)" ]] || valheim_error "Restore refuses symbolic links."
  [[ -z "$(find "$restored" ! -type d ! -type f -print -quit)" ]] || valheim_error "Restore refuses special files."
  mv /var/lib/valheim/saves "$stage/previous-saves"
  if ! mv "$restored" /var/lib/valheim/saves; then
    mv "$stage/previous-saves" /var/lib/valheim/saves
    valheim_error "Restore failed; previous saves were put back."
  fi
  echo "World saves restored; current server configuration was retained."
fi
phase=complete
prune_backups
VALHEIM_CLI
chown root:root /usr/local/bin/valheim
chmod 0755 /usr/local/bin/valheim

cat <<'VALHEIM_SERVICE' >/etc/systemd/system/valheim.service
[Unit]
Description=Valheim Dedicated Server
Wants=network-online.target
After=network-online.target
ConditionPathExists=!/etc/valheim/maintenance
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=valheim
Group=valheim
WorkingDirectory=/opt/valheim
ExecStart=/usr/local/libexec/valheim-launch
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
KillMode=control-group
TimeoutStopSec=180
SendSIGKILL=no
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/valheim /opt/valheim
CapabilityBoundingSet=
RestrictSUIDSGID=true
StandardOutput=journal
StandardError=journal
SyslogIdentifier=valheim

[Install]
WantedBy=multi-user.target
VALHEIM_SERVICE

cat <<'VALHEIM_AUTO_SERVICE' >/etc/systemd/system/valheim-update.service
[Unit]
Description=Back up and update Valheim
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecCondition=/usr/local/libexec/valheim-auto-update
ExecStart=/usr/local/bin/valheim update
TimeoutStartSec=infinity
UMask=0077
VALHEIM_AUTO_SERVICE

cat <<'VALHEIM_AUTO' >/usr/local/libexec/valheim-auto-update
#!/usr/bin/env bash
# shellcheck disable=SC2154 # auto_update is supplied by valheim_load_config.
set +x
set -euo pipefail
source /usr/local/libexec/valheim-config
valheim_load_config
[[ "$auto_update" == 1 ]]
VALHEIM_AUTO
chown root:root /usr/local/libexec/valheim-auto-update
chmod 0755 /usr/local/libexec/valheim-auto-update

cat <<'VALHEIM_TIMER' >/etc/systemd/system/valheim-update.timer
[Unit]
Description=Daily optional Valheim update

[Timer]
OnCalendar=*-*-* 05:00:00
RandomizedDelaySec=15m
Persistent=false
Unit=valheim-update.service

[Install]
WantedBy=timers.target
VALHEIM_TIMER

# Re-running setup preserves configuration and saves. CLI maintenance takes a backup.
msg_info "Installing or validating Valheim"
chown root:root /etc/systemd/system/valheim.service /etc/systemd/system/valheim-update.service /etc/systemd/system/valheim-update.timer
chmod 0644 /etc/systemd/system/valheim.service /etc/systemd/system/valheim-update.service /etc/systemd/system/valheim-update.timer
systemctl daemon-reload
flock -u 8
exec 8>&-
/usr/local/bin/valheim update
systemctl enable -q valheim.service
systemctl enable -q --now valheim-update.timer
/usr/local/bin/valheim start
msg_ok "Installed Valheim; check valheim logs for readiness"

motd_ssh
customize
cleanup_lxc
