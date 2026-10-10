"""MQSC-specific quoting and query-output helpers."""
from __future__ import annotations

import re
import unicodedata


def mqsc_quote(value: object) -> str:
    """Return a complete MQSC single-quoted string literal.

    Pass raw values only. Shell quoting is separate and must not be applied to
    this output. Reject controls so a value cannot add MQSC lines.
    """
    if not isinstance(value, str):
        raise TypeError("mqsc_quote expects a string")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("MQSC strings cannot contain control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "''")
    return "'" + escaped + "'"


def mqsc_enum(value: object, allowed: object) -> str:
    """Validate an MQSC keyword against a supplied finite allowlist."""
    if not isinstance(value, str):
        raise TypeError("mqsc_enum expects a string keyword")
    if not isinstance(allowed, (list, tuple, set, frozenset)):
        raise TypeError("mqsc_enum expects a sequence of allowed keywords")
    if value not in allowed:
        raise ValueError("unsupported MQSC keyword: {!r}".format(value))
    return value


def mqsc_integer(value: object, minimum: int, maximum: int) -> int:
    """Validate numeric MQSC tokens without accepting booleans or coercing text."""
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError("MQSC integer must be between {} and {}".format(minimum, maximum))
    return value


def _mqsc_attr_values(block: str, attribute: str) -> list[str]:
    """Extract balanced ATTRIBUTE(...) values; malformed syntax fails closed."""
    marker = re.compile(r"(?i)(?<![A-Z0-9_]){}\(".format(re.escape(attribute)))
    values: list[str] = []
    position = 0
    while match := marker.search(block, position):
        start = match.end()
        depth = 1
        quote = None
        escaped = False
        index = start
        while index < len(block):
            char = block[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == quote:
                    if quote == "'" and index + 1 < len(block) and block[index + 1] == "'":
                        index += 1
                    else:
                        quote = None
            elif char in ("'", '"'):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    values.append(block[start:index])
                    position = index + 1
                    break
            index += 1
        else:
            raise ValueError("malformed MQSC DISPLAY output: unterminated {} attribute".format(attribute))
    return values


def mqsc_display_has_sslpeermap(
    output: object, channel: object, sslpeer: object, sslcerti: object = "", address: object = "*"
) -> bool:
    """Find an exact SSLPEERMAP key (channel, peer, issuer, address) in output.

    Caller must run DISPLAY CHLAUTH(channel) TYPE(SSLPEERMAP) MATCH(EXACT) ALL
    and suppress runmqsc command echo with -e. This prevents a more-specific
    channel profile or a different SSLCERTI mapping from counting as a match.
    """
    if not all(isinstance(value, str) for value in (output, channel, sslpeer, sslcerti, address)):
        raise TypeError("mqsc_display_has_sslpeermap expects string arguments")
    if any(unicodedata.category(char) == "Cc" for value in (channel, sslpeer, sslcerti, address) for char in value):
        raise ValueError("MQSC strings cannot contain control characters")

    headers = list(re.finditer(r"(?im)^\s*CHLAUTH\(([^)]*)\)\s+TYPE\(([^)]*)\)", output))
    if not headers and re.search(r"(?m)^\s*AMQ8878I?:", output) is not None:
        raise ValueError("cannot determine SSLPEERMAP identity safely: DISPLAY success contained no record headers")
    for index, header in enumerate(headers):
        if header.group(1) != channel or header.group(2).upper() != "SSLPEERMAP":
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(output)
        block = output[header.end():end]
        peers = _mqsc_attr_values(block, "SSLPEER")
        issuers = _mqsc_attr_values(block, "SSLCERTI")
        addresses = _mqsc_attr_values(block, "ADDRESS")
        if len(peers) != 1 or len(issuers) != 1 or len(addresses) != 1:
            raise ValueError(
                "cannot determine SSLPEERMAP identity safely: expected one SSLPEER, SSLCERTI and ADDRESS per record"
            )
        if any("\n" in value or "\r" in value for value in peers + issuers + addresses):
            raise ValueError("wrapped SSLPEERMAP identity is unsupported; inspect the MQ record before continuing")
        if peers[0] == sslpeer and issuers[0].strip() == sslcerti and addresses[0] == address:
            return True
    return False


def mqsc_chlauth_identity_count(records: object, record: object) -> int:
    """Count declared CHLAUTH identities with optional SSLCERTI normalized to blank."""
    if not isinstance(records, (list, tuple)) or not isinstance(record, dict):
        raise TypeError("mqsc_chlauth_identity_count expects a record list and mapping")
    kind = record.get("type")
    identity_fields = ("type", "channel", "host_qmgr")
    if kind == "SSLPEERMAP":
        identity_fields += ("sslpeer",)
        target_issuer = record.get("sslcerti", "")
    elif kind == "BLOCKUSER":
        target_issuer = ""
    else:
        raise ValueError("unsupported CHLAUTH identity type")
    count = 0
    for candidate in records:
        if not isinstance(candidate, dict):
            raise TypeError("CHLAUTH records must be mappings")
        if all(candidate.get(field) == record.get(field) for field in identity_fields):
            if kind == "SSLPEERMAP" and candidate.get("sslcerti", "") != target_issuer:
                continue
            if kind == "SSLPEERMAP" and candidate.get("address", "*") != record.get("address", "*"):
                continue
            count += 1
    return count


class FilterModule:
    def filters(self):
        return {
            "mqsc_quote": mqsc_quote,
            "mqsc_enum": mqsc_enum,
            "mqsc_integer": mqsc_integer,
            "mqsc_display_has_sslpeermap": mqsc_display_has_sslpeermap,
            "mqsc_chlauth_identity_count": mqsc_chlauth_identity_count,
        }
