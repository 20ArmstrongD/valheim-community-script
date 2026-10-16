#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
output=$(mktemp -d)
trap 'rm -rf "$output"' EXIT
python3 tools/extract-runtime.py "$output"
bash -n ct/valheim.sh
bash -n install/valheim-install.sh
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
# Generated files live outside the repository, so config discovery cannot find it.
shellcheck --rcfile "$PWD/.shellcheckrc" ct/valheim.sh install/valheim-install.sh tools/check.sh
shellcheck --rcfile "$PWD/.shellcheckrc" "$output/valheim-config" "$output/valheim-launch" "$output/valheim" "$output/valheim-auto-update"
