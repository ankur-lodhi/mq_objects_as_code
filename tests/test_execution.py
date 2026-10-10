"""Local integration tests for the MQSC execution task drafts.

Requires ansible-core/ansible-playbook on PATH. Every play runs against a
Python fake `runmqsc` executable in a temporary directory; no MQ installation,
host, credentials, or provisioning is used.
"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")
CANDIDATE = Path(__file__).resolve().parents[1]


FAKE_RUNMQSC = r'''#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

args = sys.argv[1:]
if len(args) != 2 or args[0] != "-e":
    print("fake runmqsc requires argv: -e <qmgr>", file=sys.stderr)
    sys.exit(20)
qmgr = args[1]
command = sys.stdin.read().strip()
state_path = Path(os.environ["MQSC_FAKE_STATE"])
log_path = Path(os.environ["MQSC_FAKE_LOG"])
state = json.loads(state_path.read_text())
log = json.loads(log_path.read_text()) if log_path.exists() else []
log.append({"argv": args, "qmgr": qmgr, "stdin": command,
            "lang": os.environ.get("LANG"), "lc_all": os.environ.get("LC_ALL")})
log_path.write_text(json.dumps(log))

mode = os.environ.get("MQSC_FAKE_MODE")
if mode == "legacy" and command.startswith("DIS "):
    print("AMQ8147: IBM MQ object not found")
    sys.exit(10)
if mode and command.startswith("DIS "):
    if mode == "unexpected":
        print("AMQ9999E: synthetic queue manager failure")
        sys.exit(10)
    if mode == "rc20":
        print("AMQ9999E: synthetic queue manager unavailable")
        sys.exit(20)
    if mode == "inline":
        print("synthetic diagnostic mentions AMQ8147E: but is not a not-found response")
        sys.exit(10)

name_match = re.search(r"\('((?:[^']|'')*)'\)", command)
if not name_match:
    print("AMQ9998E: synthetic parser could not read object name")
    sys.exit(10)
name = name_match.group(1).replace("''", "'")
objects = state.setdefault("objects", {})
running = state.setdefault("listeners_running", {})

if command.startswith("DIS QUEUE("):
    category = "queues"
    found = name in objects.setdefault(category, [])
elif command.startswith("DIS CHANNEL("):
    category = "channels"
    found = name in objects.setdefault(category, [])
elif command.startswith("DIS TOPIC("):
    category = "topics"
    found = name in objects.setdefault(category, [])
elif command.startswith("DIS SUB("):
    category = "subscriptions"
    found = name in objects.setdefault(category, [])
elif command.startswith("DIS LISTENER("):
    category = "listeners"
    found = name in objects.setdefault(category, [])
elif command.startswith("DIS LSSTATUS("):
    found = name in objects.setdefault("listeners", []) and running.get(name, False)
    if found:
        print("AMQ8631I: Display Listener Status details. LISTENER(%s) STATUS(RUNNING)" % name)
        sys.exit(0)
    print("AMQ8147E: IBM MQ object %s not found" % name)
    sys.exit(10)
elif command.startswith("START LISTENER("):
    if name not in objects.setdefault("listeners", []):
        print("AMQ8147E: IBM MQ object %s not found" % name)
        sys.exit(10)
    running[name] = True
    state_path.write_text(json.dumps(state))
    print("AMQ8021I: Request to start listener accepted")
    sys.exit(0)
elif command.startswith("DEFINE QLOCAL(") or command.startswith("DEFINE QREMOTE(") or command.startswith("DEFINE QALIAS("):
    category = "queues"
elif command.startswith("DEFINE CHANNEL("):
    category = "channels"
elif command.startswith("DEFINE TOPIC("):
    category = "topics"
elif command.startswith("DEFINE SUB("):
    category = "subscriptions"
elif command.startswith("DEFINE LISTENER("):
    category = "listeners"
else:
    print("AMQ9997E: unsupported synthetic command")
    sys.exit(10)

if command.startswith("DIS "):
    if found:
        print("AMQ8409: Display object details. %s(%s)" % (category.upper(), name))
        sys.exit(0)
    print("AMQ8147E: IBM MQ object %s not found" % name)
    sys.exit(10)

existing = objects.setdefault(category, [])
if name in existing:
    print("AMQ8101E: synthetic duplicate object")
    sys.exit(10)
existing.append(name)
if category == "listeners":
    running.setdefault(name, False)
state_path.write_text(json.dumps(state))
print("AMQ8006I: synthetic object created")
'''

def production_filter_dir() -> Path:
    """Locate the repository's real playbooks/filter_plugins directory."""
    for base in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        candidate = base / "playbooks" / "filter_plugins"
        if candidate.is_dir() and any(candidate.glob("*.py")):
            return candidate
    raise AssertionError("playbooks/filter_plugins with production MQSC filters was not found")


