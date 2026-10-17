#!/usr/bin/env python3
"""Opt-in, fail-closed Valheim migration. Never sources imported shell or units."""
import argparse
from contextlib import contextmanager
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

LIMIT = 32 * 1024**3
FILE_LIMIT = 100000
PACKAGE_LIMIT = 128 * 1024**2
CHUNKED_WORLD_VERSIONS = frozenset({40, 41, 42})
SOURCE_DEFAULTS = {"port": "2456", "public": "1", "crossplay": "0", "saveinterval": "1800",
                   "world_backups": "4", "backupshort": "7200", "backuplong": "43200"}
VALUES = {"combat": {"veryeasy", "easy", "hard", "veryhard"},
          "deathpenalty": {"casual", "veryeasy", "easy", "hard", "hardcore"},
          "resources": {"muchless", "less", "more", "muchmore", "most"},
          "raids": {"none", "muchless", "less", "more", "muchmore"},
          "portals": {"casual", "hard", "veryhard"}}
KEYS = {"nobuildcost", "playerevents", "passivemobs", "nomap"}
PRESETS = {"normal", "casual", "easy", "hard", "hardcore", "immersive", "hammer"}
FLAGS = {"-name": "server_name", "-world": "world_name", "-port": "port", "-public": "public",
         "-saveinterval": "saveinterval", "-backups": "world_backups",
         "-backupshort": "backupshort", "-backuplong": "backuplong"}


class MigrationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hash_stream(stream)


def hash_stream(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_replace(source, destination):
    source, destination = Path(source), Path(destination)
    os.replace(source, destination)
    sync_directory(source.parent)
    if destination.parent != source.parent:
        sync_directory(destination.parent)


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".new")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    durable_replace(temporary, path)


def regular(path):
    return Path(path).is_file() and not Path(path).is_symlink()


def relative(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and ".." not in path.parts and
            "\\" not in name and all(ord(c) >= 32 for c in name), "Unsafe archive path.")
    require(str(path) == name.rstrip("/"), "Non-canonical archive path.")
    return path


def static_value(raw, environment):
    require(not any(x in raw for x in ("`", "$(", ";", "\n")), "Dynamic shell syntax is not supported; supply reviewed settings JSON.")
    tokens = shlex.split(raw)
    require(len(tokens) == 1, "Ambiguous shell value; supply reviewed settings JSON.")
    value = tokens[0]
    if not raw.startswith("'"):
        def expand(match):
            name = match.group(1) or match.group(2)
            require(name in environment, "Unresolved startup variable; supply reviewed settings JSON.")
            return environment[name]
        value = re.sub(r"\$\{(\w+)\}|\$(\w+)", expand, value)
        require("$" not in value, "Unsupported shell expansion.")
    return value


def validate_settings(settings):
    require(isinstance(settings, dict), "Settings must be a JSON object.")
    require(set(settings) == {"config", "arguments"}, "Settings require config and arguments objects.")
    config, arguments = settings["config"], settings["arguments"]
    require(isinstance(config, dict) and isinstance(arguments, list), "Invalid settings types.")
    require(all(isinstance(x, str) for x in arguments), "Gameplay arguments must be strings.")
    require(set(config) <= set(FLAGS.values()) | {"crossplay"}, "Unsupported translated configuration key.")
    require(all(isinstance(v, str) and "\n" not in v and "\r" not in v and "\0" not in v for v in config.values()), "Invalid configuration value.")
    require("server_name" in config and "world_name" in config, "Source must explicitly identify server and selected world.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", config["world_name"]), "Unsupported world name.")
    require(len(arguments) <= 100, "Too many gameplay arguments.")
    settings["config"] = {**SOURCE_DEFAULTS, **config}
    require(1 <= len(config["server_name"]) <= 64 and all(32 <= ord(c) <= 126 for c in config["server_name"]), "Invalid source server name.")
    ranges = {"port": (1024, 65534), "saveinterval": (60, 86400), "world_backups": (1, 100),
              "backupshort": (60, 31536000), "backuplong": (60, 31536000)}
    for name, (minimum, maximum) in ranges.items():
        value = settings["config"][name]
        require(re.fullmatch(r"[1-9][0-9]{0,7}", value) and minimum <= int(value) <= maximum,
                "Translated numeric setting is outside supported limits.")
    require(all(settings["config"][name] in {"0", "1"} for name in ("public", "crossplay")), "Invalid source visibility/crossplay setting.")
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "-preset":
            require(index + 1 < len(arguments) and arguments[index + 1] in PRESETS, "Invalid preset.")
            index += 2
        elif token == "-modifier":
            require(index + 2 < len(arguments) and arguments[index + 1] in VALUES and
                    arguments[index + 2] in VALUES[arguments[index + 1]], "Invalid world modifier.")
            index += 3
        elif token == "-setkey":
            require(index + 1 < len(arguments) and arguments[index + 1] in KEYS, "Invalid world key.")
            index += 2
        else:
            raise MigrationError("Unrecognized gameplay argument; review source settings.")
    return settings


