"""Migration tests: real archives/transactions; no network or production services."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import shutil
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migration", ROOT / "migration/valheim_migrate.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SCRIPT = '''#!/bin/bash
export SERVER_NAME="Fixture Community"
export PASSWORD="not-a-real-server-password"
cd /srv/game
./valheim_server.x86_64 -name "$SERVER_NAME" -world Current -password "$PASSWORD" -port 2456 -public 0 -crossplay -savedir /srv/saves -saveinterval 900 -backups 6 -backupshort 7200 -backuplong 43200 -modifier portals casual -modifier resources more
'''


def dll():
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 88, 0x10B)
    struct.pack_into("<II", data, 88 + 96 + 14 * 8, 1, 72)
    return bytes(data)


class FakeDestination(m.Destination):
    def __init__(self, root):
        super().__init__(root)
        self.events = []
        self.fail_commit = False
        self.fail_start = False

    def run(self, args, **kwargs):
        self.events.append(args)
        if self.fail_commit and args[-1] == "check-config":
            raise m.MigrationError("Injected final configuration failure.")
        if self.fail_start and args[-1] == "start":
            raise m.MigrationError("Injected start failure.")

    def stopped(self):
        self.events.append(["verify-stopped"])

    def ownership(self, path, game=False):
        for item in [path, *path.rglob("*")] if path.is_dir() else [path]:
            item.chmod(0o750 if item.is_dir() or item.name in {"valheim-launch", "valheim_server.x86_64"} else 0o600)

    def fresh_game(self, target):
        if target.parent.stat().st_mode & 0o777 != 0o711:
            raise AssertionError("SteamCMD cannot traverse the root-owned staging parent")
        target.mkdir()
        (target / "valheim_server.x86_64").write_bytes(b"fresh-game-not-imported")
        return "12345"

    def validate_config(self, values):
        m.require(len(values["server_password"]) >= 5, "Invalid replacement password.")
        m.require("\n" not in values["server_password"], "Invalid replacement password.")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "saves/worlds_local").mkdir(parents=True)
        for name in ("Current.db", "Current.fwl", "Older.db", "Older.fwl", "Current_backup_auto.db", "Current_backup_auto.fwl"):
            (self.source / "saves/worlds_local" / name).write_bytes(b"world-" + name.encode())
        for name in ("adminlist.txt", "bannedlist.txt", "permittedlist.txt"):
            (self.source / "saves" / name).write_text("fixture-id\n")
        m.write_json(self.source / "settings.json", m.parse_startup(SCRIPT))

    def archive(self, alter=None):
        files = {str(p.relative_to(self.source)): {"sha256": m.digest(p), "size": p.stat().st_size} for p in m.tree_files(self.source)}
        manifest = {"schema": 1, "consistent": "stopped-service-double-hash", "files": files}
        if alter:
            alter(manifest)
        m.write_json(self.source / "manifest.json", manifest)
        output = self.root / "snapshot.tar.gz"
        with tarfile.open(output, "w:gz") as archive:
            for path in m.tree_files(self.source):
                archive.add(path, arcname=str(path.relative_to(self.source)), recursive=False)
        return output

    def chunked(self, generation=1804, world="Current", folder="worlds_local", version=41):
        parent = self.source / "saves" / folder
        for suffix in (".db", ".fwl"):
            (parent / (world + suffix)).unlink(missing_ok=True)
        directory = parent / world
        directory.mkdir(parents=True, exist_ok=True)
        stem = directory / f"_main.{generation}"
        # Synthetic binary headers/index; opaque payloads are not game fixtures.
        Path(str(stem) + ".db2").write_bytes(struct.pack("<id", version, 12.5) + b"opaque-db")
        payload = struct.pack("<i", version) + b"opaque-metadata"
        Path(str(stem) + ".fwl2").write_bytes(struct.pack("<i", len(payload)) + payload)
        Path(str(stem) + ".ok").write_bytes(struct.pack("<i", version))
        entries = [(0x2020, 0, 15, 1), (1, 0, 3, 0)]
        Path(str(stem) + ".chunks").write_bytes(struct.pack("<Hii", version, 1, 2) +
                                                b"".join(struct.pack("<HBIi", *entry) for entry in entries))
        for coordinate, size, revision, count in entries:
            name = f"{coordinate >> 8:02x}_{coordinate & 255:02x}__{size}_{revision}.chunk"
            (directory / name).write_bytes(struct.pack("<hi", version, count) + (b"opaque-object" if count else b""))
        return directory

    def unpack_fixture(self):
        (self.source / "manifest.json").unlink(missing_ok=True)
        archive = self.archive()
        output = Path(tempfile.mkdtemp(dir=self.root))
        m.unpack(archive, output, m.digest(archive))
        return output

    def test_chunked_supported_versions_preserve_bytes(self):
        for version in (40, 41, 42):
            directory = self.chunked(version=version)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            output = self.unpack_fixture() / "saves/worlds_local/Current"
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_version42_reported_framing(self):
        directory = self.chunked(generation=1808, version=42)
        payload = struct.pack("<i", 42) + bytes(2486)
        (directory / "_main.1808.fwl2").write_bytes(struct.pack("<i", 2490) + payload)
        entries = [(0x2020 + i, 0, i + 1, 0) for i in range(32)]
        index = struct.pack("<Hii", 42, 0, 32) + b"".join(struct.pack("<HBIi", *entry) for entry in entries)
        self.assertEqual(len(index), 362)
        (directory / "_main.1808.chunks").write_bytes(index)
        for coordinate, size, revision, count in entries:
            (directory / f"{coordinate >> 8:02x}_{coordinate & 255:02x}__{size}_{revision}.chunk").write_bytes(struct.pack("<hi", 42, count))
        self.unpack_fixture()

    def test_version42_mismatched_versions_refused(self):
        directory = self.chunked(version=42)
        for name, offset, fmt, versions in (
                ("_main.1804.fwl2", 4, "<i", (40, 41, 43)),
                ("_main.1804.ok", 0, "<i", (40, 41, 43)),
                ("_main.1804.chunks", 0, "<H", (40, 41, 43)),
                ("20_20__0_15.chunk", 0, "<h", (39, 43))):
            path = directory / name
            original = path.read_bytes()
            for version in versions:
                changed = bytearray(original)
                struct.pack_into(fmt, changed, offset, version)
                path.write_bytes(changed)
                with self.subTest(name=name, version=version), self.assertRaises(m.MigrationError):
                    self.unpack_fixture()
            path.write_bytes(original)

    def test_older_unchanged_chunks_allowed_but_future_chunks_refused(self):
        directory = self.chunked(version=42)
        chunk = directory / "20_20__0_15.chunk"
        original = chunk.read_bytes()
        for version in (40, 41):
            chunk.write_bytes(struct.pack("<h", version) + original[2:])
            self.unpack_fixture()
        self.chunked(version=41)
        chunk.write_bytes(original)
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()

    def test_version42_changed_layouts_and_missing_chunks_refused(self):
        directory = self.chunked(version=42)
        index = directory / "_main.1804.chunks"
        original = index.read_bytes()
        for data in (original[:-1], original + b"extra", original[:6] + struct.pack("<i", 32) + original[10:]):
            index.write_bytes(data)
            with self.assertRaises(m.MigrationError):
                self.unpack_fixture()
        index.write_bytes(original)
        chunk = directory / "20_20__0_15.chunk"
        for data in (b"", struct.pack("<hi", 42, 99)):
            chunk.write_bytes(data)
            with self.assertRaises(m.MigrationError):
                self.unpack_fixture()
        chunk.unlink()
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()

    def test_chunked_generations_backups_and_bytes_preserved(self):
        self.chunked()
        self.chunked(1803)
        self.chunked(40, "Current_backup_auto-20261017")
        output = self.unpack_fixture()
        for path in m.tree_files(self.source / "saves"):
            self.assertEqual(path.read_bytes(), (output / path.relative_to(self.source)).read_bytes())

    def test_chunked_world_selected_from_settings(self):
        self.chunked(world="OtherWorld")
        settings = json.loads((self.source / "settings.json").read_text())
        settings["config"]["world_name"] = "OtherWorld"
        m.write_json(self.source / "settings.json", settings)
        self.unpack_fixture()

    def test_chunked_backup_cannot_replace_active_world(self):
        self.chunked(world="Current_backup_auto-20261017")
        for suffix in (".db", ".fwl"):
            (self.source / "saves/worlds_local" / ("Current" + suffix)).unlink()
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()

    def test_chunked_missing_metadata_or_chunks_refused(self):
        directory = self.chunked()
        for name in ("_main.1804.db2", "_main.1804.fwl2", "_main.1804.chunks", "_main.1804.ok", "20_20__0_15.chunk"):
            path = directory / name
            contents = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises(m.MigrationError):
                self.unpack_fixture()
            path.write_bytes(contents)

    def test_chunked_generations_cannot_be_combined(self):
        directory = self.chunked()
        (directory / "_main.1804.fwl2").rename(directory / "_main.1805.fwl2")
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()

    def test_chunked_malformed_index_refused(self):
        directory = self.chunked()
        index = directory / "_main.1804.chunks"
        good = index.read_bytes()
        entry = good[10:21]
        invalid = [b"", good[:-1], good + b"x", struct.pack("<Hii", 41, 1, -1),
                   struct.pack("<Hii", 99, 1, 2) + good[10:],
                   struct.pack("<Hii", 41, 999, 2) + good[10:],
                   struct.pack("<Hii", 41, 2, 2) + entry * 2,
                   struct.pack("<Hii", 41, 1, 1) + struct.pack("<HBIi", 0xffff, 4, 1, 1)]
        for data in invalid:
            index.write_bytes(data)
            with self.subTest(data=data.hex()), self.assertRaises(m.MigrationError):
                self.unpack_fixture()

    def test_chunked_wrong_revision_and_header_refused(self):
        directory = self.chunked()
        chunk = directory / "20_20__0_15.chunk"
        chunk.rename(directory / "20_20__0_14.chunk")
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()
        chunk.write_bytes(struct.pack("<hi", 41, 2) + b"opaque")
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()

    def test_chunked_malformed_names_versions_and_lengths_refused(self):
        directory = self.chunked()
        for name, data in (("_main.1804.db2", struct.pack("<i", 99)),
                           ("_main.1804.fwl2", struct.pack("<ii", 100, 41)),
                           ("_main.1804.ok", struct.pack("<i", 40)),
                           ("_main.01804.db2", b"bad")):
            path = directory / name
            original = path.read_bytes() if path.exists() else None
            path.write_bytes(data)
            with self.subTest(name=name), self.assertRaises(m.MigrationError):
                self.unpack_fixture()
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_chunked_mixed_and_multiple_save_roots_refused(self):
        directory = self.chunked()
        legacy = directory.parent / "Current.db"
        legacy.write_bytes(b"old")
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()
        legacy.unlink()
        self.chunked(folder="worlds")
        with self.assertRaises(m.MigrationError):
            self.unpack_fixture()

    def test_chunked_archive_integrity_still_required(self):
        self.chunked()
        archive = self.archive(lambda manifest: manifest["files"]["saves/worlds_local/Current/_main.1804.chunks"].update(sha256="0" * 64))
        output = self.root / "invalid-extracted"
        output.mkdir()
        with self.assertRaisesRegex(m.MigrationError, "Manifest content mismatch"):
            m.unpack(archive, output, m.digest(archive))

    def test_chunked_invalid_dry_run_never_applies(self):
        directory = self.chunked()
        (directory / "20_20__0_15.chunk").unlink()
        archive = self.archive()
        argv = ["valheim-migrate", "import", "--archive", str(archive), "--sha256", m.digest(archive), "--dry-run"]
        with mock.patch.object(m.sys, "argv", argv), mock.patch.object(m.os, "geteuid", return_value=0), \
             mock.patch.object(m.subprocess, "run", return_value=argparse.Namespace(stdout="lxc")), \
             mock.patch.object(m.Destination, "apply") as apply, mock.patch.object(m.getpass, "getpass") as password, \
             self.assertRaises(m.MigrationError):
            m.main()
        apply.assert_not_called()
        password.assert_not_called()

    def test_chunked_dry_run_has_no_destination_side_effects(self):
        self.chunked()
        destination = self.destination()
        before = {str(p): p.read_bytes() for p in m.tree_files(self.root / "destination")}
        self.test_dry_run_has_no_destination_or_password_side_effects()
        self.assertEqual(before, {str(p): p.read_bytes() for p in m.tree_files(self.root / "destination")})

    def destination(self):
        root = self.root / "destination"
        for name in ("etc/valheim", "usr/local/libexec", "opt/valheim", "var/lib/valheim/saves"):
            (root / name).mkdir(parents=True)
        (root / "etc/valheim/server.conf").write_text("server_name=Old Destination\nworld_name=Old\nserver_password=old-fixture-password\nauto_update=1\n")
        (root / "usr/local/libexec/valheim-launch").write_text('#!/usr/bin/env bash\nargs=()\nexec ./valheim_server.x86_64 "${args[@]}"\n')
        (root / "opt/valheim/old-binary").write_text("old destination game")
        (root / "var/lib/valheim/saves/old-world").write_text("old destination world")
        return FakeDestination(root)

    def test_static_translation_never_preserves_source_password(self):
        settings = m.parse_startup(SCRIPT)
        self.assertEqual(settings["config"]["world_name"], "Current")
        self.assertEqual(settings["config"]["crossplay"], "1")
        self.assertEqual(settings["config"]["saveinterval"], "900")
        self.assertEqual(settings["arguments"], ["-modifier", "portals", "casual", "-modifier", "resources", "more"])
        self.assertNotIn("not-a-real-server-password", json.dumps(settings))

    def test_static_parser_rejects_execution_and_ambiguity(self):
        for text in (SCRIPT.replace('"Fixture Community"', '"$(id)"'), SCRIPT + "\nrm -rf /\n",
                     SCRIPT + SCRIPT, SCRIPT.replace('"$SERVER_NAME"', '"$UNKNOWN"'), SCRIPT.replace("-public 0", "-public 0 -public 1")):
            with self.subTest(text=text[-40:]), self.assertRaises(m.MigrationError):
                m.parse_startup(text)

    def test_service_environment_is_data(self):
        text = './valheim_server.x86_64 -name "$NAME" -world Current -password ignored'
        self.assertEqual(m.parse_startup(text, 'Environment="NAME=Fixture Community"')["config"]["server_name"], "Fixture Community")
        with self.assertRaises(m.MigrationError):
            m.parse_startup(text, "EnvironmentFile=/etc/secret")

    def test_snapshot_checks_stopped_source_and_sanitizes_credentials(self):
        game = self.root / "game"
        (game / "BepInEx/config").mkdir(parents=True)
        for number in range(5):
            (game / f"BepInEx/config/plugin{number}.cfg").write_text("fixture=true")
        script = self.root / "start.sh"
        script.write_text(SCRIPT)
        output = self.root / "export.tar.gz"
        args = argparse.Namespace(output=str(output), game_dir=str(game), saves_dir=str(self.source / "saves"),
                                  startup_script=str(script), settings=None, unit_file=None, service="valheim.service")
        with mock.patch.object(m, "stopped") as stopped:
            m.snapshot(args)
        self.assertGreaterEqual(stopped.call_count, 3)
        extracted = self.root / "checked"
        extracted.mkdir()
        m.unpack(output, extracted, m.digest(output))
        self.assertEqual(len(list((extracted / "bepinex-config").glob("*.cfg"))), 5)
        self.assertNotIn("not-a-real-server-password", (extracted / "settings.json").read_text())

    def test_source_must_already_be_stopped(self):
        with mock.patch.object(m.subprocess, "run", return_value=argparse.Namespace(stdout="active")):
            with self.assertRaises(m.MigrationError):
                m.stopped("valheim.service")

    def test_archive_verifies_all_worlds_backups_and_lists(self):
        archive = self.archive()
        destination = self.root / "unpacked"
        destination.mkdir()
        m.unpack(archive, destination, m.digest(archive))
        for original in m.tree_files(self.source / "saves"):
            self.assertEqual(original.read_bytes(), (destination / original.relative_to(self.source)).read_bytes())

    def test_archive_hash_and_manifest_tampering_refused(self):
        archive = self.archive()
        with self.assertRaises(m.MigrationError):
            m.unpack(archive, self.root / "out", "0" * 64)
        (self.source / "manifest.json").unlink()
        archive = self.archive(lambda manifest: manifest["files"]["saves/worlds_local/Current.db"].update(sha256="0" * 64))
        output = self.root / "out"
        output.mkdir()
        with self.assertRaises(m.MigrationError):
            m.unpack(archive, output, m.digest(archive))

    def test_malicious_tar_paths_links_devices_and_duplicates_refused(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("saves/link", tarfile.SYMTYPE), ("saves/device", tarfile.CHRTYPE)):
            with self.subTest(name=name):
                archive = self.root / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as stream:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    stream.addfile(member, io.BytesIO())
                with self.assertRaises(m.MigrationError):
                    m.unpack(archive, self.root / "out", m.digest(archive))

    def test_import_idempotency_and_rollback_preserve_previous_destination(self):
        destination = self.destination()
        settings = m.parse_startup(SCRIPT)
        identity = destination.apply(self.source, settings, "a" * 64, None, "replacement-fixture-password")
        self.assertEqual(destination.path("opt/valheim/valheim_server.x86_64").read_bytes(), b"fresh-game-not-imported")
        self.assertIn("auto_update=0", destination.path("etc/valheim/server.conf").read_text())
        self.assertIn("-modifier portals casual", destination.path("usr/local/libexec/valheim-launch").read_text())
        count = len(destination.events)
        self.assertEqual(destination.apply(self.source, settings, "a" * 64, None, "ignored-on-retry"), identity)
        self.assertEqual(len(destination.events), count)
        destination.rollback(destination.state / identity)
        self.assertTrue(destination.path("opt/valheim/old-binary").exists())
        self.assertTrue(destination.path("var/lib/valheim/saves/old-world").exists())
        self.assertFalse(destination.path("etc/valheim/mod-lock.json").exists())
        self.assertTrue((destination.state / identity / "after/saves/worlds_local/Current.db").exists())
        destination.rollback(destination.state / identity)
        next_identity = destination.apply(self.source, settings, "a" * 64, None, "replacement-fixture-password")
        self.assertNotEqual(next_identity, identity)
        self.assertTrue((destination.state / identity / "after/saves/worlds_local/Current.db").exists())

    def test_activation_failure_rolls_back_without_starting_server(self):
        destination = self.destination()
        destination.fail_commit = True
        with self.assertRaises(m.MigrationError):
            destination.apply(self.source, m.parse_startup(SCRIPT), "b" * 64, None, "replacement-fixture-password")
        self.assertTrue(destination.path("opt/valheim/old-binary").exists())
        self.assertTrue(destination.path("var/lib/valheim/saves/old-world").exists())
        self.assertNotIn([destination.cli, "start"], destination.events)

    def test_optional_start_failure_rolls_back_and_preserves_new_data(self):
        destination = self.destination()
        destination.fail_start = True
        with self.assertRaises(m.MigrationError):
            destination.apply(self.source, m.parse_startup(SCRIPT), "e" * 64, None, "replacement-fixture-password", start=True)
        self.assertTrue(destination.path("opt/valheim/old-binary").exists())
        self.assertTrue(destination.path("var/lib/valheim/saves/old-world").exists())
        self.assertFalse(destination.path("etc/valheim/maintenance").exists())

    def test_idempotent_retry_refuses_modified_executable_code(self):
        destination = self.destination()
        settings = m.parse_startup(SCRIPT)
        destination.apply(self.source, settings, "f" * 64, None, "replacement-fixture-password")
        destination.path("opt/valheim/valheim_server.x86_64").write_bytes(b"modified after import")
        with self.assertRaises(m.MigrationError):
            destination.apply(self.source, settings, "f" * 64, None, "replacement-fixture-password")

    def test_plugin_is_quarantined_without_plan_and_never_executed(self):
        (self.source / "plugins").mkdir()
        (self.source / "plugins/Unknown.dll").write_bytes(dll())
        game = self.root / "new-game"
        game.mkdir()
        with self.assertRaises(m.MigrationError):
            m.prepare_mods(game, self.source, None, "12345")
        self.assertFalse((game / "BepInEx/plugins/Unknown.dll").exists())

    def test_plugin_static_validation_rejects_native_or_malformed_binary(self):
        path = self.root / "plugin.dll"
        path.write_bytes(dll())
        m.managed_dll(path)
        for contents in (b"arbitrary", bytes(512), dll()[:100]):
            path.write_bytes(contents)
            with self.assertRaises(m.MigrationError):
                m.managed_dll(path)

    def test_zip_traversal_refused(self):
        path = self.root / "package.zip"
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("../outside", "no")
        with self.assertRaises(m.MigrationError):
            m.package_zip(path, m.digest(path), self.root / "expanded")

    def reviewed_mod_fixture(self):
        (self.source / "plugins").mkdir()
        imported = self.source / "plugins/FixtureAdmin.dll"
        imported.write_bytes(dll() + b"older-version")
        (self.source / "plugins/Unreviewed.dll").write_bytes(b"never execute")
        (self.source / "bepinex-config").mkdir()
        (self.source / "bepinex-config/BepInEx.cfg").write_text("unsafe-old-loader-path=source")
        for number in range(4):
            (self.source / f"bepinex-config/plugin{number}.cfg").write_text("fixture=true")
        loader_id, plugin_id = "denikson-BepInExPack_Valheim-5.4.9999", "Fixture-Admin-1.0.0"
        archives = {}
        native = bytearray(20)
        native[:5] = b"\x7fELF\x02"
        native[18:20] = b"\x3e\0"
        contents = {
            loader_id: {"BepInExPack_Valheim/start_game_bepinex.sh": b"#!/bin/sh\nexit 0\n",
                        "BepInExPack_Valheim/BepInEx/core/BepInEx.Preloader.dll": dll(),
                        "BepInExPack_Valheim/doorstop_libs/libdoorstop_x64.so": bytes(native),
                        "BepInExPack_Valheim/BepInEx/config/BepInEx.cfg": b"trusted-package-default"},
            plugin_id: {"plugins/FixtureAdmin.dll": dll() + b"fresh-reviewed-version"}}
        records = []
        for identifier, files in contents.items():
            path = self.root / (identifier + ".zip")
            _, name, version = identifier.split("-")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"name": name, "version_number": version,
                                                             "dependencies": [] if identifier == loader_id else [loader_id]}))
                for filename, data in files.items():
                    archive.writestr(filename, data)
            archives[identifier] = path
            records.append({"id": identifier, "sha256": m.digest(path), "reviewed": True})
        records[1]["assemblies"] = [{"path": "plugins/FixtureAdmin.dll", "sha256": hashlib.sha256(contents[plugin_id]["plugins/FixtureAdmin.dll"]).hexdigest(),
                                     "source_sha256": m.digest(imported)}]
        plan = {"tested_valheim_build_id": "12345", "compatibility_confirmed": True, "bepinex": records[0], "plugins": [records[1]]}
        game = self.root / "fresh"
        game.mkdir()

        return game, plan, archives, contents

    def test_reviewed_fresh_mod_packages_enable_only_approved_assemblies(self):
        game, plan, archives, contents = self.reviewed_mod_fixture()
        plugin_id = plan["plugins"][0]["id"]

        def download(package, target):
            shutil.copyfile(archives[package["id"]], target)

        self.assertTrue(m.prepare_mods(game, self.source, plan, "12345", download))
        self.assertEqual((game / "BepInEx/plugins/FixtureAdmin.dll").read_bytes(), contents[plugin_id]["plugins/FixtureAdmin.dll"])
        self.assertFalse((game / "BepInEx/plugins/Unreviewed.dll").exists())
        self.assertEqual((game / "BepInEx/config/BepInEx.cfg").read_text(), "trusted-package-default")
        self.assertEqual(len(list((game / "BepInEx/config").glob("*.cfg"))), 5)
        self.assertTrue((self.source / "plugins/Unreviewed.dll").exists())

    def test_dry_run_has_no_destination_or_password_side_effects(self):
        archive = self.archive()
        argv = ["valheim-migrate", "import", "--archive", str(archive), "--sha256", m.digest(archive), "--dry-run"]
        with mock.patch.object(m.sys, "argv", argv), mock.patch.object(m.os, "geteuid", return_value=0), \
             mock.patch.object(m.subprocess, "run", return_value=argparse.Namespace(stdout="lxc")), \
             mock.patch.object(m.Destination, "apply") as apply, mock.patch.object(m.getpass, "getpass") as password, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            m.main()
            apply.assert_not_called()
            password.assert_not_called()

    def test_snapshot_detects_changed_files_before_finalization(self):
        game = self.root / "game"
        game.mkdir()
        script = self.root / "start.sh"
        script.write_text(SCRIPT)
        args = argparse.Namespace(output=str(self.root / "export.tar.gz"), game_dir=str(game), saves_dir=str(self.source / "saves"),
                                  startup_script=str(script), settings=None, unit_file=None, service="valheim.service")
        calls = []

        def mutate(service):
            calls.append(service)
            if len(calls) == 2:
                (self.source / "saves/worlds_local/Current.db").write_bytes(b"changed while capturing")

        with mock.patch.object(m, "stopped", side_effect=mutate), self.assertRaises(m.MigrationError):
            m.snapshot(args)
        self.assertFalse(Path(args.output).exists())

    def test_mod_plan_rejects_unverified_build(self):
        game = self.root / "new-game"
        game.mkdir()
        with self.assertRaises(m.MigrationError):
            m.prepare_mods(game, self.source, {"tested_valheim_build_id": "old", "compatibility_confirmed": True}, "new")

    def test_ssh_rejects_option_injection_without_running_commands(self):
        with mock.patch.object(m.subprocess, "Popen") as process:
            for host in ("-oProxyCommand=evil", "user@host;id", "user@host /bin/sh"):
                with self.assertRaises(m.MigrationError):
                    m.fetch_ssh(host, "/snapshot.tar.gz", self.root / "incoming")
            process.assert_not_called()

    def test_activation_rename_failure_restores_already_moved_destination(self):
        destination = self.destination()
        replace = m.durable_replace
        injected = []

        def fail(source, target):
            if Path(source).name == "saves" and Path(target) == destination.path("var/lib/valheim/saves") and not injected:
                injected.append(True)
                raise OSError("Injected rename failure")
            return replace(source, target)

        with mock.patch.object(m, "durable_replace", side_effect=fail), self.assertRaises(OSError):
            destination.apply(self.source, m.parse_startup(SCRIPT), "c" * 64, None, "replacement-fixture-password")
        self.assertTrue(destination.path("opt/valheim/old-binary").exists())
        self.assertTrue(destination.path("var/lib/valheim/saves/old-world").exists())
        self.assertFalse(destination.path("etc/valheim/maintenance").exists())

    def test_destination_custom_modifiers_are_not_silently_combined(self):
        destination = self.destination()
        launcher = destination.path("usr/local/libexec/valheim-launch")
        launcher.write_text(launcher.read_text().replace('exec ./valheim', 'args+=(-modifier raids none)\nexec ./valheim'))
        with self.assertRaises(m.MigrationError):
            destination.apply(self.source, m.parse_startup(SCRIPT), "d" * 64, None, "replacement-fixture-password")
        self.assertNotIn([destination.cli, "stop"], destination.events)

    def test_trusted_ssh_archive_is_streamed_read_only(self):
        process = mock.Mock()
        process.stdout = io.BytesIO(b"archive-fixture")
        process.wait.return_value = 0
        process.poll.return_value = 0
        target = self.root / "ssh.tar.gz"
        with mock.patch.object(m.subprocess, "Popen", return_value=process) as run:
            m.fetch_ssh("user@source.example", "/root/snapshot.tar.gz", target)
        self.assertEqual(target.read_bytes(), b"archive-fixture")
        arguments = run.call_args.args[0]
        self.assertIn("-oStrictHostKeyChecking=yes", arguments)
        self.assertIn("-oBatchMode=yes", arguments)
        self.assertNotIn("systemctl", arguments[-1])


if __name__ == "__main__":
    unittest.main()
