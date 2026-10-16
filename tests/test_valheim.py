"""Exercise the emitted Bash runtime against disposable directories and OS doubles."""
import fcntl
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("extract", ROOT / "tools/extract-runtime.py")
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)
BUNDLE = extractor.extract()
BASH = shutil.which("bash")


def virtualize(body, root):
    body = body.replace("export PATH=/usr/sbin:/usr/bin:/sbin:/bin", "# Test PATH supplied by harness")
    body = body.replace('"$EUID" -eq 0', f'"$EUID" -eq {os.geteuid()}')
    body = body.replace('"$EUID" -ne 0', f'"$EUID" -ne {os.geteuid()}')
    for prefix in ("/usr/local/libexec", "/usr/local/bin/valheim", "/etc/valheim", "/etc/systemd/system",
                   "/etc/debian_version", "/var/lib/valheim", "/var/backups/valheim", "/opt/valheim",
                   "/opt/steamcmd", "/run/valheim-management"):
        body = re.sub(r"(?<![a-zA-Z0-9_$])" + re.escape(prefix), str(root) + prefix, body)
    return body.replace("--directory / etc/", f"--directory {root} etc/")


class ValheimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="valheim-test-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.mockbin = self.root / "mockbin"
        self.mockbin.mkdir()
        for command in ("stat", "id", "chown", "flock", "sleep", "systemctl", "runuser", "tar", "journalctl",
                        "getent", "dpkg", "systemd-detect-virt", "apt", "useradd", "install"):
            (self.mockbin / command).symlink_to(ROOT / "tests/mock-command.py")
        if not shutil.which("sha256sum"):
            (self.mockbin / "sha256sum").symlink_to(shutil.which("gsha256sum"))
        self.env = {**os.environ, "TEST_ROOT": str(self.root), "REAL_TAR": shutil.which("tar")}
        self.env["PATH"] = str(self.mockbin) + os.pathsep + os.environ["PATH"]
        for path, body in BUNDLE.items():
            dest = self.root / path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if body.startswith("#!/"):
                body = virtualize(body, self.root)
            dest.write_text(body)
            dest.chmod(0o755)
        self.saves = self.root / "var/lib/valheim/saves/worlds_local"
        self.saves.mkdir(parents=True)
        (self.saves / "Dedicated.db").write_bytes(b"original-world\x00\x01")
        (self.saves / "Dedicated.fwl").write_bytes(b"world-metadata")
        self.backups = self.root / "var/backups/valheim"
        self.backups.mkdir(parents=True)
        self.backups.chmod(0o700)
        (self.root / "run/valheim-management").mkdir(parents=True)
        for path in ("opt/steamcmd/steamcmd.sh", "opt/valheim/valheim_server.x86_64"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(ROOT / "tests/mock-command.py")
        self.state("active")
        self.config()

    def config(self, **overrides):
        self.values = dict(server_name="Disposable Test Server", world_name="Dedicated",
                           server_password="fixture-only-password", public="1", crossplay="1", port="2456",
                           saveinterval="1800", world_backups="6", backupshort="7200", backuplong="43200",
                           backup_retention="14", auto_update="0")
        self.values.update(overrides)
        self.conf = self.root / "etc/valheim/server.conf"
        self.conf.parent.mkdir(parents=True, exist_ok=True)
        self.conf.parent.chmod(0o750)
        self.conf.write_text("".join(f"{k}={v}\n" for k, v in self.values.items()))
        self.conf.chmod(0o640)

    def state(self, active):
        (self.root / "state").write_text(json.dumps(dict(active=active, pid=123 if active == "active" else 0)))

    def run_cli(self, *args, **env):
        return subprocess.run([BASH, str(self.root / "usr/local/bin/valheim"), *map(str, args)],
                              env={**self.env, **env}, capture_output=True, text=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def events(self):
        return (self.root / "events").read_text() if (self.root / "events").exists() else ""

    def marker(self):
        return self.root / "etc/valheim/maintenance"

    def run_installer(self, **env):
        (self.root / "etc/debian_version").write_text("13")
        source = (ROOT / "install/valheim-install.sh").read_text()
        installer = self.root / "installer.sh"
        installer.write_text(virtualize(source, self.root))
        lifecycle = """
color() { :; }
catch_errors() { set -e; }
setting_up_container() { :; }
network_check() { :; }
update_os() { :; }
msg_info() { :; }
msg_ok() { :; }
msg_error() { echo "$*" >&2; }
motd_ssh() { :; }
customize() { :; }
cleanup_lxc() { :; }
is_unattended() { return 0; }
random_password() { echo generated-test-fixture-password; }
prompt_password() { echo "$2"; }
prompt_input_required() {
  local name="$4" value
  value="${!name}"
  echo "${value:-$2}"
}
"""
        return subprocess.run([BASH, str(installer)], capture_output=True, text=True,
                              env={**self.env, "FUNCTIONS_FILE_PATH": lifecycle, "STD": "", **env})

    def test_configuration_accepts_literal_shell_metacharacters(self):
        self.config(server_name='Test $(id) `id` "quoted"', server_password='test-only-$(id);&')
        self.assert_ok(self.run_cli("check-config"))
        result = subprocess.run([BASH, str(self.root / "usr/local/libexec/valheim-launch")],
                                env=self.env, capture_output=True, text=True)
        self.assert_ok(result)
        args = json.loads((self.root / "launch-args").read_text())
        self.assertEqual(args[args.index("-name") + 1], self.values["server_name"])
        self.assertEqual(args[args.index("-password") + 1], self.values["server_password"])
        self.assertIn("-crossplay", args)
        self.assertEqual(args[args.index("-backups") + 1], "6")
        self.assertNotIn(self.values["server_password"], result.stdout + result.stderr)

    def test_configuration_rejects_invalid_values(self):
        for key, value in [("world_name", "../world"), ("world_name", "a/b"), ("server_name", "bad\nname"),
                           ("server_password", "1234"), ("public", "yes"), ("crossplay", "2"), ("port", "65535"),
                           ("port", "02456"), ("saveinterval", "0"), ("backup_retention", "0"),
                           ("world_backups", "101"), ("auto_update", "$(id)"), ("backuplong", "999999999")]:
            with self.subTest(key=key, value=value):
                self.config(**{key: value})
                result = self.run_cli("check-config")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(self.values["server_password"], result.stderr)

    def test_configuration_rejects_duplicates_unknowns_and_missing(self):
        for suffix in ("port=2457\n", "unknown=value\n", "badline\n"):
            self.config()
            with self.conf.open("a") as stream:
                stream.write(suffix)
            self.assertNotEqual(self.run_cli("check-config").returncode, 0)
        self.conf.unlink()
        self.assertNotEqual(self.run_cli("check-config").returncode, 0)

    def test_configuration_rejects_unsafe_permissions_and_ownership(self):
        self.conf.chmod(0o644)
        self.assertNotEqual(self.run_cli("check-config").returncode, 0)
        self.conf.chmod(0o640)
        self.assertNotEqual(self.run_cli("check-config", MOCK_OWNER="99").returncode, 0)
        self.conf.rename(self.conf.with_suffix(".real"))
        self.conf.symlink_to(self.conf.with_suffix(".real"))
        self.assertNotEqual(self.run_cli("check-config").returncode, 0)

    def test_update_order_and_world_persistence(self):
        before = self.conf.read_bytes()
        self.assert_ok(self.run_cli("update"))
        events = self.events()
        self.assertLess(events.index("systemctl stop"), events.index("tar --create"))
        self.assertLess(events.index("tar --create"), events.index("steamcmd "))
        self.assertLess(events.index("steamcmd "), events.index("systemctl start"))
        self.assertIn("+app_update 896660 validate +quit", events)
        self.assertEqual(before, self.conf.read_bytes())
        self.assertEqual((self.saves / "Dedicated.db").read_bytes(), b"original-world\x00\x01")
        self.assertFalse(self.marker().exists())
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 1)

    def test_update_preserves_stopped_state(self):
        self.state("inactive")
        self.assert_ok(self.run_cli("update"))
        self.assertNotIn("systemctl start", self.events())

    def test_stop_failure_prevents_backup_and_update(self):
        self.assertNotEqual(self.run_cli("update", FAIL_STOP="1").returncode, 0)
        self.assertNotIn("tar --create", self.events())
        self.assertNotIn("steamcmd ", self.events())
        self.assertTrue(self.marker().exists())

    def test_backup_failure_prevents_update_and_resumes_server(self):
        self.assertNotEqual(self.run_cli("update", FAIL_BACKUP="1").returncode, 0)
        self.assertNotIn("steamcmd ", self.events())
        self.assertIn("systemctl start", self.events())
        self.assertFalse(self.marker().exists())
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_steam_failure_or_false_success_leaves_server_inhibited(self):
        for failure in ("FAIL_STEAM", "NO_STEAM_CONFIRMATION"):
            with self.subTest(failure=failure):
                self.state("active")
                self.marker().unlink(missing_ok=True)
                (self.root / "events").write_text("")
                self.assertNotEqual(self.run_cli("update", **{failure: "1"}).returncode, 0)
                self.assertNotIn("systemctl start", self.events())
                self.assertTrue(self.marker().exists())
                self.assertNotEqual(self.run_cli("start").returncode, 0)

    def test_backup_restore_round_trip_and_pre_restore_backup(self):
        self.assert_ok(self.run_cli("backup"))
        archive = next(self.backups.glob("*.tar.gz"))
        (self.saves / "Dedicated.db").write_bytes(b"changed-world")
        (self.saves / "later-file").write_text("remove on restore")
        self.assert_ok(self.run_cli("restore", archive))
        self.assertEqual((self.saves / "Dedicated.db").read_bytes(), b"original-world\x00\x01")
        self.assertFalse((self.saves / "later-file").exists())
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 2)
        self.assertFalse(self.marker().exists())

    def test_corrupt_backup_rejected_before_shutdown(self):
        self.assert_ok(self.run_cli("backup"))
        archive = next(self.backups.glob("*.tar.gz"))
        archive.write_bytes(b"corrupt")
        (self.root / "events").write_text("")
        self.assertNotEqual(self.run_cli("restore", archive).returncode, 0)
        self.assertNotIn("systemctl stop", self.events())

    def test_restore_extraction_failure_preserves_current_worlds(self):
        self.assert_ok(self.run_cli("backup"))
        archive = next(self.backups.glob("*.tar.gz"))
        (self.saves / "Dedicated.db").write_bytes(b"changed-world")
        self.assertNotEqual(self.run_cli("restore", archive, FAIL_EXTRACT="1").returncode, 0)
        self.assertEqual((self.saves / "Dedicated.db").read_bytes(), b"changed-world")
        self.assertTrue(self.marker().exists())

    def test_restore_rejects_links(self):
        (self.saves / "link").symlink_to("Dedicated.db")
        self.assert_ok(self.run_cli("backup"))
        archive = next(self.backups.glob("*.tar.gz"))
        self.assertNotEqual(self.run_cli("restore", archive).returncode, 0)

    def test_retention_only_after_success(self):
        self.config(backup_retention="2")
        for _ in range(3):
            self.assert_ok(self.run_cli("backup"))
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 2)
        self.assertNotEqual(self.run_cli("update", FAIL_STEAM="1").returncode, 0)
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 3)

    def test_concurrent_operation_rejected(self):
        lock = (self.root / "run/valheim-management/operation.lock").open("w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertNotEqual(self.run_cli("backup").returncode, 0)
        self.assertNotIn("systemctl stop", self.events())

    def test_stop_works_with_invalid_configuration_and_marker(self):
        self.conf.write_text("invalid")
        self.marker().touch()
        self.assert_ok(self.run_cli("stop"))

    def test_status_and_logs_do_not_require_valid_configuration(self):
        self.conf.unlink()
        self.assert_ok(self.run_cli("status"))
        self.assert_ok(self.run_cli("logs"))

    def test_automatic_update_opt_in(self):
        auto = self.root / "usr/local/libexec/valheim-auto-update"
        self.assertNotEqual(subprocess.run([BASH, str(auto)], env=self.env).returncode, 0)
        self.config(auto_update="1")
        self.assertEqual(subprocess.run([BASH, str(auto)], env=self.env).returncode, 0)

    def test_installer_refuses_direct_execution(self):
        env = {**os.environ}
        env.pop("FUNCTIONS_FILE_PATH", None)
        result = subprocess.run([BASH, str(ROOT / "install/valheim-install.sh")], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"fresh LXC", result.stderr)

    def test_service_uses_graceful_shutdown_and_persistent_inhibition(self):
        service = BUNDLE["/etc/systemd/system/valheim.service"]
        for expected in ("User=valheim", "KillSignal=SIGINT", "SendSIGKILL=no", "Restart=on-failure",
                         "ConditionPathExists=!/etc/valheim/maintenance", "NoNewPrivileges=true"):
            self.assertIn(expected, service)

    def test_repeated_guest_setup_preserves_configuration_and_worlds(self):
        before = self.conf.read_bytes()
        self.state("inactive")
        for _ in range(2):
            result = self.run_installer(var_valheim_name="Do not replace existing name")
            self.assert_ok(result)
            self.assertEqual(self.conf.read_bytes(), before)
            self.assertEqual((self.saves / "Dedicated.db").read_bytes(), b"original-world\x00\x01")

    def test_fresh_configuration_uses_exported_inputs_without_disclosing_password(self):
        self.conf.unlink()
        self.state("inactive")
        result = self.run_installer(var_valheim_name="$(id) Test Community", var_valheim_world="TestWorld",
                                    var_valheim_password="fixture-only-$(id)", var_valheim_public="0",
                                    var_valheim_crossplay="0", var_valheim_retention="3")
        self.assert_ok(result)
        self.assertIn("server_name=$(id) Test Community\n", self.conf.read_text())
        self.assertIn("server_password=fixture-only-$(id)\n", self.conf.read_text())
        self.assertIn("backup_retention=3\n", self.conf.read_text())
        self.assertNotIn("fixture-only-$(id)", result.stdout + result.stderr)
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o640)

    def test_unattended_install_generates_private_password(self):
        self.conf.unlink()
        self.state("inactive")
        result = self.run_installer()
        self.assert_ok(result)
        self.assertIn("server_password=generated-test-fixture-password\n", self.conf.read_text())
        self.assertNotIn("generated-test-fixture-password", result.stdout + result.stderr)

    def test_retention_preserves_incomplete_archives_and_counts_completed_only(self):
        self.config(backup_retention="2")
        incomplete = self.backups / "valheim-20000101T000000Z-000001.tar.gz"
        incomplete.write_bytes(b"incomplete")
        for _ in range(2):
            self.assert_ok(self.run_cli("backup"))
        self.assertEqual(len(list(self.backups.glob("*.tar.gz.sha256"))), 2)
        self.assertTrue(incomplete.exists())

    def test_crossplay_can_be_disabled_without_extra_argument(self):
        self.config(crossplay="0", public="0")
        result = subprocess.run([BASH, str(self.root / "usr/local/libexec/valheim-launch")],
                                env=self.env, capture_output=True, text=True)
        self.assert_ok(result)
        args = json.loads((self.root / "launch-args").read_text())
        self.assertNotIn("-crossplay", args)
        self.assertEqual(args[args.index("-public") + 1], "0")

    def test_failed_start_is_reported(self):
        self.state("inactive")
        self.assertNotEqual(self.run_cli("start", FAIL_START="1").returncode, 0)

    def test_failed_service_state_requires_operator_attention(self):
        self.state("failed")
        self.assertNotEqual(self.run_cli("update").returncode, 0)
        self.assertNotIn("steamcmd ", self.events())

    def test_metadata_declares_all_exported_application_inputs(self):
        metadata = json.loads((ROOT / "docs/valheim-metadata.json").read_text())
        declared = {field["name"] for field in metadata["app_vars"]}
        exported = set(re.findall(r"^export (var_valheim_\w+)=", (ROOT / "ct/valheim.sh").read_text(), re.M))
        self.assertEqual(declared, exported)
        password = next(field for field in metadata["app_vars"] if field["name"] == "var_valheim_password")
        self.assertTrue(password["secret"])


if __name__ == "__main__":
    unittest.main()