def parse_startup(text, unit=""):
    environment, commands = {}, []
    # Unit content is data only. EnvironmentFile and drop-in overrides need review.
    for line in unit.splitlines():
        if line.strip().startswith(("EnvironmentFile=", "ExecStartPre=", "ExecStartPost=")):
            raise MigrationError("Service has external settings/hooks; supply reviewed settings JSON.")
        if line.strip().startswith("Environment="):
            for assignment in shlex.split(line.strip().split("=", 1)[1]):
                name, sep, value = assignment.partition("=")
                require(sep and re.fullmatch(r"\w+", name), "Invalid unit environment.")
                require("%" not in value, "Systemd specifiers require reviewed settings JSON.")
                environment[name] = value
        if line.strip().startswith("ExecStart="):
            command = shlex.split(line.strip().split("=", 1)[1])
            require(len(command) == 1, "Unit passes additional startup arguments; supply reviewed settings JSON.")
    for line in text.replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assignment = re.fullmatch(r"(?:export\s+)?([A-Za-z_]\w*)=(.+)", line)
        if assignment:
            # These source bootstrap paths are replaced by fresh game/loader packages.
            if assignment[1] not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SteamAppId"} and not assignment[1].startswith("DOORSTOP_"):
                environment[assignment[1]] = static_value(assignment[2], environment)
        elif re.match(r"^(?:cd\s|ulimit\s)", line):
            require(not any(x in line for x in (";", "`", "$(", "&&", "||")), "Dynamic startup commands require review.")
        else:
            tokens = shlex.split(line, posix=False, comments=True)
            if tokens and tokens[0] == "exec":
                tokens.pop(0)
            if tokens and Path(tokens[0]).name == "start_game_bepinex.sh":
                tokens.pop(0)
            require(tokens and Path(tokens[0]).name == "valheim_server.x86_64",
                    "Unsupported startup script command; supply reviewed settings JSON instead of executing it.")
            commands.append([static_value(token, environment) for token in tokens[1:]])
    require(len(commands) == 1, "Expected one unambiguous Valheim invocation; supply reviewed settings JSON.")
    tokens, config, arguments, index = commands[0], {}, [], 0
    seen = set()
    while index < len(tokens):
        flag = tokens[index].lower()
        require(flag not in seen or flag in {"-modifier", "-setkey"}, "Duplicate startup flag requires review.")
        seen.add(flag)
        if flag in {"-batchmode", "-nographics"}:
            index += 1
        elif flag == "-crossplay":
            config["crossplay"] = "1"
            index += 1
        else:
            count = 2 if flag == "-modifier" else 1
            require(index + count < len(tokens), "Missing startup argument value.")
            values = tokens[index + 1:index + 1 + count]
            if flag in FLAGS:
                config[FLAGS[flag]] = values[0]
            elif flag in {"-preset", "-modifier", "-setkey"}:
                arguments.extend([flag, *[x.lower() for x in values]])
            else:
                require(flag in {"-password", "-savedir", "-logfile"}, "Unsupported startup flag; supply reviewed settings JSON.")
                # Source credentials and filesystem/log paths are deliberately discarded.
            index += count + 1
    return validate_settings({"config": config, "arguments": arguments})


def stopped(service):
    require(re.fullmatch(r"[A-Za-z0-9_-]+\.service", service), "Invalid source service name.")
    for field, expected in (("ActiveState", "inactive"), ("MainPID", "0")):
        result = subprocess.run(["systemctl", "show", service, f"--property={field}", "--value"],
                                check=True, capture_output=True, text=True)
        require(result.stdout.strip() == expected, "Source service must be stopped before snapshot; no automatic source shutdown is performed.")
    for entry in Path("/proc").glob("[0-9]*/comm"):
        try:
            name = entry.read_text().strip()
        except (OSError, UnicodeError):
            continue
        require(not name.startswith("valheim_server"), "A source game process is still running.")


def tree_files(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "Expected a real source directory.")
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Snapshot refuses symbolic links; review the source directory.")
        if path.is_dir():
            continue
        require(regular(path) and stat.S_ISREG(path.stat().st_mode), "Snapshot refuses special files.")
        yield path


def snapshot(args):
    output = Path(args.output).resolve()
    require(not output.exists(), "Output already exists; choose another archive name.")
    source = Path(args.game_dir).resolve()
    saves = Path(args.saves_dir).resolve()
    settings = validate_settings(json.loads(Path(args.settings).read_text())) if args.settings else parse_startup(
        Path(args.startup_script).read_text(), Path(args.unit_file).read_text() if args.unit_file else "")
    stopped(args.service)
    with tempfile.TemporaryDirectory(prefix="valheim-snapshot-") as temporary:
        stage = Path(temporary)
        inventory, total_size = {}, 0
        roots = {"saves": saves}
        for category, folder in (("bepinex-config", "config"), ("plugins", "plugins")):
            directory = source / "BepInEx" / folder
            if directory.exists():
                roots[category] = directory
        inputs = []
        for category, directory in roots.items():
            for path in tree_files(directory):
                name = str(PurePosixPath(category) / path.relative_to(directory).as_posix())
                relative(name)
                before = digest(path)
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                require(before == digest(destination) == digest(path), "Source changed during snapshot; stop writers and retry.")
                inventory[name] = {"sha256": before, "size": destination.stat().st_size}
                inputs.append((path, name))
                total_size += destination.stat().st_size
                require(total_size <= LIMIT and len(inventory) <= FILE_LIMIT,
                        "Source snapshot exceeds size/count limits.")
        require(inventory and len(inventory) <= FILE_LIMIT, "Empty or oversized snapshot.")
        write_json(stage / "settings.json", settings)
        inventory["settings.json"] = {"sha256": digest(stage / "settings.json"), "size": (stage / "settings.json").stat().st_size}
        stopped(args.service)
        require(all(digest(path) == inventory[name]["sha256"] for path, name in inputs), "Source changed before final snapshot verification.")
        require({str(PurePosixPath(category) / p.relative_to(folder).as_posix()) for category, folder in roots.items() for p in tree_files(folder)} ==
                set(inventory) - {"settings.json"}, "Source file set changed during snapshot.")
        stopped(args.service)
        write_json(stage / "manifest.json", {"schema": 1, "consistent": "stopped-service-double-hash", "files": inventory})
        partial = output.with_suffix(output.suffix + ".partial")
        try:
            with partial.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for path in tree_files(stage):
                    archive.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
            partial.chmod(0o600)
            os.link(partial, output)  # Exclusive publication: never overwrite a source file.
        finally:
            partial.unlink(missing_ok=True)
    checksum = digest(output)
    sidecar = output.with_name(output.name + ".sha256")
    with sidecar.open("x") as stream:
        stream.write(checksum + "\n")
    sidecar.chmod(0o600)
    print("Consistent migration snapshot created. Preserve its SHA-256 through a trusted channel:", checksum)


