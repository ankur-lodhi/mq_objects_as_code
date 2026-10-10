"""Isolated Ansible integration tests for AUTHREC/CHLAUTH safety.

Run from the project root with ``python -m unittest tests.test_security`` and
ansible-core installed. The test copies the whole repository to a temporary
folder and invokes only the security validators/tasks against a fake runmqsc.
No IBM MQ installation, remote hosts, or real queue managers are contacted.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("MQ_SECURITY_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")


class SecurityPlaybookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name == "nt":
            raise unittest.SkipTest("ansible-core is a POSIX controller; run integration tests on Linux")
        if not ANSIBLE_PLAYBOOK:
            raise unittest.SkipTest("ansible-playbook is not installed")
        if not (PROJECT_ROOT / "playbooks" / "filter_plugins" / "mqsc.py").is_file():
            raise unittest.SkipTest("run this test against the repository with the security filter installed")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mq-security-")
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(
            PROJECT_ROOT,
            self.root,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv"),
        )
        self.fake_dir = self.root / "fake mq $HOME"
        self.fake_dir.mkdir()
        self.log = Path(self.temp.name) / "runmqsc.jsonl"
        self._install_fake_runmqsc()
        (self.root / "inventory-test.ini").write_text(
            "[mqservers]\nmqserver1 ansible_connection=local\n", encoding="utf-8"
        )
        self.playbook = self.root / "playbooks" / "security_test.yml"
        self.playbook.write_text(
            textwrap.dedent(
                """\
                ---
                - name: Security tasks in a disposable local test
                  hosts: mqservers
                  gather_facts: false
                  tasks:
                    - ansible.builtin.include_tasks: ../validation/validate_authrecords.yml
                    - ansible.builtin.include_tasks: ../validation/validate_chlauth.yml
                    - ansible.builtin.include_tasks: ../tasks/configure_authrecords.yml
                    - ansible.builtin.include_tasks: ../tasks/configure_chlauth.yml
                """
            ),
            encoding="utf-8",
        )
        self.write_inventory()
        self.write_chlauth()

    def tearDown(self):
        self.temp.cleanup()

    def write_inventory(self, auth=None):
        auth = auth or [
            {
                "profile": "TESTQ",
                "objtype": "QUEUE",
                "principal": "app",
                "authadd": ["GET", "PUT"],
                "host_qmgr": "QM1",
            }
        ]
        self.write_yaml(
            self.root / "objects" / "mqserver1" / "mq_auth_records.yml",
            {"mq_auth_records": auth},
        )

    def write_chlauth(self, records=None):
        if records is None:
            records = [
                {
                    "channel": "CH.TEST",
                    "type": "BLOCKUSER",
                    "userlist": "*MQADMIN",
                    "action": "ADD",
                    "host_qmgr": "QM1",
                },
                {
                    "channel": "CH.TEST",
                    "type": "SSLPEERMAP",
                    "usersrc": "NOACCESS",
                    "action": "ADD",
                    "sslpeer": "CN=Client",
                    "address": "10.0.0.0/24",
                    "host_qmgr": "QM1",
                },
            ]
        self.write_yaml(self.root / "objects" / "mqserver1" / "mq_chlauth.yml", {"mq_chlauth": records})

    @staticmethod
    def write_yaml(path, data):
        # JSON is valid YAML 1.2 and keeps adversarial strings escaped safely.
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _install_fake_runmqsc(self):
        script = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
command = sys.stdin.read().strip()
with open(os.environ["FAKE_MQ_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps({"argv": args, "stdin": command}) + "\n")
mode = os.environ.get("FAKE_CHLAUTH_MODE", "absent")
if command.startswith("DIS QUEUE("):
    sys.stdout.write("AMQ8147E: IBM MQ object not found.\n")
    sys.exit(10)
if command.startswith("DEFINE QLOCAL("):
    sys.stdout.write("AMQ8006I: Object created.\n")
    sys.exit(0)
if command.startswith("DISPLAY CHLAUTH"):
    if mode == "failed":
        sys.stderr.write("AMQ9999E: Simulated unexpected query failure.\n")
        sys.exit(20)
    if mode == "absent":
        sys.stdout.write("AMQ8884E: Channel authentication record not found.\n")
        sys.exit(10)
    if "TYPE(BLOCKUSER)" in command:
        sys.stdout.write("AMQ8878I: Display channel authentication record details.\nCHLAUTH(CH.TEST) TYPE(BLOCKUSER)\nUSERLIST(*MQADMIN)\n")
        sys.exit(0)
    sys.stdout.write("AMQ8878I: Display channel authentication record details.\n")
    sys.stdout.write(os.environ.get("FAKE_SSLPEERMAP_OUTPUT", ""))
    sys.exit(0)
sys.exit(0)
'''
        target = self.fake_dir / "runmqsc"
        target.write_text(script, encoding="utf-8", newline="\n")
        target.chmod(0o755)

    def run_playbook(self, check=False, mode="absent", ssl_output=""):
        env = os.environ.copy()
        env.update(
            FAKE_MQ_LOG=str(self.log),
            FAKE_CHLAUTH_MODE=mode,
            FAKE_SSLPEERMAP_OUTPUT=ssl_output,
        )
        args = [
            ANSIBLE_PLAYBOOK,
            "-i",
            str(self.root / "inventory-test.ini"),
            str(self.playbook),
            "-e",
            json.dumps({"mq_dir": str(self.fake_dir), "mq_user": "test-user",
                        "ansible_become": False, "ansible_python_interpreter": sys.executable}),
        ]
        if check:
            args.append("--check")
        return subprocess.run(args, cwd=self.root, env=env, text=True, capture_output=True, check=False, timeout=90)

    def run_main_playbook(self):
        env = os.environ.copy()
        env.update(
            FAKE_MQ_LOG=str(self.log),
            FAKE_CHLAUTH_MODE="absent",
            FAKE_SSLPEERMAP_OUTPUT="",
        )
        args = [
            ANSIBLE_PLAYBOOK,
            "-i", str(self.root / "inventory-test.ini"),
            str(self.root / "playbooks" / "configure_mq.yml"),
            "-e", json.dumps({"mq_dir": str(self.fake_dir), "mq_user": "test-user",
                              "ansible_become": False, "ansible_python_interpreter": sys.executable}),
        ]
        return subprocess.run(args, cwd=self.root, env=env, text=True, capture_output=True, check=False, timeout=90)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def set_commands(self):
        return [call["stdin"] for call in self.calls() if call["stdin"].startswith("SET ")]

    def test_absent_records_add_authrec_and_both_chlauth_records(self):
        result = self.run_playbook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sets = self.set_commands()
        self.assertTrue(any(command.startswith("SET AUTHREC") for command in sets))
        self.assertTrue(any("TYPE(BLOCKUSER)" in command and "ACTION(ADD)" in command for command in sets))
        self.assertTrue(any("TYPE(SSLPEERMAP)" in command and "ADDRESS('10.0.0.0/24')" in command for command in sets))
        self.assertFalse(any("REFRESH SECURITY" in call["stdin"] for call in self.calls()))

    def test_existing_exact_blockuser_and_sslpeer_address_identity_are_not_rewritten(self):
        ssl = "CHLAUTH(CH.TEST) TYPE(SSLPEERMAP)\nSSLPEER(CN=Client) SSLCERTI( ) ADDRESS(10.0.0.0/24) USERSRC(NOACCESS)\n"
        result = self.run_playbook(mode="existing", ssl_output=ssl)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sets = self.set_commands()
        self.assertTrue(any(command.startswith("SET AUTHREC") for command in sets))
        self.assertFalse(any("SET CHLAUTH" in command for command in sets))

    def test_other_sslpeer_or_issuer_does_not_suppress_requested_rule(self):
        fixtures = [
            "CHLAUTH(CH.TEST) TYPE(SSLPEERMAP)\nSSLPEER(CN=Other) SSLCERTI( ) ADDRESS(10.0.0.0/24)\n",
            "CHLAUTH(CH.TEST) TYPE(SSLPEERMAP)\nSSLPEER(CN=Client) SSLCERTI(CN=CA) ADDRESS(10.0.0.0/24)\n",
        ]
        for ssl in fixtures:
            with self.subTest(ssl=ssl):
                self.log.unlink(missing_ok=True)
                result = self.run_playbook(mode="existing", ssl_output=ssl)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                commands = self.set_commands()
                self.assertTrue(any("TYPE(SSLPEERMAP)" in command for command in commands))

    def test_distinct_address_for_same_ssl_identity_does_not_suppress_target(self):
        ssl = "CHLAUTH(CH.TEST) TYPE(SSLPEERMAP)\nSSLPEER(CN=Client) SSLCERTI( ) ADDRESS(192.0.2.0/24)\n"
        result = self.run_playbook(mode="existing", ssl_output=ssl)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("ADDRESS('10.0.0.0/24')" in command for command in self.set_commands()))

    def test_unexpected_chlauth_probe_failure_fails_closed(self):
        result = self.run_playbook(mode="failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("SET CHLAUTH" in command for command in self.set_commands()))

    def test_check_mode_executes_probes_but_no_writes(self):
        result = self.run_playbook(check=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any(command.startswith("DISPLAY CHLAUTH") for command in [c["stdin"] for c in self.calls()]))
        self.assertEqual(self.set_commands(), [])

    def test_late_invalid_chlauth_and_control_issuer_prevent_all_writes(self):
        self.write_chlauth(
            [
                {
                    "channel": "CH.TEST", "type": "BLOCKUSER", "userlist": "*MQADMIN",
                    "action": "ADD", "host_qmgr": "QM1",
                },
                {
                    "channel": "CH.TEST", "type": "SSLPEERMAP", "usersrc": "NOACCESS",
                    "action": "ADD", "sslpeer": "CN=Client", "sslcerti": "CA\nSET CHLAUTH(*) TYPE(BLOCKUSER) ACTION(REMOVE)",
                    "host_qmgr": "QM1",
                },
            ]
        )
        result = self.run_playbook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_malformed_security_collection_prevents_all_writes(self):
        self.write_chlauth({"bad": "mapping instead of list"})
        result = self.run_playbook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_unsupported_security_field_prevents_all_writes(self):
        records = [
            {"channel": "CH.TEST", "type": "BLOCKUSER", "userlist": "*MQADMIN",
             "action": "ADD", "host_qmgr": "QM1", "ignored_extra": "silently-dangerous"}
        ]
        self.write_chlauth(records)
        result = self.run_playbook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_authrec_set_variants_validate_and_render(self):
        self.write_inventory([{
            "profile": "TESTQ", "objtype": "QUEUE", "principal": "app",
            "authadd": ["SET", "SETALL", "SETID"], "host_qmgr": "QM1",
        }])
        result = self.run_playbook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("AUTHADD(SET,SETALL,SETID)" in command for command in self.set_commands()))

    def test_main_preflight_rejects_late_security_error_before_valid_queue_mutation(self):
        objects = self.root / "objects" / "mqserver1"
        for path in objects.glob("*.yml"):
            if path.name not in {"mq_auth_records.yml", "mq_chlauth.yml"}:
                path.unlink()
        self.write_yaml(objects / "queues.yml", {
            "mq_queues": [{"name": "VALID.Q", "type": "local", "host_qmgr": "QM1"}]
        })
        self.write_chlauth([
            {"channel": "CH.TEST", "type": "BLOCKUSER", "userlist": "*MQADMIN",
             "action": "ADD", "host_qmgr": "QM1"},
            {"channel": "CH.TEST", "type": "SSLPEERMAP", "usersrc": "NOACCESS",
             "action": "ADD", "sslpeer": "CN=Client",
             "sslcerti": "CA\nSET CHLAUTH(*) TYPE(BLOCKUSER) ACTION(REMOVE)",
             "host_qmgr": "QM1"},
        ])
        result = self.run_main_playbook()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("control characters", result.stdout + result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
