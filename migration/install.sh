#!/usr/bin/env bash
# Run from a reviewed checkout inside the destination LXC; never on the source VM.
set +x
set -euo pipefail
[[ "$EUID" -eq 0 && "$(systemd-detect-virt --container)" == lxc ]] || {
  echo "Install this optional add-on as root inside the destination LXC." >&2
  exit 1
}
[[ -x /usr/local/bin/valheim && -f /etc/valheim/server.conf ]] || {
  echo "Complete the normal Valheim installation first." >&2
  exit 1
}
cd "$(dirname "${BASH_SOURCE[0]}")"
apt install -y python3 openssh-client curl ca-certificates
install -d -o root -g root -m 0700 /run/valheim-management
exec 9>/run/valheim-management/operation.lock
flock -n 9 || { echo "Another Valheim operation is running." >&2; exit 1; }
install -o root -g root -m 0644 valheim_migrate.py /usr/local/libexec/valheim_migrate.py
python3 - <<'PY'
from pathlib import Path
import os
target = Path('/usr/local/bin/valheim')
text = target.read_text()
hook = 'if [[ -f /usr/local/libexec/valheim-migration-hooks ]]; then\n  source /usr/local/libexec/valheim-migration-hooks\nfi\n'
anchor = 'source /usr/local/libexec/valheim-config\n'
if hook not in text:
    if text.count(anchor) != 1:
        raise SystemExit('Unsupported/custom management CLI; no CLI changes made.')
    backup = Path('/usr/local/libexec/valheim-before-migration')
    if not backup.exists():
        backup.write_text(text)
        backup.chmod(0o755)
    temporary = target.with_name('.valheim.new')
    temporary.write_text(text.replace(anchor, anchor + hook, 1))
    temporary.chmod(0o755)
    os.replace(temporary, target)
PY
install -o root -g root -m 0644 cli-hooks.sh /usr/local/libexec/valheim-migration-hooks
echo "Optional migration tools installed. No migration was executed. Run: valheim migrate --help"