def validate_chunked_world(directory):
    """Validate generations and index references without interpreting/rebuilding saves.

    Binary layout reference: community-valheim-tools/fejdit,
    src/format/chunked.rs (ChunkMap, ChunkIndex and ChunkFile).
    """
    generations = {}
    for path in directory.iterdir():
        if path.name.startswith("_main."):
            match = re.fullmatch(r"_main\.(0|[1-9][0-9]{0,9})\.(db2|fwl2|chunks|ok)", path.name)
            require(match and path.is_file(), "Malformed chunked-world generation filename.")
            generation, extension = match.groups()
            require(int(generation) <= 0xffffffff, "Invalid chunked-world generation number.")
            generations.setdefault(generation, set()).add(extension)
        require(path.suffix not in {".db", ".fwl"}, "Mixed legacy files inside chunked world; review snapshot.")
    require(generations, "Selected chunked world has no snapshot metadata.")
    for generation, extensions in generations.items():
        require(extensions == {"db2", "fwl2", "chunks", "ok"},
                "Incomplete chunked-world generation; require matching .db2/.fwl2/.chunks/.ok files.")
        stem = directory / ("_main." + generation)
        def header(extension, length):
            with Path(str(stem) + extension).open("rb") as stream:
                data = stream.read(length)
            require(len(data) == length, "Truncated chunked-world metadata.")
            return data
        version = struct.unpack("<i", header(".db2", 4))[0]
        frame_size, metadata_version = struct.unpack("<ii", header(".fwl2", 8))
        require(version in CHUNKED_WORLD_VERSIONS and metadata_version == version and frame_size >= 4 and
                frame_size == Path(str(stem) + ".fwl2").stat().st_size - 4,
                "Unsupported or inconsistent chunked-world metadata version/length.")
        require(Path(str(stem) + ".ok").stat().st_size == 4 and
                struct.unpack("<i", header(".ok", 4))[0] == version,
                "Invalid chunked-world completion marker.")
        index = Path(str(stem) + ".chunks")
        require(10 <= index.stat().st_size <= 10 + 11 * FILE_LIMIT, "Invalid chunk index size.")
        data = index.read_bytes()
        # Exact framing rejects changed index layouts; never infer entries from file size alone.
        map_version, total, count = struct.unpack_from("<Hii", data)
        require(map_version == version and 0 <= count <= FILE_LIMIT and total >= 0 and
                len(data) == 10 + count * 11, "Malformed or unsupported chunk index.")
        seen, objects = set(), 0
        for offset in range(10, len(data), 11):
            coordinate, size, revision, number = struct.unpack_from("<HBIi", data, offset)
            x, y = coordinate & 255, coordinate >> 8
            require(size <= 3 and x < 64 and y < 64 and x % (1 << size) == y % (1 << size) == 0 and
                    (coordinate, size) not in seen and number >= 0, "Invalid or duplicate chunk index entry.")
            seen.add((coordinate, size))
            chunk = directory / f"{y:02x}_{x:02x}__{size}_{revision}.chunk"
            require(chunk.is_file() and chunk.stat().st_size >= 6, "Required world chunk is missing or truncated.")
            with chunk.open("rb") as stream:
                chunk_version, chunk_count = struct.unpack("<hi", stream.read(6))
            # Unchanged chunks may predate the main save; newer chunks cannot belong to it.
            require(chunk_version in CHUNKED_WORLD_VERSIONS and chunk_version <= version and chunk_count == number and
                    (number == 0 or chunk.stat().st_size > 6), "Chunk header disagrees with snapshot index.")
            objects += number
        require(objects == total, "Chunk index object totals disagree.")


def validate_selected_world(saves, world):
    # Exact names only: backup directories must never stand in for the live world.
    candidates = []
    for folder in ("worlds_local", "worlds", "."):
        parent = saves / folder
        legacy = [parent / (world + extension) for extension in (".db", ".fwl")]
        chunked = parent / world
        if chunked.exists() or any(path.exists() for path in legacy):
            candidates.append((chunked, legacy))
    require(len(candidates) == 1, "Selected world is missing or ambiguous across save roots; review settings.json.")
    chunked, legacy = candidates[0]
    if chunked.exists():
        require(chunked.is_dir() and not any(path.exists() for path in legacy),
                "Mixed legacy and chunked selected world; explicitly review the active source before capture.")
        validate_chunked_world(chunked)
    else:
        require(all(path.is_file() for path in legacy), "Selected world is missing a .db/.fwl pair.")


def unpack(archive, destination, expected):
    require(re.fullmatch(r"[0-9a-f]{64}", expected or ""), "An expected SHA-256 from a trusted channel is required.")
    require(regular(archive) and Path(archive).stat().st_size <= LIMIT, "Archive is missing, linked or too large.")
    require(digest(archive) == expected, "Archive SHA-256 mismatch; destination untouched.")
    seen, size = set(), 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = relative(member.name)
            require(member.isfile() and member.name not in seen, "Only unique regular files are accepted (no links/devices/directories).")
            require(path.parts[0] in {"saves", "bepinex-config", "plugins", "settings.json", "manifest.json"}, "Unexpected archive payload.")
            if path.parts[0] in {"settings.json", "manifest.json"}:
                require(len(path.parts) == 1 and member.size <= 1024**2, "Oversized/invalid snapshot metadata.")
            size += member.size
            require(size <= LIMIT and len(seen) < FILE_LIMIT, "Archive expansion limit exceeded.")
            seen.add(member.name)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with source.extractfile(member) as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            target.chmod(0o600)
    manifest = json.loads((destination / "manifest.json").read_text())
    require(isinstance(manifest, dict), "Invalid snapshot manifest.")
    require(manifest.get("schema") == 1 and manifest.get("consistent") == "stopped-service-double-hash", "No supported consistent-source snapshot attestation.")
    files = manifest.get("files", {})
    require(isinstance(files, dict), "Invalid snapshot inventory.")
    require(set(files) == seen - {"manifest.json"}, "Manifest inventory mismatch.")
    for name, record in files.items():
        path = destination / name
        require(record == {"sha256": digest(path), "size": path.stat().st_size}, "Manifest content mismatch.")
    settings = validate_settings(json.loads((destination / "settings.json").read_text()))
    validate_selected_world(destination / "saves", settings["config"]["world_name"])
    return settings


