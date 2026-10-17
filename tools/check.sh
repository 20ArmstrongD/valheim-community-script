#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
output=$(mktemp -d)
trap 'rm -rf "$output"' EXIT
python3 tools/extract-runtime.py "$output"
# ShellCheck searches beside each script, including those extracted under /tmp.
cp .shellcheckrc "$output/.shellcheckrc"
bash -n ct/valheim.sh
bash -n install/valheim-install.sh
bash -n migration/install.sh
bash -n migration/cli-hooks.sh
for script in "$output"/*; do
  if [[ "$(head -n 1 "$script")" == '#!/usr/bin/env bash' ]]; then
    bash -n "$script"
  fi
done
python3 -m unittest discover -s tests -v
if ! command -v shellcheck >/dev/null; then
  echo "ShellCheck is required for the complete check. Install it and rerun tools/check.sh." >&2
  exit 1
fi
shellcheck ct/valheim.sh install/valheim-install.sh tools/check.sh migration/install.sh migration/cli-hooks.sh
shellcheck "$output/valheim-config" "$output/valheim-launch" "$output/valheim" "$output/valheim-auto-update"
