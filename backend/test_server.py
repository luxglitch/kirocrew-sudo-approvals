import hashlib
import hmac
import importlib.util
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("server.py")
WRAPPER_PATH = MODULE_PATH.parent.parent / "bin" / "sudo-with-approval"
SPEC = importlib.util.spec_from_file_location("sudo_approvals_server", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


class AutoApproveWindowTests(unittest.TestCase):
    def tearDown(self):
        server.disarm_auto_approve()

    def test_supported_durations_can_be_armed(self):
        for duration in server.AUTO_APPROVE_DURATIONS:
            state = server.arm_auto_approve(duration)
            self.assertTrue(state["active"])
            self.assertGreater(state["remainingSeconds"], 0)
            self.assertLessEqual(state["remainingSeconds"], duration)

    def test_unknown_and_boolean_durations_are_rejected(self):
        for duration in (True, 0, 60, 10_800, -300):
            with self.assertRaises(ValueError):
                server.arm_auto_approve(duration)

    def test_disarm_is_immediate(self):
        server.arm_auto_approve(300)
        state = server.disarm_auto_approve()
        self.assertFalse(state["active"])
        self.assertIsNone(state["expiresAt"])
        self.assertEqual(state["remainingSeconds"], 0)

    def test_expired_window_fails_closed(self):
        server.arm_auto_approve(300)
        with server._lock:
            server._auto_deadline = time.monotonic() - 1
        state = server.auto_approve_state()
        self.assertFalse(state["active"])


class RuntimeNamespaceTests(unittest.TestCase):
    def test_different_app_roots_get_different_runtime_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            first = pathlib.Path(directory, "home-one", "apps", "sudo-approvals")
            second = pathlib.Path(directory, "home-two", "apps", "sudo-approvals")
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            first_runtime = server.default_runtime_dir(str(first), "1000")
            second_runtime = server.default_runtime_dir(str(second), "1000")
            self.assertNotEqual(first_runtime, second_runtime)
            self.assertRegex(
                first_runtime, r"^/tmp/sudo-approvals-1000-[0-9a-f]{12}$"
            )

    def test_canonical_paths_share_one_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            app_root = pathlib.Path(directory, "real", "sudo-approvals")
            app_root.mkdir(parents=True)
            alias = pathlib.Path(directory, "alias")
            alias.symlink_to(app_root, target_is_directory=True)
            self.assertEqual(
                server.default_runtime_dir(str(app_root), "1000"),
                server.default_runtime_dir(str(alias), "1000"),
            )

    def test_wrapper_and_backend_agree_for_installed_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            app_root = pathlib.Path(directory, "sudo-approvals")
            wrapper = self._copy_wrapper(app_root / "bin" / "sudo-with-approval")
            (app_root / "app.json").write_text("{}", encoding="utf-8")
            self._exercise_wrapper(wrapper, app_root)

    def test_copied_wrapper_uses_kirocrew_home_install(self):
        with tempfile.TemporaryDirectory() as directory:
            crew_home = pathlib.Path(directory, "crew-home")
            app_root = crew_home / "apps" / "sudo-approvals"
            app_root.mkdir(parents=True)
            wrapper = self._copy_wrapper(
                pathlib.Path(directory, "local", "bin", "sudo-with-approval")
            )
            self._exercise_wrapper(
                wrapper, app_root, {"KIROCREW_HOME": str(crew_home)}
            )

    @staticmethod
    def _copy_wrapper(destination: pathlib.Path) -> pathlib.Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WRAPPER_PATH, destination)
        destination.chmod(0o755)
        return destination

    def _exercise_wrapper(self, wrapper, app_root, extra_env=None):
        runtime = pathlib.Path(
            server.default_runtime_dir(str(app_root), server.USER_KEY)
        )
        request_fifo = runtime / "request.fifo"
        response_fifo = runtime / "response.fifo"
        fake_bin = pathlib.Path(app_root).parent / "fake-bin"
        runtime.mkdir(mode=0o700)
        os.mkfifo(request_fifo, 0o600)
        os.mkfifo(response_fifo, 0o600)
        fake_bin.mkdir()
        fake_sudo = fake_bin / "sudo"
        fake_sudo.write_text(
            '#!/usr/bin/env bash\n[[ "$1" == "-A" ]] || exit 64\nshift\nexec "$@"\n',
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        request_text = []

        def approve():
            with request_fifo.open("r", encoding="utf-8") as stream:
                request_text.append(stream.read())
            with response_fifo.open("w", encoding="utf-8") as stream:
                stream.write("y\n")

        responder = threading.Thread(target=approve, daemon=True)
        responder.start()
        env = os.environ.copy()
        for key in (
            "SUDO_APPROVAL_RUNTIME_DIR",
            "SUDO_APPROVAL_REQUEST_FIFO",
            "SUDO_APPROVAL_RESPONSE_FIFO",
            "KIROCREW_HOME",
        ):
            env.pop(key, None)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env.update(extra_env or {})
        try:
            result = subprocess.run(
                [str(wrapper), "true"],
                cwd=app_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            responder.join(timeout=5)
            self.assertFalse(responder.is_alive())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CMD: sudo true", request_text[0])
        finally:
            shutil.rmtree(runtime, ignore_errors=True)


class FifoSafetyTests(unittest.TestCase):
    def test_creates_owner_only_fifo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "request.fifo")
            server._ensure_fifo(path)
            info = os.stat(path)
            self.assertTrue(stat.S_ISFIFO(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    def test_rejects_regular_file_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "request.fifo")
            pathlib.Path(path).write_text("not a fifo")
            with self.assertRaises(RuntimeError):
                server._ensure_fifo(path)


class ProxyVerificationTests(unittest.TestCase):
    def test_valid_signature_and_tampering(self):
        original = server.PROXY_SECRET
        server.PROXY_SECRET = "test-secret"
        try:
            body = b'{"action":"disarm"}'
            timestamp = str(int(time.time()))
            body_hash = hashlib.sha256(body).hexdigest()
            message = f"{timestamp}:POST:/api/auto-approve:{body_hash}"
            signature = hmac.new(
                b"test-secret", message.encode(), hashlib.sha256
            ).hexdigest()
            header = f"{timestamp}:{signature}"
            self.assertTrue(
                server.verify_proxy(header, "POST", "/api/auto-approve", body)
            )
            self.assertFalse(
                server.verify_proxy(header, "POST", "/api/auto-approve", b"tampered")
            )
        finally:
            server.PROXY_SECRET = original


if __name__ == "__main__":
    unittest.main()