def managed_dll(path):
    """Check PE/CLI structure without loading managed code. This is not a safety proof."""
    require(Path(path).stat().st_size <= 64 * 1024**2, "Plugin assembly is too large.")
    data = Path(path).read_bytes()
    require(len(data) >= 256 and data[:2] == b"MZ", "Plugin is not a PE/CLI assembly.")
    offset = struct.unpack_from("<I", data, 60)[0]
    require(offset + 256 <= len(data) and data[offset:offset + 4] == b"PE\0\0", "Invalid PE header.")
    optional = offset + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    require(magic in (0x10B, 0x20B), "Unsupported PE format.")
    directory = optional + (96 if magic == 0x10B else 112) + 14 * 8
    require(directory + 8 <= len(data), "Truncated CLI directory.")
    rva, size = struct.unpack_from("<II", data, directory)
    require(rva and size >= 72, "Plugin has no CLR metadata directory.")


def package_zip(path, expected, destination):
    require(digest(path) == expected, "Package SHA-256 mismatch.")
    seen, total = set(), 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = str(relative(member.filename))
            require(name not in seen, "Duplicate package entry.")
            seen.add(name)
            mode = member.external_attr >> 16
            require(not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode) or stat.S_ISREG(mode) or stat.S_ISDIR(mode)), "Package contains a link or special file.")
            total += member.file_size
            require(total <= 512 * 1024**2 and len(seen) <= 10000, "Package expansion limit exceeded.")
            target = destination / name
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
    return json.loads((destination / "manifest.json").read_text())


def package_identity(package):
    require(re.fullmatch(r"[A-Za-z0-9_]+-[A-Za-z0-9_]+-[0-9]+\.[0-9]+\.[0-9]+", package.get("id", "")), "Invalid pinned Thunderstore package ID.")
    require(re.fullmatch(r"[0-9a-f]{64}", package.get("sha256", "")), "Pinned package SHA-256 required.")
    return package["id"].split("-")


def download_package(package, target):
    owner, name, version = package_identity(package)
    url = f"https://thunderstore.io/package/download/{owner}/{name}/{version}/"
    curl = shutil.which("curl")
    require(curl, f"stage=mod-download package={package['id']}: curl unavailable; install curl or use --mod-package-dir.")
    require(not target.exists(), "Package output already exists.")
    log = target.with_suffix(".download.log")
    partial = target.with_suffix(".partial")
    # Disable user curl configuration; no shell, credentials, headers or insecure TLS options.
    command = [curl, "--disable", "--silent", "--show-error", "--fail", "--location",
               "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
               "--connect-timeout", "15", "--max-time", "120", "--max-filesize", str(PACKAGE_LIMIT),
               "--write-out", "%{stderr}\nHTTP_STATUS=%{http_code}\n", "--url", url]
    try:
        with log.open("xb") as diagnostics:
            log.chmod(0o600)
            for attempt in range(3):
                diagnostics.write(f"stage=mod-download package={package['id']} url={url} attempt={attempt + 1}\n".encode())
                diagnostics.flush()
                attempt_offset = diagnostics.tell()
                with partial.open("wb") as writer:
                    partial.chmod(0o600)
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=diagnostics)
                    try:
                        total = 0
                        while chunk := process.stdout.read(64 * 1024):
                            total += len(chunk)
                            require(total <= PACKAGE_LIMIT, f"stage=mod-download package={package['id']}: package exceeds size limit; log={log}")
                            writer.write(chunk)
                        code = process.wait(timeout=135)
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                        process.stdout.close()
                statuses = re.findall(r"HTTP_STATUS=([0-9]{3})", log.read_bytes()[attempt_offset:].decode(errors="replace"))
                status = statuses[-1] if statuses else "unknown"
                if code == 0 and status.startswith("2"):
                    require(digest(partial) == package["sha256"],
                            f"stage=mod-download package={package['id']} url={url} HTTP={status}: SHA-256 mismatch; log={log}")
                    durable_replace(partial, target)
                    return
                if attempt == 2 or status in {"403", "404"} or code not in {5, 6, 7, 18, 22, 28, 52, 55, 56}:
                    raise MigrationError(f"stage=mod-download package={package['id']} url={url} HTTP={status} curl_exit={code}; log={log}. Recover transaction before retry.")
                time.sleep(attempt + 1)
    finally:
        partial.unlink(missing_ok=True)


def local_package_downloader(directory):
    directory = Path(directory)
    require(directory.is_absolute(), "Local package directory must be absolute.")
    require(directory.is_dir() and all(not p.is_symlink() for p in [directory, *directory.parents]),
            "Local package directory must be a real directory without symlink components.")

    def copy(package, target):
        package_identity(package)
        source = directory / (package["id"] + ".zip")
        # Open relative to a pinned directory descriptor; never write into the source.
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            with os.fdopen(descriptor, "rb") as reader:
                info = os.fstat(reader.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_size <= PACKAGE_LIMIT, "Local package must be a bounded regular file.")
                with target.open("xb") as writer:
                    target.chmod(0o600)
                    total = 0
                    while chunk := reader.read(64 * 1024):
                        total += len(chunk)
                        require(total <= PACKAGE_LIMIT, "Local package exceeds size limit.")
                        writer.write(chunk)
            require(digest(target) == package["sha256"], f"stage=local-package package={package['id']}: SHA-256 mismatch.")
        except OSError as error:
            raise MigrationError(f"stage=local-package package={package['id']}: missing, linked or unreadable package; no network fallback.") from error
        finally:
            os.close(directory_fd)
    return copy


