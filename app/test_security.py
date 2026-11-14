import stat
import tempfile
import unittest
from pathlib import Path

import security


def settings(**env):
    return security.Settings.from_env(env)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_local_and_strict(self):
        s = settings()
        self.assertFalse(s.cloud)
        self.assertEqual(s.bind_host, "127.0.0.1")
        self.assertEqual(s.mcp_auth, "key")
        self.assertFalse(s.secure_cookies)
        self.assertEqual(s.mcp_url, "http://127.0.0.1:3788/mcp")
        self.assertIn("127.0.0.1:3787", s.portal_hosts)
        self.assertIn("localhost:3787", s.portal_hosts)
        self.assertNotIn("evil.example", s.portal_hosts)
        self.assertIn("http://127.0.0.1:3787", s.portal_origins)

    def test_public_url_turns_on_cloud_mode(self):
        s = settings(DETOUR_PUBLIC_URL="https://Detour.Example.com/")
        self.assertTrue(s.cloud)
        self.assertTrue(s.secure_cookies)
        self.assertEqual(s.public_url, "https://detour.example.com")
        self.assertIn("detour.example.com", s.portal_hosts)
        self.assertIn("https://detour.example.com", s.portal_origins)
        self.assertEqual(s.mcp_url, "https://detour.example.com/mcp")
        # Platform health checks and the container's own check still reach the loopback address.
        self.assertIn("127.0.0.1:3787", s.portal_hosts)

    def test_default_https_port_is_dropped_and_other_ports_are_kept(self):
        self.assertIn("detour.example.com", settings(DETOUR_PUBLIC_URL="https://detour.example.com:443").portal_hosts)
        s = settings(DETOUR_PUBLIC_URL="https://detour.example.com:8443")
        self.assertIn("detour.example.com:8443", s.portal_hosts)
        self.assertIn("https://detour.example.com:8443", s.portal_origins)

    def test_public_url_must_be_a_plain_https_origin(self):
        for bad in ("http://detour.example.com", "detour.example.com", "https://detour.example.com/app", "https://u:p@detour.example.com",
                    "https://detour.example.com?x=1", "https://detour.example.com#top", "ftp://detour.example.com", "https://"):
            with self.subTest(bad=bad), self.assertRaises(security.ConfigError):
                settings(DETOUR_PUBLIC_URL=bad)

    def test_mcp_auth_mode_fails_closed(self):
        self.assertEqual(settings(DETOUR_MCP_AUTH_MODE="NONE").mcp_auth, "none")
        self.assertEqual(settings(DETOUR_MCP_AUTH_MODE=" Key ").mcp_auth, "key")
        for typo in ("keys", "off", "false", "disabled", "0"):
            with self.subTest(typo=typo), self.assertRaises(security.ConfigError):
                settings(DETOUR_MCP_AUTH_MODE=typo)

    def test_cloud_mode_never_allows_unauthenticated_mcp(self):
        with self.assertRaisesRegex(security.ConfigError, "none"):
            settings(DETOUR_PUBLIC_URL="https://detour.example.com", DETOUR_MCP_AUTH_MODE="none")

    def test_session_hours_are_bounded(self):
        self.assertEqual(settings().session_hours, 24)
        self.assertEqual(settings(DETOUR_SESSION_HOURS="2").session_hours, 2)
        for bad in ("0", "-1", "169", "abc"):
            with self.subTest(bad=bad), self.assertRaises(security.ConfigError):
                settings(DETOUR_SESSION_HOURS=bad)

    def test_ports_must_be_valid(self):
        with self.assertRaises(security.ConfigError):
            settings(PORT="99999")


class PortalTokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_install_generates_a_private_token_once(self):
        token = security.load_portal_token(settings(), {}, self.dir)
        self.assertGreaterEqual(len(token), security.MIN_TOKEN_CHARS)
        path = self.dir / "portal-token"
        self.assertEqual(path.read_text().strip(), token)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(security.load_portal_token(settings(), {}, self.dir), token)

    def test_configured_token_wins_and_is_not_written_to_disk(self):
        configured = "x" * 30
        self.assertEqual(security.load_portal_token(settings(), {"DETOUR_PORTAL_TOKEN": f" {configured} "}, self.dir), configured)
        self.assertFalse((self.dir / "portal-token").exists())

    def test_short_tokens_are_refused(self):
        with self.assertRaisesRegex(security.ConfigError, "at least"):
            security.load_portal_token(settings(), {"DETOUR_PORTAL_TOKEN": "short"}, self.dir)

    def test_cloud_mode_requires_a_configured_token(self):
        cloud = settings(DETOUR_PUBLIC_URL="https://detour.example.com")
        with self.assertRaisesRegex(security.ConfigError, "DETOUR_PORTAL_TOKEN"):
            security.load_portal_token(cloud, {}, self.dir)
        self.assertFalse((self.dir / "portal-token").exists())

    def test_a_damaged_token_file_is_an_error_not_a_silent_reset(self):
        (self.dir / "portal-token").write_text("tiny")
        with self.assertRaises(security.ConfigError):
            security.load_portal_token(settings(), {}, self.dir)


