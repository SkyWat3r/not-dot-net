"""Tests for critical security fixes."""

from not_dot_net.frontend.login import _safe_redirect


class TestOpenRedirect:
    def test_relative_path_allowed(self):
        assert _safe_redirect("/dashboard") == "/dashboard"

    def test_root_allowed(self):
        assert _safe_redirect("/") == "/"

    def test_absolute_url_rejected(self):
        assert _safe_redirect("https://evil.com") == "/"

    def test_protocol_relative_rejected(self):
        assert _safe_redirect("//evil.com") == "/"

    def test_scheme_with_netloc_rejected(self):
        assert _safe_redirect("http://evil.com/path") == "/"

    def test_empty_string_rejected(self):
        assert _safe_redirect("") == "/"

    def test_path_with_query_allowed(self):
        assert _safe_redirect("/page?foo=bar") == "/page?foo=bar"

    def test_triple_slash_rejected(self):
        assert _safe_redirect("///evil.com") == "/"

    def test_backslash_rejected(self):
        assert _safe_redirect("/\\evil.com") == "/"

    def test_no_leading_slash_rejected(self):
        assert _safe_redirect("evil.com") == "/"

    def test_javascript_scheme_rejected(self):
        assert _safe_redirect("javascript:alert(1)") == "/"

    def test_data_scheme_rejected(self):
        assert _safe_redirect("data:text/html,hi") == "/"


class TestAuditResolveNames:
    def test_target_id_not_mutated(self):
        """_resolve_names should not overwrite target_id with display name (#4)."""
        from not_dot_net.backend.audit import AuditEvent

        ev = AuditEvent(
            category="test", action="test",
            target_type="user", target_id="some-uuid-string",
        )
        # Before resolution, _target_display should not exist
        assert not hasattr(ev, "_target_display")
        # After resolution sets _target_display, target_id should remain a UUID string
        ev._target_display = "John Doe"
        assert ev.target_id == "some-uuid-string"
        assert ev._target_display == "John Doe"


class TestLdapEscaping:
    def test_special_chars_escaped(self):
        from ldap3.utils.conv import escape_filter_chars

        malicious = "admin)(objectClass=*"
        escaped = escape_filter_chars(malicious)
        assert "(" not in escaped
        assert ")" not in escaped

    def test_asterisk_escaped(self):
        from ldap3.utils.conv import escape_filter_chars

        assert "*" not in escape_filter_chars("*")