def mod_launcher_environment(game):
    libraries = list(game.rglob("*doorstop*x64*.so"))
    require(len(libraries) == 1, "Ambiguous Linux x64 Doorstop library selection.")
    library = "./" + libraries[0].relative_to(game).as_posix()
    directory = "./" + libraries[0].parent.relative_to(game).as_posix()
    return ("export DOORSTOP_ENABLED=1\n"
            "export DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll\n"
            f"export LD_PRELOAD={shlex.quote(library)}\n"
            f'export LD_LIBRARY_PATH={shlex.quote(directory)}:"${{LD_LIBRARY_PATH:-}}"\n')


def prepare_mods(stage, source, plan, build_id, downloader=download_package):
    plugins = list((source / "plugins").rglob("*")) if (source / "plugins").exists() else []
    has_mods = bool(plugins or (source / "bepinex-config").exists())
    require(not has_mods or plan, "Modded source requires a reviewed mod plan; imported binaries remain quarantined.")
    if not has_mods and not plan:
        return False
    require(plan["tested_valheim_build_id"] == build_id and plan.get("compatibility_confirmed") is True,
            "Mod plan must explicitly confirm compatibility with the fresh Steam build ID.")
    loader = plan["bepinex"]
    require(loader["id"].startswith("denikson-BepInExPack_Valheim-5."), "Use the Valheim-specific BepInEx 5 package.")
    packages = [loader, *plan.get("plugins", [])]
    require(len({p["id"] for p in packages}) == len(packages), "Duplicate mod package.")
    require(all(p.get("reviewed") is True for p in packages), "Every downloaded code package requires explicit review approval.")
    roots = []
    for index, package in enumerate(packages):
        archive = stage.parent / f"package-{index}.zip"
        package_identity(package)
        downloader(package, archive)
        folder = stage.parent / f"package-{index}"
        folder.mkdir()
        manifest = package_zip(archive, package["sha256"], folder)
        owner, name, version = package["id"].split("-")
        require(manifest.get("name") == name and manifest.get("version_number") == version, "Package manifest identity mismatch.")
        require(set(manifest.get("dependencies", [])) <= {p["id"] for p in packages}, "Unresolved or different-version plugin dependency.")
        roots.append(folder)
    loader_root = roots[0] / "BepInExPack_Valheim"
    for required in ("start_game_bepinex.sh", "BepInEx/core/BepInEx.Preloader.dll"):
        require(regular(loader_root / required), "Loader package layout is unsupported.")
    libraries = list(loader_root.rglob("*doorstop*x64*.so"))
    require(libraries, "Loader lacks a Linux x64 Doorstop library.")
    for library in libraries:
        header = library.read_bytes()[:20]
        require(len(header) == 20 and header[:5] == b"\x7fELF\x02" and header[18:20] == b"\x3e\0", "Loader must contain a Linux x86-64 Doorstop library.")
    managed_dll(loader_root / "BepInEx/core/BepInEx.Preloader.dll")
    shutil.copytree(loader_root, stage, dirs_exist_ok=True)
    # Never transfer source patchers/core/loader configuration into the executable tree.
    enabled = {}
    for package, folder in zip(packages[1:], roots[1:]):
        entries = package.get("assemblies", [])
        require(entries, "Each plugin package needs explicit assembly entries.")
        for entry in entries:
            name = str(relative(entry["path"]))
            require(name.lower().endswith(".dll"), "Only managed plugin assemblies may be enabled.")
            binary = folder / name
            managed_dll(binary)
            require(digest(binary) == entry["sha256"] and entry["source_sha256"] in {digest(p) for p in plugins if regular(p)},
                    "Plugin approval does not match an inventoried source binary and fresh package assembly.")
            output = stage / "BepInEx/plugins" / Path(name).name
            require(output.name not in enabled and not output.exists(), "Conflicting plugin assembly names.")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(binary, output)
            enabled[output.name] = digest(output)
    if (source / "bepinex-config").exists():
        for path in tree_files(source / "bepinex-config"):
            name = path.relative_to(source / "bepinex-config")
            if name.name.lower() == "bepinex.cfg":
                continue
            require(path.suffix.lower() in {".cfg", ".json", ".yml", ".yaml", ".txt"}, "Unrecognized plugin configuration format; original remains quarantined.")
            require(path.stat().st_size <= 4 * 1024**2 and b"\0" not in path.read_bytes(), "Configuration is not bounded text.")
            destination = stage / "BepInEx/config" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    (stage / "start_game_bepinex.sh").chmod(0o750)
    return True


