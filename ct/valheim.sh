#!/usr/bin/env bash
_CS_DEFAULT_URL="https://raw.githubusercontent.com/20ArmstrongD/valheim-community-script/main"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Copyright (c) 2026 Valheim Community Script contributors
# License: MIT
# Source: https://www.valheimgame.com/support/a-guide-to-dedicated-servers/
# shellcheck disable=SC2034 # Container defaults are consumed by Core.

APP="Valheim"
var_tags="${var_tags:-gaming}"
var_cpu="${var_cpu:-4}"
var_ram="${var_ram:-8192}"
var_disk="${var_disk:-32}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_unprivileged="${var_unprivileged:-1}"
var_arm64="${var_arm64:-no}" # SteamCMD and the Linux server require x86.
set +x
export var_valheim_name="${var_valheim_name:-}"
export var_valheim_world="${var_valheim_world:-}"
export var_valheim_password="${var_valheim_password:-}"
export var_valheim_public="${var_valheim_public:-}"
export var_valheim_crossplay="${var_valheim_crossplay:-}"
export var_valheim_port="${var_valheim_port:-2456}"
export var_valheim_saveinterval="${var_valheim_saveinterval:-1800}"
export var_valheim_backups="${var_valheim_backups:-6}"
export var_valheim_backupshort="${var_valheim_backupshort:-7200}"
export var_valheim_backuplong="${var_valheim_backuplong:-43200}"
export var_valheim_retention="${var_valheim_retention:-14}"
export var_valheim_autoupdate="${var_valheim_autoupdate:-0}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  if [[ ! -x /usr/local/bin/valheim || ! -d /opt/valheim ]]; then
    msg_error "No Valheim installation found. Use a fresh container for installation."
    exit 1
  fi
  msg_info "Updating Valheim with a world backup"
  /usr/local/bin/valheim update
  msg_ok "Updated Valheim"
  exit
}

start
build_container
description
msg_ok "Completed successfully!\n"
echo "Configure /etc/valheim/server.conf inside the container; manage with valheim."
echo "Check valheim logs for readiness and a crossplay join code."
