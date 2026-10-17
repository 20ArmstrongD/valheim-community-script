"""Network-free subprocess, offline packages, launcher and staging recovery tests."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import unittest
from unittest import mock
import zipfile

import test_migration as fixtures

m = fixtures.m


class PackageTests(unittest.TestCase):
    setUp = fixtures.MigrationTests.setUp
    destination = fixtures.MigrationTests.destination
    reviewed_mod_fixture = fixtures.MigrationTests.reviewed_mod_fixture

    def package(self, body=b"reviewed ZIP bytes"):
        return {"id": "Fixture-Package-1.0.0", "sha256": hashlib.sha256(body).hexdigest()}

    def curl(self, responses):
        calls = []

        def popen(command, stdout, stderr):
            code, status, body = responses[min(len(calls), len(responses) - 1)]
            calls.append(command)
            stderr.write(f"HTTP_STATUS={status}\n".encode())
            stderr.flush()
            process = mock.Mock()
            process.stdout = io.BytesIO(body)
            process.wait.return_value = code
            process.poll.return_value = code
            return process
        return mock.patch.object(m.subprocess, "Popen", side_effect=popen), calls

    def test_curl_success_uses_secure_flags_and_private_verified_output(self):
        patch, calls = self.curl([(0, "200", b"reviewed ZIP bytes")])
        target = self.root / "download.zip"
        with patch, mock.patch.object(m.shutil, "which", return_value="/usr/bin/curl"):
            m.download_package(self.package(), target)
        self.assertEqual(target.read_bytes(), b"reviewed ZIP bytes")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        command = calls[0]
        self.assertEqual(command[:2], ["/usr/bin/curl", "--disable"])
        for option in ("--fail", "--location", "--connect-timeout", "--max-time", "--max-filesize"):
            self.assertIn(option, command)
        for option in ("--proto", "--proto-redir"):
            self.assertEqual(command[command.index(option) + 1], "=https")
        self.assertFalse(target.with_suffix(".partial").exists())

    def test_curl_real_subprocess_streams_to_verified_file(self):
        executable = self.root / "fake-curl"
        executable.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'reviewed ZIP bytes')\nsys.stderr.write('HTTP_STATUS=200\\n')\n")
        executable.chmod(0o700)
        with mock.patch.object(m.shutil, "which", return_value=str(executable)):
            m.download_package(self.package(), self.root / "real-pipe.zip")
        self.assertEqual((self.root / "real-pipe.zip").read_bytes(), b"reviewed ZIP bytes")

    def test_http_403_and_404_report_identity_url_status_and_keep_logs(self):
        for status in ("403", "404"):
            target = self.root / (status + ".zip")
            patch, calls = self.curl([(22, status, b"")])
            with patch, mock.patch.object(m.shutil, "which", return_value="curl"), self.assertRaises(m.MigrationError) as error:
                m.download_package(self.package(), target)
            self.assertIn("stage=mod-download", str(error.exception))
            self.assertIn("HTTP=" + status, str(error.exception))
            self.assertIn("https://thunderstore.io/package/download/Fixture/Package/1.0.0/", str(error.exception))
            self.assertEqual(len(calls), 1)
            self.assertTrue(target.with_suffix(".download.log").exists())
            self.assertFalse(target.exists())

    def test_timeout_and_interrupted_transfers_retry_with_fresh_files(self):
        for code in (28, 18, -15):
            patch, calls = self.curl([(code, "000", b"partial")])
            target = self.root / f"failure-{code}.zip"
            with patch, mock.patch.object(m.shutil, "which", return_value="curl"), mock.patch.object(m.time, "sleep"), self.assertRaises(m.MigrationError):
                m.download_package(self.package(), target)
            self.assertEqual(len(calls), 1 if code < 0 else 3)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".partial").exists())
        patch, calls = self.curl([(18, "200", b"partial"), (0, "200", b"reviewed ZIP bytes")])
        with patch, mock.patch.object(m.shutil, "which", return_value="curl"), mock.patch.object(m.time, "sleep"):
            m.download_package(self.package(), self.root / "retried.zip")
        self.assertEqual(len(calls), 2)

    def test_curl_hash_mismatch_and_stream_limit_never_publish(self):
        for limit in (128 * 1024**2, 2):
            target = self.root / f"invalid-{limit}.zip"
            patch, _ = self.curl([(0, "200", b"wrong contents")])
            with patch, mock.patch.object(m.shutil, "which", return_value="curl"), mock.patch.object(m, "PACKAGE_LIMIT", limit), self.assertRaises(m.MigrationError):
                m.download_package(self.package(), target)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".partial").exists())

    def test_curl_unavailable_fails_without_network_fallback(self):
        with mock.patch.object(m.shutil, "which", return_value=None), self.assertRaisesRegex(m.MigrationError, "curl unavailable"):
            m.download_package(self.package(), self.root / "missing.zip")

    def test_local_packages_full_validation_and_read_only_sources(self):
        game, plan, archives, _ = self.reviewed_mod_fixture()
        directory = self.root.resolve() / "offline"
        directory.mkdir()
        for identifier, path in archives.items():
            shutil.copyfile(path, directory / (identifier + ".zip"))
        before = {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in directory.iterdir()}
        with mock.patch.object(m, "download_package", side_effect=AssertionError("network forbidden")):
            self.assertTrue(m.prepare_mods(game, self.source, plan, "12345", m.local_package_downloader(directory)))
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in directory.iterdir()})
        self.assertTrue((game / "BepInEx/plugins/FixtureAdmin.dll").exists())

    def test_offline_dry_run_verifies_packages_without_apply_or_password(self):
        _, plan, archives, _ = self.reviewed_mod_fixture()
        directory = self.root.resolve() / "offline"
        directory.mkdir()
        for identifier, path in archives.items():
            shutil.copyfile(path, directory / (identifier + ".zip"))
        plan_path = self.root / "plan.json"
        m.write_json(plan_path, plan)
        archive = fixtures.MigrationTests.archive(self)
        argv = ["valheim-migrate", "import", "--archive", str(archive), "--sha256", m.digest(archive),
                "--dry-run", "--mod-plan", str(plan_path), "--mod-package-dir", str(directory)]
        with mock.patch.object(m.sys, "argv", argv), mock.patch.object(m.os, "geteuid", return_value=0), \
             mock.patch.object(m.subprocess, "run", return_value=argparse.Namespace(stdout="lxc")), \
             mock.patch.object(m.Destination, "apply") as apply, mock.patch.object(m.getpass, "getpass") as password, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            m.main()
        apply.assert_not_called()
        password.assert_not_called()

    def test_local_missing_hash_symlinks_and_unsafe_ids(self):
        directory = self.root.resolve() / "offline"
        directory.mkdir()
        downloader = m.local_package_downloader(directory)
        package = self.package()
        source = directory / (package["id"] + ".zip")
        with self.assertRaises(m.MigrationError):
            downloader(package, self.root / "a.zip")
        source.write_bytes(b"wrong")
        with self.assertRaises(m.MigrationError):
            downloader(package, self.root / "b.zip")
        source.unlink()
        source.symlink_to(self.source / "settings.json")
        with self.assertRaises(m.MigrationError):
            downloader(package, self.root / "c.zip")
        alias = self.root / "alias"
        alias.symlink_to(directory)
        with self.assertRaises(m.MigrationError):
            m.local_package_downloader(alias)
        with self.assertRaises(m.MigrationError):
            downloader({"id": "../escape", "sha256": "0" * 64}, self.root / "d.zip")

    def test_package_zip_links_and_unsafe_paths(self):
        for name, mode in (("../outside", 0o100600), ("/outside", 0o100600), ("linked", 0o120777)):
            archive = self.root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                member = zipfile.ZipInfo(name)
                member.external_attr = mode << 16
                stream.writestr(member, "unsafe")
            with self.assertRaises(m.MigrationError):
                m.package_zip(archive, m.digest(archive), self.root / "expanded")

    def test_manifest_dependency_and_assembly_hash_mismatches(self):
        for failure in ("identity", "dependency", "assembly"):
            with self.subTest(failure=failure):
                # Separate fixture tree per case.
                case = fixtures.MigrationTests()
                case.setUp()
                try:
                    game, plan, archives, _ = case.reviewed_mod_fixture()
                    package = plan["plugins"][0]
                    if failure == "assembly":
                        package["assemblies"][0]["sha256"] = "0" * 64
                    else:
                        path = archives[package["id"]]
                        with zipfile.ZipFile(path) as source:
                            data = {name: source.read(name) for name in source.namelist()}
                        manifest = json.loads(data["manifest.json"])
                        manifest["name" if failure == "identity" else "dependencies"] = "Wrong" if failure == "identity" else ["Unknown-Dependency-1.0.0"]
                        data["manifest.json"] = json.dumps(manifest).encode()
                        with zipfile.ZipFile(path, "w") as target:
                            for name, body in data.items():
                                target.writestr(name, body)
                        package["sha256"] = m.digest(path)
                    def download(record, target):
                        shutil.copyfile(archives[record["id"]], target)
                    with self.assertRaises(m.MigrationError):
                        m.prepare_mods(game, case.source, plan, "12345", download)
                finally:
                    case.doCleanups()

    def test_modded_production_launcher_environment_and_rollback(self):
        destination = self.destination()
        def mods(game, *args):
            library = game / "doorstop_libs/libdoorstop_x64.so"
            library.parent.mkdir()
            library.write_bytes(b"not executed")
            return True
        with mock.patch.object(m, "prepare_mods", side_effect=mods):
            identity = destination.apply(self.source, m.parse_startup(fixtures.SCRIPT), "b" * 64, None, "replacement-password")
        launcher = destination.path("usr/local/libexec/valheim-launch").read_text()
        self.assertIn("export DOORSTOP_ENABLED=1\n", launcher)
        self.assertIn("export DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll", launcher)
        self.assertIn("export LD_PRELOAD=./doorstop_libs/libdoorstop_x64.so", launcher)
        self.assertIn('export LD_LIBRARY_PATH=./doorstop_libs:"${LD_LIBRARY_PATH:-}"', launcher)
        self.assertIn('exec ./valheim_server.x86_64 "${args[@]}"', launcher)
        self.assertNotIn("start_game_bepinex.sh", launcher)
        subprocess.run(["bash", "-n"], input=launcher, text=True, check=True)
        self.assertNotIn([destination.cli, "start"], destination.events)
        destination.rollback(destination.state / identity)
        self.assertNotIn("DOORSTOP", destination.path("usr/local/libexec/valheim-launch").read_text())

    def test_staging_failure_recovery_preserves_data_and_permits_retry(self):
        destination = self.destination()
        before = {str(p): p.read_bytes() for p in m.tree_files(self.root / "destination")}
        with mock.patch.object(m, "prepare_mods", side_effect=m.MigrationError("stage=mod-download package=Fixture HTTP=403")), self.assertRaises(m.MigrationError):
            destination.apply(self.source, m.parse_startup(fixtures.SCRIPT), "c" * 64, None, "replacement-password")
        transaction = next(p for p in destination.state.iterdir() if p.is_dir())
        journal = json.loads((transaction / "journal.json").read_text())
        self.assertEqual(journal["status"], "staging-failed")
        self.assertIn("HTTP=403", journal["error"])
        events = list(destination.events)
        destination.rollback(transaction)
        self.assertEqual(events, destination.events)
        for path, contents in before.items():
            self.assertEqual(Path(path).read_bytes(), contents)
        self.assertTrue((transaction / "quarantine/settings.json").exists())
        self.assertFalse(destination.path("etc/valheim/maintenance").exists())
        new = destination.apply(self.source, m.parse_startup(fixtures.SCRIPT), "c" * 64, None, "replacement-password")
        self.assertNotEqual(new, transaction.name)

    def test_interrupted_staging_retains_logs_and_recovers_without_stop(self):
        destination = self.destination()
        def interrupt(game, *args):
            (game.parent / "package-0.download.log").write_text("HTTP_STATUS=000\n")
            raise KeyboardInterrupt()
        with mock.patch.object(m, "prepare_mods", side_effect=interrupt), self.assertRaises(m.MigrationError):
            destination.apply(self.source, m.parse_startup(fixtures.SCRIPT), "d" * 64, None, "replacement-password")
        transaction = next(p for p in destination.state.iterdir() if p.is_dir())
        log = transaction / "package-0.download.log"
        self.assertEqual(log.read_text(), "HTTP_STATUS=000\n")
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        destination.rollback(transaction)
        self.assertNotIn([destination.cli, "stop"], destination.events)
        self.assertTrue(destination.path("opt/valheim/old-binary").exists())
        self.assertTrue(log.exists())

    def test_concurrent_migration_recovery_lease_rejected(self):
        destination = self.destination()
        other = fixtures.FakeDestination(self.root / "destination")
        with destination.migration_operation(), self.assertRaises(m.MigrationError):
            with other.migration_operation():
                self.fail("concurrent recovery should never enter")

    def test_legacy_interrupted_staging_and_unsafe_journal(self):
        destination = self.destination()
        destination.state.mkdir(mode=0o700, parents=True)
        transaction = destination.state / ("a" * 24)
        (transaction / "old").mkdir(parents=True)
        journal = {"status": "staging", "slots": [], "fingerprints": {}}
        m.write_json(transaction / "journal.json", journal)
        destination.rollback(transaction)
        self.assertEqual(destination.events, [])
        journal.update(status="activating", slots=[{"name": "game", "target": "../production", "had_old": False, "intent": True}])
        m.write_json(transaction / "journal.json", journal)
        with self.assertRaises(m.MigrationError):
            destination.rollback(transaction)
        self.assertEqual(destination.events, [])
