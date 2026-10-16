#!/usr/bin/env python3
"""Test doubles only. No production commands or network operations are performed."""
import fcntl
import json
import os
import pathlib
import subprocess
import sys

root = pathlib.Path(os.environ["TEST_ROOT"])
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]


def event(value):
    with (root / "events").open("a") as stream:
        stream.write(value + "\n")


if command == "stat":
    path = pathlib.Path(args[-1])
    field = args[1]
    if field == "%u":
        print(os.environ.get("MOCK_OWNER", "0"))
    elif field == "%g":
        print(os.getgid())
    elif field == "%a":
        print(oct(path.stat().st_mode & 0o777)[2:])
elif command == "id":
    if args == ["valheim"]:
        sys.exit(0)
    print(os.getgid() if args[0] == "-g" else 222)
elif command == "getent":
    print(f"valheim:x:222:222::{root}/var/lib/valheim:/usr/sbin/nologin")
elif command == "dpkg":
    print("amd64")
elif command == "systemd-detect-virt":
    print("lxc")
elif command in ("apt", "useradd"):
    event(command + " " + " ".join(args))
elif command == "install":
    clean_args = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("-o", "-g"):
            skip = True
        else:
            clean_args.append(arg)
    sys.exit(subprocess.call(["/usr/bin/install", *clean_args]))
elif command == "chown":
    pass
elif command == "flock":
    try:
        fcntl.flock(int(args[-1]), fcntl.LOCK_UN if "-u" in args else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
elif command == "sleep":
    pass
elif command == "systemctl":
    event("systemctl " + " ".join(args))
    state_path = root / "state"
    state = json.loads(state_path.read_text())
    if args[0] == "stop":
        if os.environ.get("FAIL_STOP"):
            sys.exit(1)
        state["active"] = "inactive"
        state["pid"] = 0
    elif args[0] == "start":
        state["active"] = "active"
        state["pid"] = 123
        if os.environ.get("FAIL_START"):
            state["active"] = "failed"
            state["pid"] = 0
    elif args[0] == "show":
        print(state["pid"] if "--property=MainPID" in args else state["active"])
    elif args[0] == "is-active":
        sys.exit(0 if state["active"] == "active" else 3)
    elif "status" in args:
        print(state["active"])
        sys.exit(0 if state["active"] == "active" else 3)
    state_path.write_text(json.dumps(state))
elif command == "runuser":
    assert args[:3] == ["-u", "valheim", "--"]
    event("runuser " + " ".join(args[3:]))
    sys.exit(subprocess.call(args[3:]))
elif command == "steamcmd.sh":
    event("steamcmd " + " ".join(args))
    if os.environ.get("FAIL_STEAM"):
        print("Steam unavailable")
        sys.exit(8)
    if not os.environ.get("NO_STEAM_CONFIRMATION"):
        print("Success! App '896660' fully installed.")
elif command == "tar":
    event("tar " + args[0])
    if os.environ.get("FAIL_BACKUP") and "--create" in args:
        sys.exit(2)
    if os.environ.get("FAIL_EXTRACT") and "--extract" in args:
        sys.exit(2)
    sys.exit(subprocess.call([os.environ["REAL_TAR"], *args]))
elif command == "journalctl":
    event("journalctl " + " ".join(args))
elif command == "valheim_server.x86_64":
    (root / "launch-args").write_text(json.dumps(args))
else:
    raise AssertionError(command)
