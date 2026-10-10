"""Standalone runtime tests for the MQ YAML validators.

Run with ``python -m unittest discover -s tests`` in an environment that has
ansible-playbook, PyYAML, and Python available. Each case copies only the
validator under test into a temporary project that mirrors its expected
``validation/`` and ``objects/<inventory_hostname>/`` layout.
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_PLAYBOOK = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")

VALIDATORS = {
    "queues": ("queue_validation.yml", "queues.yml", "mq_queues"),
    "channels": ("validate_channels.yml", "channels.yml", "mq_channel"),
    "listeners": ("validate_listeners.yml", "listeners.yml", "mq_listeners"),
    "topics": ("validate_topics.yml", "topics.yml", "mq_topics"),
    "subscriptions": ("validate_subscriptions.yml", "subscriptions.yml", "mq_subscriptions"),
}


@unittest.skipUnless(ANSIBLE_PLAYBOOK, "ansible-playbook is not available")
class ValidationRuntimeTests(unittest.TestCase):
    maxDiff = None

    def sample(self, kind: str):
        _, filename, root_var = VALIDATORS[kind]
        source = PROJECT_ROOT / "objects" / "mqserver1" / filename
        with source.open(encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
        return copy.deepcopy(loaded[root_var])

    def run_validator(self, kind: str, value, *, root_override: bool = False):
        validator_filename, object_filename, root_var = VALIDATORS[kind]
        with tempfile.TemporaryDirectory(prefix="mq-validation-") as temp_name:
            project = Path(temp_name)
            validation_dir = project / "validation"
            object_dir = project / "objects" / "localhost"
            validation_dir.mkdir(parents=True)
            object_dir.mkdir(parents=True)
            shutil.copy2(PROJECT_ROOT / "validation" / validator_filename, validation_dir / validator_filename)

            root_value = value if root_override else {root_var: value}
            with (object_dir / object_filename).open("w", encoding="utf-8", newline="\n") as stream:
                yaml.safe_dump(root_value, stream, sort_keys=False, allow_unicode=True)

            playbook = project / "validate.yml"
            playbook.write_text(
                "---\n"
                "- name: Validate MQ fixture\n"
                "  hosts: localhost\n"
                "  gather_facts: false\n"
                "  tasks:\n"
                f"    - name: Run {kind} validator\n"
                f"      ansible.builtin.include_tasks: validation/{validator_filename}\n",
                encoding="utf-8",
                newline="\n",
            )
            env = os.environ.copy()
            env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
            env["ANSIBLE_RETRY_FILES_ENABLED"] = "False"
            result = subprocess.run(
                [ANSIBLE_PLAYBOOK, "-i", "localhost,", "-c", "local", str(playbook)],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )
            return result

    def assert_passes(self, kind: str, value, **kwargs):
        result = self.run_validator(kind, value, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_fails(self, kind: str, value, **kwargs):
        result = self.run_validator(kind, value, **kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repository_samples_pass(self):
        for kind in VALIDATORS:
            with self.subTest(kind=kind):
                self.assert_passes(kind, self.sample(kind))

    def test_case_sensitive_object_names_and_different_queue_managers_are_allowed(self):
        for kind in VALIDATORS:
            with self.subTest(kind=kind, case="case-sensitive"):
                records = self.sample(kind)
                other = copy.deepcopy(records[0])
                other["name"] = other["name"].swapcase()
                if kind == "listeners":
                    other["port"] += 1  # Keep the listener endpoint distinct.
                self.assert_passes(kind, records + [other])

            with self.subTest(kind=kind, case="different-qmgr"):
                records = self.sample(kind)
                other = copy.deepcopy(records[0])
                other["host_qmgr"] = "QMOTHER"
                if kind == "listeners":
                    other["port"] += 1
                self.assert_passes(kind, records + [other])

    def test_same_queue_name_with_different_type_is_duplicate(self):
        record = copy.deepcopy(self.sample("queues")[0])
        other = {"name": record["name"], "type": "xmitq", "host_qmgr": record["host_qmgr"]}
        self.assert_fails("queues", [record, other])

    def test_same_channel_name_with_different_type_is_duplicate(self):
        sender = next(item for item in self.sample("channels") if item["type"] == "SDR")
        receiver = {"name": sender["name"], "type": "RCVR", "host_qmgr": sender["host_qmgr"]}
        self.assert_fails("channels", [sender, receiver])

    def test_duplicate_topic_and_subscription_names_ignore_other_fields(self):
        topic = copy.deepcopy(self.sample("topics")[0])
        duplicate_topic = {**topic, "topic_str": "/different/topic"}
        self.assert_fails("topics", [topic, duplicate_topic])

        subscription = copy.deepcopy(self.sample("subscriptions")[0])
        duplicate_subscription = {**subscription, "topic_str": "/different/topic"}
        self.assert_fails("subscriptions", [subscription, duplicate_subscription])

    def test_duplicate_listener_identity_and_endpoint_are_rejected(self):
        listener = copy.deepcopy(self.sample("listeners")[0])
        self.assert_fails("listeners", [listener, copy.deepcopy(listener)])

        other_name_same_endpoint = {**listener, "name": "ANOTHER.LISTENER"}
        self.assert_fails("listeners", [listener, other_name_same_endpoint])

    def test_invalid_root_shapes_and_malformed_records_fail(self):
        for root in (None, "not-a-list", {"name": "TEST"}):
            with self.subTest(root=root):
                self.assert_fails("queues", root)
        self.assert_fails("queues", {}, root_override=True)

        self.assert_fails("queues", ["not-a-mapping"])
        self.assert_fails("channels", [{"name": "MISSING.FIELDS"}])

    def test_remote_queue_requires_rname_but_accepts_empty_rname(self):
        remote = copy.deepcopy(next(item for item in self.sample("queues") if item["type"] == "remote"))
        without_rname = {key: value for key, value in remote.items() if key != "rname"}
        self.assert_fails("queues", [without_rname])
        remote["rname"] = ""
        self.assert_passes("queues", [remote])

    def test_max_depth_bounds_and_rejects_booleans(self):
        queue = copy.deepcopy(self.sample("queues")[0])
        for value in (0, 999_999_999):
            with self.subTest(value=value):
                self.assert_passes("queues", [{**queue, "max_depth": value}])
        for value in (-1, 1_000_000_000, True, "5000"):
            with self.subTest(value=value):
                self.assert_fails("queues", [{**queue, "max_depth": value}])

    def test_alias_target_type_defaults_to_queue_and_requires_canonical_enum(self):
        alias = copy.deepcopy(next(item for item in self.sample("queues") if item["type"] == "alias"))
        alias.pop("target_type", None)
        self.assert_passes("queues", [alias])
        self.assert_fails("queues", [{**alias, "target_type": "queue"}])
        self.assert_fails("queues", [{**alias, "target_type": "PROCESS"}])

    def test_subscription_names_allow_spaces_and_more_than_48_characters(self):
        subscription = copy.deepcopy(self.sample("subscriptions")[0])
        subscription["name"] = "A legal subscription name longer than 48 characters"
        self.assert_passes("subscriptions", [subscription])
        self.assert_fails("subscriptions", [{**subscription, "name": "SUB*NAME"}])

    def test_control_characters_are_rejected_before_configuration(self):
        queue = copy.deepcopy(self.sample("queues")[0])
        self.assert_fails("queues", [{**queue, "name": "BAD\nNAME"}])

        channel = copy.deepcopy(next(item for item in self.sample("channels") if item["type"] == "SDR"))
        self.assert_fails("channels", [{**channel, "target_host": "bad\nhost"}])

    def test_wildcard_names_and_invalid_channel_ports_fail(self):
        queue = copy.deepcopy(self.sample("queues")[0])
        self.assert_fails("queues", [{**queue, "name": "BAD*NAME"}])

        channel = copy.deepcopy(next(item for item in self.sample("channels") if item["type"] == "SDR"))
        for value in (True, "1417", 1023, 1414, 65536):
            with self.subTest(port=value):
                self.assert_fails("channels", [{**channel, "target_port": value}])

    def test_listener_rejects_invalid_ports_and_noncanonical_control(self):
        listener = copy.deepcopy(self.sample("listeners")[0])
        # 1414 is excluded by this project's listener-port policy.
        for value in (True, "1417", 1023, 1414, 65536):
            with self.subTest(port=value):
                self.assert_fails("listeners", [{**listener, "port": value}])
        self.assert_fails("listeners", [{**listener, "control": "qmgr"}])

    def test_hyphenated_channel_names_remain_accepted(self):
        sender = copy.deepcopy(next(item for item in self.sample("channels") if item["type"] == "SDR"))
        sender["name"] = "QM1-TO-QM2"
        self.assert_passes("channels", [sender])

    def test_queue_manager_names_up_to_48_characters(self):
        for kind in VALIDATORS:
            with self.subTest(kind=kind):
                record = self.sample(kind)[0]
                record["host_qmgr"] = "Q" * 48
                self.assert_passes(kind, [record])
                record["host_qmgr"] += "Q"
                self.assert_fails(kind, [record])


if __name__ == "__main__":
    unittest.main()
