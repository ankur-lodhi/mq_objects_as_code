"""Run actual Ansible against an isolated local fixture, never an MQ server."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = shutil.which("ansible-playbook")


@unittest.skipUnless(ANSIBLE, "requires a supported Ansible control node (Linux/macOS/WSL)")
class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.host = "preflight_test"
        self.objects = self.root / "objects" / self.host
        self.objects.mkdir()
        self.marker = self.root / "mutation.txt"
        # Instrument configuration includes; validation/discovery is production
        # code. A queue mutation before a later validation fails must be visible.
        for task in (self.root / "tasks").glob("configure_*.yml"):
            task.write_text(yaml.safe_dump([{
                "name": "Record that configuration began",
                "ansible.builtin.copy": {"dest": str(self.marker), "content": "mutation"},
            }]), encoding="utf-8")

    def write_objects(self, filename, data):
        (self.objects / filename).write_text(yaml.safe_dump(data), encoding="utf-8")

    def run_play(self):
        inventory = self.root / "test-inventory.json"
        inventory.write_text(json.dumps({"mqservers": {"hosts": {self.host: {
            "ansible_connection": "local", "ansible_python_interpreter": sys.executable,
            "ansible_become": False,
        }}}}), encoding="utf-8")
        env = {**os.environ, "ANSIBLE_NOCOLOR": "1", "ANSIBLE_LOCAL_TEMP": str(self.root / ".ansible")}
        return subprocess.run([ANSIBLE, "-i", str(inventory), "playbooks/configure_mq.yml"],
                              cwd=self.root, env=env, text=True, capture_output=True, timeout=90)

    def valid_queue(self):
        self.write_objects("queues.yml", {"mq_queues": [
            {"name": "TEST.Q", "type": "local", "host_qmgr": "QM1", "max_depth": 5000}
        ]})

    def test_late_validation_prevents_earlier_mutation(self):
        self.valid_queue()
        self.write_objects("topics.yml", {"mq_topics": [
            {"name": "", "topic_str": "/invalid", "host_qmgr": "QM1"}
        ]})
        result = self.run_play()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists(), result.stdout + result.stderr)

    def test_optional_files_can_be_absent(self):
        self.valid_queue()
        result = self.run_play()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists())

    def test_missing_directory_fails(self):
        self.objects.rmdir()
        result = self.run_play()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Missing objects/", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_unknown_yaml_is_not_silently_ignored(self):
        self.write_objects("quuees.yml", {"mq_queues": []})
        result = self.run_play()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Unsupported object files", result.stdout)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
