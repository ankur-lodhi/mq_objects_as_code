import importlib.util
import pathlib
import unittest

PLUGIN = pathlib.Path(__file__).parents[1] / "playbooks" / "filter_plugins" / "mqsc.py"
spec = importlib.util.spec_from_file_location("mqsc_filter", PLUGIN)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MQSCQuoteTests(unittest.TestCase):
    def test_plain_and_wildcard_strings_are_preserved_inside_literal(self):
        self.assertEqual(module.mqsc_quote("TESTQ1"), "'TESTQ1'")
        self.assertEqual(module.mqsc_quote("*"), "'*'")
        self.assertEqual(module.mqsc_quote("CN=*"), "'CN=*'")
        self.assertEqual(module.mqsc_quote("*MQADMIN"), "'*MQADMIN'")
        self.assertEqual(module.mqsc_quote("QM1; touch /tmp/pwned"), "'QM1; touch /tmp/pwned'")

    def test_doubles_apostrophe_and_escapes_mqsc_backslash_quote(self):
        self.assertEqual(module.mqsc_quote("O'Brien"), "'O''Brien'")
        self.assertEqual(module.mqsc_quote('CN="Alice"'), "'CN=\\\"Alice\\\"'")
        self.assertEqual(module.mqsc_quote(r"CN=Alice\OU=Ops"), "'CN=Alice\\\\OU=Ops'")

    def test_dollar_is_preserved_for_stdin_based_invocation(self):
        self.assertEqual(module.mqsc_quote("$(id) $HOME"), "'$(id) $HOME'")

    def test_rejects_newlines_and_control_characters(self):
        for value in ("a\nb", "a\rb", "a\x00b", "a\x7fb", "a\x85b"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    module.mqsc_quote(value)

    def test_rejects_non_string_inputs(self):
        for value in (None, 7, ["*MQADMIN"]):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TypeError):
                    module.mqsc_quote(value)

    def test_enum_keyword_is_returned_unquoted_and_rejects_injected_types(self):
        choices = ("BLOCKUSER", "SSLPEERMAP")
        self.assertEqual(module.mqsc_enum("BLOCKUSER", choices), "BLOCKUSER")
        for value in ("blockuser", "BLOCKUSER) ACTION(REMOVE", "OTHER"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    module.mqsc_enum(value, choices)

    def test_sslpeermap_identity_checks_channel_peer_and_empty_issuer(self):
        output = """AMQ8878: Display channel authentication record details.
CHLAUTH(*) TYPE(SSLPEERMAP)
SSLPEER(CN=*) SSLCERTI( ) ADDRESS(*) USERSRC(NOACCESS)
CHLAUTH(*) TYPE(SSLPEERMAP)
SSLPEER(CN=*) SSLCERTI(CN=CA) ADDRESS(*) USERSRC(MAP)
CHLAUTH(SYSTEM.*) TYPE(SSLPEERMAP)
SSLPEER(CN=*) SSLCERTI( ) ADDRESS(*) USERSRC(NOACCESS)
"""
        self.assertTrue(module.mqsc_display_has_sslpeermap(output, "*", "CN=*"))
        self.assertFalse(module.mqsc_display_has_sslpeermap(output, "*", "CN=A"))
        self.assertTrue(module.mqsc_display_has_sslpeermap(output, "*", "CN=*", "CN=CA"))
        self.assertFalse(module.mqsc_display_has_sslpeermap(output, "SYSTEM.*", "CN=Other"))

    def test_chlauth_identity_count_normalizes_omitted_issuer(self):
        records = [
            {"type": "SSLPEERMAP", "channel": "*", "host_qmgr": "QM1", "sslpeer": "CN=*"},
            {"type": "BLOCKUSER", "channel": "*", "host_qmgr": "QM1", "userlist": "nobody"},
        ]
        self.assertEqual(module.mqsc_chlauth_identity_count(records, records[0]), 1)
        self.assertEqual(
            module.mqsc_chlauth_identity_count(records, {**records[0], "sslcerti": ""}), 1
        )
        self.assertEqual(
            module.mqsc_chlauth_identity_count(records, {**records[0], "sslcerti": "CN=CA"}), 0
        )
        self.assertEqual(module.mqsc_chlauth_identity_count(records, records[1]), 1)

    def test_different_issuer_does_not_suppress_blank_issuer_rule(self):
        output = "AMQ8878I: Display channel authentication record details.\nCHLAUTH(*) TYPE(SSLPEERMAP)\nSSLPEER(CN=*) SSLCERTI(CN=CA) ADDRESS(*)"
        self.assertFalse(module.mqsc_display_has_sslpeermap(output, "*", "CN=*"))

    def test_missing_identity_attribute_fails_closed(self):
        output = "AMQ8878I: Display channel authentication record details.\nCHLAUTH(*) TYPE(SSLPEERMAP)\nSSLPEER(CN=*)"
        with self.assertRaises(ValueError):
            module.mqsc_display_has_sslpeermap(output, "*", "CN=*")

    def test_address_is_part_of_sslpeermap_identity(self):
        output = "AMQ8878I: Details\nCHLAUTH(*) TYPE(SSLPEERMAP)\nSSLPEER(CN=*) SSLCERTI( ) ADDRESS(10.0.0.1)"
        self.assertFalse(module.mqsc_display_has_sslpeermap(output, "*", "CN=*"))
        self.assertTrue(module.mqsc_display_has_sslpeermap(output, "*", "CN=*", "", "10.0.0.1"))
        record = {"type": "SSLPEERMAP", "channel": "*", "host_qmgr": "QM1", "sslpeer": "CN=*"}
        self.assertEqual(module.mqsc_chlauth_identity_count([record], {**record, "address": "*"}), 1)
        self.assertEqual(module.mqsc_chlauth_identity_count([record], {**record, "address": "10.0.0.1"}), 0)

    def test_integer_tokens_reject_strings_and_booleans(self):
        self.assertEqual(module.mqsc_integer(0, 0, 10), 0)
        self.assertEqual(module.mqsc_integer(10, 0, 10), 10)
        for value in (True, "1", "1)\nDELETE QLOCAL(*)", -1, 11):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.mqsc_integer(value, 0, 10)

    def test_wrapped_identity_is_not_guessed(self):
        output = "AMQ8878I: Details\nCHLAUTH(*) TYPE(SSLPEERMAP)\nSSLPEER(CN=A,\nOU=B) SSLCERTI( ) ADDRESS(*)"
        with self.assertRaises(ValueError):
            module.mqsc_display_has_sslpeermap(output, "*", "CN=A,OU=B")

    def test_unterminated_attribute_fails_closed(self):
        output = "AMQ8878I: Display channel authentication record details.\nCHLAUTH(*) TYPE(SSLPEERMAP)\nSSLPEER(CN=* SSLCERTI( ) ADDRESS(*)"
        with self.assertRaises(ValueError):
            module.mqsc_display_has_sslpeermap(output, "*", "CN=*")


if __name__ == "__main__":
    unittest.main()

