"""Exercise the restorecon shell block with fake tools, never privileged OS changes."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallerTest(unittest.TestCase):
    def run_restorecon(self, exit_code=0):
        source = (Path(__file__).parents[1] / "scripts/install-common.sh").read_text()
        start = source.index('RESTORECON="')
        block = source[start : source.index("export LOCALFORGE_INSTALL_DATA", start)]
        with tempfile.TemporaryDirectory(prefix="lf-install-") as temp:
            root = Path(temp)
            log = root / "restore.log"
            (root / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
            (root / "restorecon").write_text(
                f'#!/bin/sh\nprintf "%s\\n" "$@" > "$RESTORE_LOG"\nexit {exit_code}\n'
            )
            (root / "sudo").chmod(0o700)
            (root / "restorecon").chmod(0o700)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "RESTORE_LOG": str(log),
            }
            result = subprocess.run(
                ["bash", "-ec", block], env=env, capture_output=True, text=True
            )
            return result, log.read_text().splitlines()

    def test_restorecon_runs_after_privileged_install(self):
        result, args = self.run_restorecon()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            args, ["-Rv", "/usr/lib/localforge", "/usr/share/polkit-1/actions"]
        )

    def test_restorecon_failure_aborts_installation(self):
        result, _ = self.run_restorecon(exit_code=17)
        self.assertEqual(result.returncode, 17)