FILTER_SOURCE = production_filter_dir()



class ExecutionTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ANSIBLE_PLAYBOOK:
            raise unittest.SkipTest("ansible-playbook is required for execution task integration tests")
        for name in (
            "configure_queues.yml", "configure_channels.yml", "configure_listeners.yml",
            "configure_topics.yml", "configure_subscriptions.yml",
        ):
            if not (CANDIDATE / "tasks" / name).is_file():
                raise AssertionError("missing candidate task: " + name)

    def run_task(self, task_name: str, object_file: str, config: str, *, state=None,
                 check=False, mode="", assert_success=True):
        with tempfile.TemporaryDirectory(prefix="mqsc-ansible-test-") as temp:
            root = Path(temp)
            (root / "tasks").mkdir()
            (root / "objects" / "localhost").mkdir(parents=True)
            (root / "inventory").mkdir()
            (root / "bin").mkdir()
            (root / "filter_plugins").mkdir()
            (root / "playbooks").mkdir()

            shutil.copyfile(CANDIDATE / "tasks" / task_name, root / "tasks" / task_name)
            (root / "objects" / "localhost" / object_file).write_text(config, encoding="utf-8")
            for plugin in FILTER_SOURCE.glob("*.py"):
                shutil.copyfile(plugin, root / "filter_plugins" / plugin.name)
            fake = root / "bin" / "runmqsc"
            fake.write_text(FAKE_RUNMQSC, encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            ansible_cfg = textwrap.dedent("""\
                [defaults]
                inventory = inventory/hosts
                filter_plugins = filter_plugins
                host_key_checking = False
                retry_files_enabled = False
                interpreter_python = auto_silent
            """)
            (root / "ansible.cfg").write_text(ansible_cfg, encoding="utf-8")
            (root / "inventory" / "hosts").write_text(
                "localhost ansible_connection=local ansible_python_interpreter=" + sys.executable + "\n",
                encoding="utf-8",
            )
            playbook = textwrap.dedent("""\
                ---
                - hosts: localhost
                  gather_facts: false
                  vars:
                    mq_dir: "{mq_dir}"
                    mq_user: "{mq_user}"
                  tasks:
                    - ansible.builtin.include_tasks: tasks/{task}
            """).format(mq_dir=str(root / "bin"), mq_user=getpass.getuser(), task=task_name)
            (root / "playbooks" / "playbook.yml").write_text(
                playbook.replace("tasks/", "../tasks/"), encoding="utf-8"
            )

            state_path = root / "state.json"
            log_path = root / "invocations.json"
            state_path.write_text(json.dumps(state or {"objects": {}, "listeners_running": {}}), encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "ANSIBLE_CONFIG": str(root / "ansible.cfg"),
                "ANSIBLE_LOCAL_TEMP": str(root / "local-tmp"),
                "MQSC_FAKE_STATE": str(state_path),
                "MQSC_FAKE_LOG": str(log_path),
                "MQSC_FAKE_MODE": mode,
            })
            (root / "local-tmp").mkdir()
            cmd = [ANSIBLE_PLAYBOOK, "-i", str(root / "inventory" / "hosts"), str(root / "playbooks" / "playbook.yml")]
            if check:
                cmd.append("--check")
            result = subprocess.run(cmd, cwd=root, env=env, text=True, capture_output=True, timeout=30)
            if assert_success:
                self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
            log = json.loads(log_path.read_text()) if log_path.exists() else []
            final_state = json.loads(state_path.read_text())
            return result, log, final_state

    @staticmethod
    def invocations(log, prefix):
        return [entry for entry in log if entry["stdin"].startswith(prefix)]

    def test_queue_create_is_idempotent_and_probe_omits_maxdepth(self):
        config = """mq_queues:
  - name: LOCAL.Q
    type: local
    host_qmgr: QM1
  - name: REMOTE.Q
    type: remote
    host_qmgr: QM1
    rname: TARGET.Q
    remote_qmgr: QM2
    xmit_queue: XMIT.Q
  - name: ALIAS.Q
    type: alias
    host_qmgr: QM1
    target: LOCAL.Q
  - name: LOCAL.Q2
    type: local
    host_qmgr: QM2
"""
        result, first_log, first_state = self.run_task("configure_queues.yml", "queues.yml", config)
        self.assertIn("LOCAL.Q", first_state["objects"]["queues"])
        self.assertIn("REMOTE.Q", first_state["objects"]["queues"])
        self.assertIn("ALIAS.Q", first_state["objects"]["queues"])
        self.assertIn("LOCAL.Q2", first_state["objects"]["queues"])
        displays = self.invocations(first_log, "DIS QUEUE(")
        self.assertEqual(len(displays), 4)
        self.assertTrue(all("MAXDEPTH" not in entry["stdin"] for entry in displays))
        creates = [entry for entry in first_log if entry["stdin"].startswith("DEFINE ")]
        self.assertEqual(len(creates), 4)
        self.assertIn("MAXDEPTH(5000)", creates[0]["stdin"])
        self.assertIn("TARGETTYPE(QUEUE)", creates[2]["stdin"])
        self.assertIn("QM2", [entry["qmgr"] for entry in first_log])

        _, second_log, _ = self.run_task(
            "configure_queues.yml", "queues.yml", config,
            state=first_state,
        )
        self.assertFalse([entry for entry in second_log if entry["stdin"].startswith("DEFINE ")])
        self.assertEqual([entry["qmgr"] for entry in second_log], ["QM1", "QM1", "QM1", "QM2"])

    def test_channel_topic_and_subscription_missing_objects_are_created(self):
        fixtures = [
            ("configure_channels.yml", "channels.yml",
             "mq_channel:\n  - name: C1\n    type: RCVR\n    host_qmgr: QM1\n", "DEFINE CHANNEL('C1') CHLTYPE(RCVR)"),
            ("configure_topics.yml", "topics.yml",
             "mq_topics:\n  - name: T1\n    topic_str: /orders\n    host_qmgr: QM1\n", "DEFINE TOPIC('T1') TOPICSTR('/orders')"),
            ("configure_subscriptions.yml", "subscriptions.yml",
             "mq_subscriptions:\n  - name: S1\n    topic_str: /orders\n    dest: DEST.Q\n    host_qmgr: QM1\n", "DEFINE SUB('S1') TOPICSTR('/orders') DEST('DEST.Q')"),
        ]
        for task, vars_file, config, expected in fixtures:
            with self.subTest(task=task):
                _, log, state = self.run_task(task, vars_file, config)
                self.assertIn(expected, [entry["stdin"] for entry in log])
                self.assertEqual(len([entry for entry in log if entry["stdin"].startswith("DEFINE ")]), 1)
                self.assertTrue(state["objects"])
                self.assertTrue(all(entry["argv"][0:1] == ["-e"] for entry in log))
                _, repeat_log, _ = self.run_task(task, vars_file, config, state=state)
                self.assertFalse([entry for entry in repeat_log if entry["stdin"].startswith("DEFINE ")])

    def test_rc10_without_anchored_object_not_found_fails_closed(self):
        result, log, state = self.run_task(
            "configure_topics.yml", "topics.yml",
            "mq_topics:\n  - name: T1\n    topic_str: /orders\n    host_qmgr: QM1\n",
            mode="unexpected", assert_success=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([entry for entry in log if entry["stdin"].startswith("DEFINE ")])
        self.assertFalse(state["objects"].get("topics"))

    def test_rc20_and_inline_object_code_both_fail_closed(self):
        config = "mq_topics:\n  - name: T1\n    topic_str: /orders\n    host_qmgr: QM1\n"
        for mode in ("rc20", "inline"):
            with self.subTest(mode=mode):
                result, log, state = self.run_task(
                    "configure_topics.yml", "topics.yml", config,
                    mode=mode, assert_success=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse([entry for entry in log if entry["stdin"].startswith("DEFINE ")])
                self.assertFalse(state["objects"].get("topics"))

    def test_missing_object_message_without_severity_suffix(self):
        _, log, _ = self.run_task(
            "configure_topics.yml", "topics.yml",
            "mq_topics:\n  - name: T1\n    topic_str: /orders\n    host_qmgr: QM1\n",
            mode="legacy",
        )
        self.assertEqual(len(self.invocations(log, "DEFINE TOPIC(")), 1)

    def test_check_mode_runs_read_probe_predicts_create_without_writing(self):
        result, log, state = self.run_task(
            "configure_topics.yml", "topics.yml",
            "mq_topics:\n  - name: T1\n    topic_str: /orders\n    host_qmgr: QM1\n",
            check=True,
        )
        self.assertIn("Would create topic T1 on QM1", result.stdout)
        self.assertIn("changed=1", result.stdout)
        self.assertTrue(any(entry["stdin"] == "DIS TOPIC('T1')" for entry in log))
        self.assertFalse([entry for entry in log if entry["stdin"].startswith("DEFINE ")])
        self.assertFalse(state["objects"].get("topics"))

    def test_topic_text_is_literal_on_stdin_and_never_shell_expanded(self):
        with tempfile.TemporaryDirectory(prefix="mqsc-marker-") as marker_dir:
            marker = Path(marker_dir) / "should-not-exist"
            topic_string = "$HOME; touch " + str(marker) + "; 'quoted'"
            config = "mq_topics:\n  - name: T1\n    topic_str: " + json.dumps(topic_string) + "\n    host_qmgr: QM1\n"
            _, log, _ = self.run_task("configure_topics.yml", "topics.yml", config)
            create = next(entry["stdin"] for entry in log if entry["stdin"].startswith("DEFINE TOPIC"))
            self.assertIn("TOPICSTR('$HOME; touch " + str(marker) + "; ''quoted''')", create)
            self.assertIn("$HOME", create)
            self.assertFalse(marker.exists())

    def test_listener_definition_includes_ip_and_starts_after_define(self):
        config = """mq_listeners:
  - name: L1
    port: 1515
    control: QMGR
    host_address: 127.0.0.1
    host_qmgr: QM1
"""
        _, log, state = self.run_task("configure_listeners.yml", "listeners.yml", config)
        define_index = next(i for i, entry in enumerate(log) if entry["stdin"].startswith("DEFINE LISTENER"))
        status_index = next(i for i, entry in enumerate(log) if entry["stdin"].startswith("DIS LSSTATUS"))
        start_index = next(i for i, entry in enumerate(log) if entry["stdin"].startswith("START LISTENER"))
        self.assertIn("IPADDR('127.0.0.1')", log[define_index]["stdin"])
        self.assertLess(define_index, status_index)
        self.assertLess(status_index, start_index)
        self.assertTrue(state["listeners_running"]["L1"])


    def test_check_mode_existing_listener_probes_runtime_and_predicts_start_only_if_stopped(self):
        config = """mq_listeners:
  - name: L1
    port: 1515
    control: QMGR
    host_address: 127.0.0.1
    host_qmgr: QM1
"""
        stopped_state = {"objects": {"listeners": ["L1"]}, "listeners_running": {"L1": False}}
        result, stopped_log, stopped_after = self.run_task(
            "configure_listeners.yml", "listeners.yml", config,
            state=stopped_state, check=True,
        )
        self.assertIn("Would start listener L1 on QM1", result.stdout)
        self.assertTrue(any(entry["stdin"] == "DIS LSSTATUS('L1')" for entry in stopped_log))
        self.assertFalse([entry for entry in stopped_log if entry["stdin"].startswith(("DEFINE ", "START "))])
        self.assertFalse(stopped_after["listeners_running"]["L1"])

        running_state = {"objects": {"listeners": ["L1"]}, "listeners_running": {"L1": True}}
        running_result, running_log, _ = self.run_task(
            "configure_listeners.yml", "listeners.yml", config,
            state=running_state, check=True,
        )
        self.assertNotIn("Would start listener L1 on QM1", running_result.stdout)
        self.assertTrue(any(entry["stdin"] == "DIS LSSTATUS('L1')" for entry in running_log))
        self.assertFalse([entry for entry in running_log if entry["stdin"].startswith(("DEFINE ", "START "))])


if __name__ == "__main__":
    unittest.main()