class SignInTests(unittest.TestCase):
    TOKEN = "t" * 32

    def make(self, now=1_000_000.0, **env):
        self.now = now
        return security.SignIn(self.TOKEN, settings(**env), clock=lambda: self.now)

    def test_token_check(self):
        sign_in = self.make()
        self.assertTrue(sign_in.check_token(self.TOKEN))
        self.assertFalse(sign_in.check_token(self.TOKEN + "x"))
        self.assertFalse(sign_in.check_token(""))
        self.assertFalse(sign_in.check_token(None))

    def test_session_cookie_round_trip_and_expiry(self):
        sign_in = self.make(DETOUR_SESSION_HOURS="1")
        cookie = sign_in.issue()
        self.assertTrue(sign_in.valid(cookie))
        self.assertNotEqual(cookie, sign_in.issue())
        self.now += 3599
        self.assertTrue(sign_in.valid(cookie))
        self.now += 2
        self.assertFalse(sign_in.valid(cookie))

    def test_forged_or_mangled_cookies_are_rejected(self):
        sign_in = self.make()
        cookie = sign_in.issue()
        version, expiry, nonce, signature = cookie.split(".")
        for bad in ("", "garbage", cookie + "x", f"{version}.{int(expiry) + 99999}.{nonce}.{signature}", f"{version}.abc.{nonce}.{signature}",
                    ".".join([version, expiry, nonce]), "v0." + cookie.split(".", 1)[1]):
            with self.subTest(bad=bad):
                self.assertFalse(sign_in.valid(bad))
        self.assertFalse(sign_in.valid(None))

    def test_changing_the_token_signs_everyone_out(self):
        cookie = self.make().issue()
        other = security.SignIn("u" * 32, settings(), clock=lambda: self.now)
        self.assertFalse(other.valid(cookie))

    def test_cookie_headers(self):
        local = self.make()
        header = local.set_cookie(local.issue())
        self.assertTrue(header.startswith("detour_session="))
        for part in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=86400"):
            self.assertIn(part, header)
        self.assertNotIn("Secure", header)
        cloud = self.make(DETOUR_PUBLIC_URL="https://detour.example.com")
        header = cloud.set_cookie(cloud.issue())
        self.assertTrue(header.startswith("__Host-detour_session="))
        self.assertIn("Secure", header)
        self.assertIn("Max-Age=0", cloud.clear_cookie())

    def test_reading_the_cookie_from_a_request(self):
        sign_in = self.make()
        cookie = sign_in.issue()
        self.assertEqual(sign_in.read_cookie(f"theme=dark; detour_session={cookie}; other=1"), cookie)
        self.assertIsNone(sign_in.read_cookie("theme=dark"))
        self.assertIsNone(sign_in.read_cookie(None))
        self.assertIsNone(sign_in.read_cookie("detour_session"))

    def test_repeated_failures_lock_sign_in_for_a_minute(self):
        sign_in = self.make()
        for _ in range(security.MAX_FAILURES):
            self.assertEqual(sign_in.retry_after(), 0)
            sign_in.record_failure()
        self.assertGreater(sign_in.retry_after(), 0)
        self.assertLessEqual(sign_in.retry_after(), 60)
        self.now += 61
        self.assertEqual(sign_in.retry_after(), 0)

    def test_signing_out_ends_that_session_only(self):
        sign_in = self.make()
        first, second = sign_in.issue(), sign_in.issue()
        sign_in.revoke(first)
        self.assertFalse(sign_in.valid(first))
        self.assertTrue(sign_in.valid(second))
        sign_in.revoke("garbage")  # ignored, never raises
        self.now += 10 * 86400
        sign_in.revoke(second)  # expired entries are dropped, so the list can't grow without limit
        self.assertEqual(sign_in.revoked_count(), 0)

    def test_success_does_not_unlock_a_throttled_guesser(self):
        sign_in = self.make()
        for _ in range(security.MAX_FAILURES):
            sign_in.record_failure()
        self.assertGreater(sign_in.retry_after(), 0)


if __name__ == "__main__":
    unittest.main()