class Destination:
    """All production paths are fixed; tests instantiate this API with a temporary root."""
    def __init__(self, root=Path("/")):
        self.root = Path(root)
        self.state = self.path("var/lib/valheim-migration")
        self.cli = str(self.path("usr/local/bin/valheim"))

    def path(self, name):
        return self.root / name.lstrip("/")

    def run(self, args, **kwargs):
        return subprocess.run(args, check=True, **kwargs)

    def stopped(self):
        stopped("valheim.service")

    def ownership(self, path, game=False):
        account = pwd.getpwnam("valheim")
        for entry in [path, *path.rglob("*")] if path.is_dir() else [path]:
            require(not entry.is_symlink(), "Staged payload contains symbolic links.")
            os.chown(entry, account.pw_uid if game else 0, account.pw_gid if game or path.name == "server.conf" else 0)
            entry.chmod(0o750 if entry.is_dir() or entry.name in {"valheim_server.x86_64", "start_game_bepinex.sh", "valheim-launch"} else (0o640 if entry.name == "server.conf" else 0o600))

    def fresh_game(self, target):
        target.mkdir()
        self.ownership(target, True)
        log = target.parent / "steamcmd.log"
        with log.open("wb") as stream:
            self.run(["runuser", "-u", "valheim", "--", "env", "HOME=/var/lib/valheim", "/opt/steamcmd/steamcmd.sh",
                      "+force_install_dir", str(target), "+login", "anonymous", "+app_update", "896660", "validate", "+quit"],
                     stdout=stream, stderr=subprocess.STDOUT)
        require(re.search(r"Success! App '896660' (fully installed|already up to date)\.", log.read_text(errors="replace")), "SteamCMD did not confirm installation; see protected transaction log.")
        require(regular(target / "valheim_server.x86_64"), "Missing fresh game binary.")
        manifest = target / "steamapps/appmanifest_896660.acf"
        match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text())
        require(match, "Cannot determine fresh game build ID.")
        return match[1]

    def validate_config(self, values):
        self.run(["bash", "-c", "set +x; source /usr/local/libexec/valheim-config; valheim_validate"],
                 env={**os.environ, **values}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @contextmanager
    def migration_operation(self):
        # Serialize staging and recovery as well as activation; recursive rollback reuses the lease.
        if getattr(self, "_migration_busy", False):
            yield
            return
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not self.state.is_symlink() and self.state.stat().st_uid == os.geteuid() and
                self.state.stat().st_mode & 0o777 == 0o700, "Migration state must be root-owned mode 0700.")
        descriptor = os.open(self.state / ".operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as mutex:
            try:
                fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise MigrationError("Another migration/recovery is running; no changes made.") from error
            self._migration_busy = True
            try:
                yield
            finally:
                self._migration_busy = False

    def rollback(self, transaction):
        with self.migration_operation():
            return self._recover(transaction)

    def _recover(self, transaction):
        journal_path = transaction / "journal.json"
        require(transaction.parent == self.state and re.fullmatch(r"[0-9a-f]{24}", transaction.name) and
                not transaction.is_symlink() and not journal_path.is_symlink(), "Unsafe transaction location.")
        require(all(not (transaction / name).is_symlink() for name in ("old", "after")), "Unsafe recovery directories.")
        journal = json.loads(journal_path.read_text())
        allowed = {"game": "opt/valheim", "saves": "var/lib/valheim/saves", "server.conf": "etc/valheim/server.conf",
                   "valheim-launch": "usr/local/libexec/valheim-launch", "mod-lock.json": "etc/valheim/mod-lock.json"}
        require(journal.get("status") in {"staging", "staging-failed", "activating", "committed", "rolled-back"} and
                isinstance(journal.get("slots"), list) and len(journal["slots"]) <= len(allowed), "Invalid recovery journal.")
        require(all(isinstance(slot, dict) and slot.get("name") in allowed and allowed[slot["name"]] == slot.get("target") and
                    type(slot.get("had_old")) is bool and slot.get("intent") is True for slot in journal["slots"]) and
                len({slot["name"] for slot in journal["slots"]}) == len(journal["slots"]), "Unsafe recovery slots.")
        if journal["status"] in {"staging", "staging-failed"}:
            require(not journal["slots"] and not journal.get("fingerprints") and
                    not any((transaction / "old").iterdir()) and not self.path("etc/valheim/maintenance").exists(),
                    "Staging recovery found activation evidence; manual inspection required.")
            lock_directory = self.path("run/valheim-management")
            lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (lock_directory / "operation.lock").open("a") as mutex:
                fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
                journal["status"] = "rolled-back"
                write_json(journal_path, journal)
            print("Staging-only transaction recovered. No services or production paths changed; logs and staged files retained.")
            return
        if journal["status"] == "rolled-back":
            print("Transaction already rolled back; no changes made.")
            return
        require(all(not (transaction / "old" / slot["name"]).is_symlink() and
                    not self.path(slot["target"]).is_symlink() for slot in journal["slots"]), "Unsafe recovery path.")
        self.run([self.cli, "stop"])
        lock_directory = self.path("run/valheim-management")
        lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (lock_directory / "operation.lock").open("a") as mutex:
            fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._rollback_locked(transaction, journal)

    def _rollback_locked(self, transaction, journal):
        self.stopped()
        self.path("etc/valheim/maintenance").touch(mode=0o600)
        after = transaction / "after"
        after.mkdir(exist_ok=True)
        for slot in reversed(journal["slots"]):
            destination, old = self.path(slot["target"]), transaction / "old" / slot["name"]
            if old.exists():
                if destination.exists():
                    require(not (after / slot["name"]).exists(), "Conflicting retained rollback data; inspect transaction manually.")
                    durable_replace(destination, after / slot["name"])
                durable_replace(old, destination)
            elif not slot["had_old"] and slot["intent"]:
                if destination.exists():
                    require(not (after / slot["name"]).exists(), "Conflicting rollback data.")
                    durable_replace(destination, after / slot["name"])
        journal["status"] = "rolled-back"
        write_json(transaction / "journal.json", journal)
        self.path("etc/valheim/maintenance").unlink(missing_ok=True)
        print("Previous destination restored and left stopped. Review before valheim start. Original source VM was not modified.")

    def apply(self, source, settings, archive_hash, plan, password, start=False, mod_package_dir=None):
        with self.migration_operation():
            return self._apply(source, settings, archive_hash, plan, password, start, mod_package_dir)

    def _apply(self, source, settings, archive_hash, plan, password, start=False, mod_package_dir=None):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not self.state.is_symlink() and self.state.stat().st_uid == os.geteuid() and self.state.stat().st_mode & 0o777 == 0o700, "Migration state must be root-owned mode 0700.")
        base_identity = hashlib.sha256((archive_hash + json.dumps(plan, sort_keys=True)).encode()).hexdigest()[:24]
        identity, attempt = base_identity, 0
        transaction = self.state / identity
        journal_path = transaction / "journal.json"
        while journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if journal["status"] == "rolled-back":
                attempt += 1
                require(attempt <= 100, "Too many retained retries; review migration state before continuing.")
                identity = hashlib.sha256(f"{base_identity}:{attempt}".encode()).hexdigest()[:24]
                transaction = self.state / identity
                journal_path = transaction / "journal.json"
                continue
            require(journal["status"] == "committed", f"Transaction {identity} needs rollback/recovery before retry.")
            require(not self.path("etc/valheim/maintenance").exists(), "Committed migration is inhibited; inspect transaction recovery before retry.")
            require(all(regular(self.path(name)) and digest(self.path(name)) == value for name, value in journal["fingerprints"].items()), "Destination configuration, executable code or mod lock drifted; review before another import.")
            print("This archive and mod plan are already applied; no changes made.")
            return identity
        transaction.mkdir(mode=0o700)
        print("Staging migration transaction:", identity)
        # Preserve every imported file, including disabled plugins and original loader config.
        old = transaction / "old"
        old.mkdir()
        journal = {"status": "staging", "slots": [], "fingerprints": {}, "archive_sha256": archive_hash, "stage": "steamcmd"}
        write_json(journal_path, journal)
        work = self.path("opt/.valheim-migration-" + identity)
        try:
            shutil.copytree(source, transaction / "quarantine")
            work.mkdir(mode=0o711)
            work.chmod(0o711)  # The private process umask must not block SteamCMD's account.
            game = work / "game"
            build = self.fresh_game(game)
            journal["stage"] = "mod-packages"
            write_json(journal_path, journal)
            downloader = local_package_downloader(mod_package_dir) if mod_package_dir is not None else download_package
            modded = prepare_mods(game, source, plan, build, downloader)
            journal["stage"] = "configuration"
            write_json(journal_path, journal)
            saves = transaction / "saves"
            shutil.copytree(source / "saves", saves)
            values = dict(line.split("=", 1) for line in self.path("etc/valheim/server.conf").read_text().splitlines() if line and not line.startswith("#"))
            values.update(settings["config"])
            values.update(server_password=password, auto_update="0")
            self.validate_config(values)
            config = transaction / "server.conf"
            config.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            launcher = transaction / "valheim-launch"
            original = self.path("usr/local/libexec/valheim-launch").read_text()
            require(original.count('exec ./valheim_server.x86_64 "${args[@]}"') == 1, "Unsupported/custom launcher; retain it and review its settings explicitly.")
            require(not re.search(r"-(?:preset|modifier|setkey)\b", original), "Destination launcher already has custom world rules; preserve it and reset/review those rules before migration.")
            extra = "args+=(" + " ".join(shlex.quote(x) for x in settings["arguments"]) + ")\n" if settings["arguments"] else ""
            command = (mod_launcher_environment(game) if modded else "") + 'exec ./valheim_server.x86_64 "${args[@]}"'
            launcher.write_text(original.replace('exec ./valheim_server.x86_64 "${args[@]}"', extra + command))
            self.run(["bash", "-n", str(launcher)])
            lock = transaction / "mod-lock.json"
            write_json(lock, {"archive_sha256": archive_hash, "build_id": build, "modded": modded,
                              "plan": plan, "files": {str(p.relative_to(game)): digest(p) for p in tree_files(game) if "BepInEx" in p.parts or p.name == "start_game_bepinex.sh"}})
            self.ownership(game, True)
            self.ownership(saves, True)
            self.ownership(config)
            self.ownership(launcher)
            self.ownership(lock)
            require(all(self.path(name).parent.stat().st_dev == transaction.stat().st_dev for name in
                        ("opt/valheim", "var/lib/valheim/saves", "etc/valheim/server.conf", "usr/local/libexec/valheim-launch")),
                    "Atomic migration requires destination paths on the same filesystem; bind mounts need an operator-led migration.")
            require(not any(os.path.ismount(self.path(name)) for name in ("opt/valheim", "var/lib/valheim/saves")),
                    "Migration refuses destination game/save mount points.")
            journal["stage"] = "destination-backup-stop"
            write_json(journal_path, journal)
            # CLI owns consistent destination backups and graceful service control.
            self.run([self.cli, "backup"])
            self.run([self.cli, "stop"])
        except BaseException as error:
            journal["status"] = "staging-failed"
            journal["error"] = str(error) if isinstance(error, MigrationError) else type(error).__name__
            write_json(journal_path, journal)
            for log in work.glob("*.log"):
                shutil.copyfile(log, transaction / log.name)
                (transaction / log.name).chmod(0o600)
            raise MigrationError(f"Transaction {identity} failed at stage={journal['stage']}: {journal['error']}. Logs retained in {transaction}; run valheim migrate rollback {identity} before retry.") from error
        lock_directory = self.path("run/valheim-management")
        lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (lock_directory / "operation.lock").open("a") as mutex:
            try:
                fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise MigrationError("Another management operation is running; destination was not replaced.") from error
            self.stopped()
            marker = self.path("etc/valheim/maintenance")
            require(not marker.exists(), "Existing maintenance inhibition requires recovery first.")
            marker.touch(mode=0o600)
            journal["status"] = "activating"
            slots = [(game, "opt/valheim"), (saves, "var/lib/valheim/saves"),
                     (config, "etc/valheim/server.conf"), (launcher, "usr/local/libexec/valheim-launch"),
                     (lock, "etc/valheim/mod-lock.json")]
            try:
                for staged, target in slots:
                    destination = self.path(target)
                    slot = {"name": staged.name, "target": target, "had_old": destination.exists(), "intent": True}
                    journal["slots"].append(slot)
                    write_json(journal_path, journal)
                    if destination.exists():
                        durable_replace(destination, old / staged.name)
                    durable_replace(staged, destination)
                self.run([self.cli, "check-config"])
                journal["fingerprints"] = {name: digest(self.path(name)) for name in
                                           ("etc/valheim/server.conf", "usr/local/libexec/valheim-launch", "etc/valheim/mod-lock.json")}
                for code in tree_files(self.path("opt/valheim")):
                    if code.name == "valheim_server.x86_64" or code.suffix.lower() in {".dll", ".so", ".sh"}:
                        journal["fingerprints"]["opt/valheim/" + code.relative_to(self.path("opt/valheim")).as_posix()] = digest(code)
                journal["status"] = "committed"
                write_json(journal_path, journal)
            except BaseException:
                # Rollback uses CLI stop; release the management lock before delegation.
                fcntl.flock(mutex, fcntl.LOCK_UN)
                self.rollback(transaction)
                raise
            marker.unlink()
        print("Migration committed:", identity, "Destination is stopped; original VM is untouched.")
        if start:
            try:
                self.run([self.cli, "start"])
            except Exception as error:
                self.rollback(transaction)
                raise MigrationError("Destination start failed; previous destination restored and left stopped. Review transaction logs before retry.") from error
        return identity


