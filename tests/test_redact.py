"""house_backend.mcp.redact with a made-up app's names and pattern."""

import re

import pytest

from house_backend.mcp.redact import REDACTED, Redactor, contains_redaction_marker

PATTERN = re.compile(
    r"""(?P<label>(?<![A-Za-z0-9_-])(?:api[-_]secret|signing[-_]key)(?![A-Za-z0-9_-]))
        (?P<sep>[ \t]*[:=][ \t]*)
        (?P<value>"[^"\n]*"|'[^'\n]*'|[^\s"']+)
      | (?P<blabel>Bearer)(?P<bsep>[ \t]+)(?P<bvalue>[A-Za-z0-9._~+/=-]{8,})""",
    re.VERBOSE,
)
redactor = Redactor({"api_secret", "signing_key"}, PATTERN, placeholder_prefixes=("secret-ref-",))


def test_text_forms_are_redacted_and_the_rest_kept():
    text = (
        'api_secret: "s3cret-value"\n'
        "api_secret: ''\n"
        "api_secret_ref: secret-ref-main\n"
        "signing-key=abc123 port=8080\n"
        "signing_key: secret-ref-other\n"
        "Authorization: Bearer abcdefgh.ijk and Bearer short\n"
    )
    out = redactor.redact_text(text)
    assert "s3cret-value" not in out and "abc123" not in out and "abcdefgh.ijk" not in out
    assert f'api_secret: "{REDACTED}"' in out
    assert "api_secret: ''" in out  # "none set" is information
    assert "api_secret_ref: secret-ref-main" in out  # another field
    assert f"signing-key={REDACTED} port=8080" in out
    assert "signing_key: secret-ref-other" in out  # a placeholder names its secret
    assert f"Bearer {REDACTED} and Bearer short" in out
    assert out.count(REDACTED) == 3


def test_json_fields_are_redacted_recursively():
    data = {"api_secret": "x", "rows": [{"signing_key": "k", "name": "n", "note": "api_secret=y"}], "n": 3}
    assert redactor.redact(data) == {
        "api_secret": REDACTED,
        "rows": [{"signing_key": REDACTED, "name": "n", "note": f"api_secret={REDACTED}"}],
        "n": 3,
    }
    assert redactor.redact({"api_secret": None, "signing_key": ""}) == {"api_secret": None, "signing_key": ""}


def test_names_only():
    assert Redactor({"token"}).redact({"token": "t", "text": "token=t"}) == {"token": REDACTED, "text": "token=t"}


def test_the_marker_echoed_back_is_detected():
    assert contains_redaction_marker({"a": [{"b": f"x{REDACTED}"}]})
    assert not contains_redaction_marker({"a": ["plain"], "n": 1})
    assert redactor.contains_marker([REDACTED])


def test_a_pattern_without_the_groups_is_refused():
    with pytest.raises(ValueError):
        Redactor(set(), re.compile(r"(?P<value>x)"))
    with pytest.raises(ValueError):
        Redactor(set(), re.compile(r"(?P<label>x)(?P<value>y)"))
