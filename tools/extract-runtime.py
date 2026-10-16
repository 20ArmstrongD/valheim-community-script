#!/usr/bin/env python3
"""Extract literal runtime heredocs for linting/tests, without executing installation."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def extract():
    source = (ROOT / "install/valheim-install.sh").read_text()
    pattern = r"^cat <<'(VALHEIM_\w+)' >([^\n]+)\n(.*?)^\1$"
    return {path: body for _, path, body in re.findall(pattern, source, re.M | re.S)}


if __name__ == "__main__":
    output = pathlib.Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    for path, body in extract().items():
        (output / pathlib.Path(path).name).write_text(body)