def fetch_ssh(host, remote, output, port=22):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9][A-Za-z0-9.-]*", host), "SSH source must be user@hostname (no options or passwords).")
    require(remote.startswith("/") and not any(ord(c) < 32 for c in remote), "Remote archive must be an absolute path.")
    program = "import pathlib,sys,shutil; p=pathlib.Path(sys.argv[1]); assert p.is_file() and not p.is_symlink(); shutil.copyfileobj(p.open('rb'),sys.stdout.buffer)"
    command = "python3 -c " + shlex.quote(program) + " " + shlex.quote(remote)
    with output.open("xb") as writer:
        process = subprocess.Popen(["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=15",
                                    "-oServerAliveInterval=30", "-oServerAliveCountMax=2", "-p", str(port), "--", host, command], stdout=subprocess.PIPE)
        try:
            total = 0
            while chunk := process.stdout.read(1024 * 1024):
                total += len(chunk)
                require(total <= LIMIT, "SSH archive exceeds size limit.")
                writer.write(chunk)
            require(process.wait() == 0, "SSH archive transfer failed; check the host key and key-based authentication.")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("snapshot", help="Run manually on an already stopped source; never stops it for you")
    for name in ("game-dir", "saves-dir", "startup-script", "output"):
        export.add_argument("--" + name, required=True)
    export.add_argument("--unit-file")
    export.add_argument("--settings", help="Reviewed JSON instead of parsing a complex startup script")
    export.add_argument("--service", default="valheim.service")
    importer = sub.add_parser("import", help="Import a consistent snapshot into this LXC")
    origin = importer.add_mutually_exclusive_group(required=True)
    origin.add_argument("--archive")
    origin.add_argument("--ssh", metavar="USER@HOST")
    importer.add_argument("--remote-archive")
    importer.add_argument("--ssh-port", type=int, default=22)
    importer.add_argument("--sha256", required=True)
    importer.add_argument("--mod-plan")
    importer.add_argument("--mod-package-dir", help="Read local <owner>-<name>-<version>.zip packages; never fall back to network")
    importer.add_argument("--dry-run", action="store_true")
    importer.add_argument("--start", action="store_true", help="Start destination only after successful commit; coordinate source cutover first")
    rollback = sub.add_parser("rollback", help="Restore retained destination state; leaves destination stopped")
    rollback.add_argument("transaction")
    args = parser.parse_args()
    os.umask(0o077)
    require(os.geteuid() == 0, "Run migration as root; source capture is a separate, deliberate operation.")
    if args.action == "snapshot":
        snapshot(args)
        return
    require(subprocess.run(["systemd-detect-virt", "--container"], capture_output=True, text=True).stdout.strip() == "lxc", "Destination migration requires an LXC; no VM/host imports.")
    destination = Destination()
    if args.action == "rollback":
        require(re.fullmatch(r"[0-9a-f]{24}", args.transaction), "Invalid transaction ID.")
        destination.rollback(destination.state / args.transaction)
        return
    with tempfile.TemporaryDirectory(prefix="valheim-import-") as temporary:
        folder = Path(temporary)
        archive = Path(args.archive) if args.archive else folder / "incoming.tar.gz"
        if args.ssh:
            require(args.remote_archive and 1 <= args.ssh_port <= 65535, "SSH import requires --remote-archive and a valid port.")
            fetch_ssh(args.ssh, args.remote_archive, archive, args.ssh_port)
        source = folder / "source"
        source.mkdir()
        settings = unpack(archive, source, args.sha256)
        inventory = [{"file": str(p.relative_to(source)), "sha256": digest(p)} for p in tree_files(source / "plugins")] if (source / "plugins").exists() else []
        print(json.dumps({"world": settings["config"]["world_name"], "crossplay": settings["config"].get("crossplay", "0"),
                          "gameplay_arguments": settings["arguments"], "plugins_quarantined": inventory,
                          "configuration_files": len(list(tree_files(source / "bepinex-config"))) if (source / "bepinex-config").exists() else 0}, indent=2))
        if args.dry_run:
            if args.mod_package_dir is not None:
                require(args.mod_plan, "--mod-package-dir dry-run requires a reviewed --mod-plan.")
                plan = json.loads(Path(args.mod_plan).read_text())
                game = folder / "checked-game"
                game.mkdir()
                prepare_mods(game, source, plan, plan["tested_valheim_build_id"], local_package_downloader(args.mod_package_dir))
                print("Local packages verified in temporary storage; fresh Steam build compatibility still requires import-time verification.")
            print("Dry run: no services, destination files, packages or passwords changed. Mod compatibility is not yet established.")
            return
        require(sys.stdin.isatty(), "A terminal is required for a replacement password; secrets are never accepted on the command line.")
        password = getpass.getpass("Replacement server password (never import the old password): ")
        require(password == getpass.getpass("Confirm replacement password: "), "Passwords do not match.")
        plan = json.loads(Path(args.mod_plan).read_text()) if args.mod_plan else None
        destination.apply(source, settings, args.sha256, plan, password, args.start, mod_package_dir=args.mod_package_dir)


if __name__ == "__main__":
    try:
        main()
    except (MigrationError, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, EOFError,
            struct.error, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as error:
        # Never echo arbitrary source text, shell command lines or credentials on errors.
        if isinstance(error, MigrationError):
            print("Migration refused:", error, file=sys.stderr)
        else:
            print("Migration failed (" + type(error).__name__ + "). Inspect protected transaction state; destination rollback/recovery may be required.", file=sys.stderr)
        sys.exit(1)
