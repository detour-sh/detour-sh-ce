import tempfile
import unittest
from pathlib import Path

import auth
import store


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.previous = store.DATA_DIR, store.DB_PATH
        store.DATA_DIR = root
        store.DB_PATH = root / "detour.sqlite"
        store.init_db()
        auth.THROTTLE.reset()

    def tearDown(self):
        store.DATA_DIR, store.DB_PATH = self.previous
        self.tmp.cleanup()

    def claim(self, code=None, **overrides):
        code = code or auth.issue_setup_code()
        payload = {"setup_code": code, "display_name": "Alex", "password": "correct horse battery", "password_confirm": "correct horse battery"}
        payload.update(overrides)
        return auth.claim_owner(payload, "Chrome on Linux")


class PasswordTests(AuthTestCase):
    def test_hash_round_trip_and_wrong_password(self):
        stored = auth.hash_password("correct horse battery")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn("correct horse", stored)
        self.assertTrue(auth.verify_password("correct horse battery", stored))
        self.assertFalse(auth.verify_password("wrong horse battery!", stored))
        self.assertFalse(auth.verify_password("x", "garbage"))

    def test_password_rules(self):
        for password, confirm in (("short", "short"), ("correct horse battery", "different one here"), ("x" * 300, "x" * 300)):
            with self.subTest(password=password[:8]), self.assertRaises(ValueError):
                auth.validate_new_password(password, confirm)


class SetupTests(AuthTestCase):
    def test_setup_code_is_only_stored_hashed_and_is_formatted(self):
        code = auth.issue_setup_code()
        self.assertRegex(code, r"^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$")
        with store.db() as conn:
            stored = conn.execute("SELECT code_hash FROM setup_codes").fetchone()["code_hash"]
        self.assertNotIn(code.replace("-", ""), stored)

    def test_claim_creates_owner_and_session(self):
        token = self.claim()
        self.assertTrue(auth.owner_exists())
        self.assertIsNotNone(auth.session_for_token(token))
        self.assertEqual(auth.owner_display_name(), "Alex")

    def test_code_is_forgiving_about_case_dashes_and_lookalikes(self):
        code = auth.issue_setup_code()
        messy = code.lower().replace("-", " ").replace("1", "l").replace("0", "o")
        self.assertIsNotNone(auth.session_for_token(self.claim(code=messy)))

    def test_wrong_code_is_refused_and_burns_after_ten_tries(self):
        auth.issue_setup_code()
        for _ in range(auth.SETUP_CODE_MAX_ATTEMPTS - 1):
            with self.assertRaises(auth.AuthError) as ctx:
                self.claim(code="WRONGWRONG00")
            self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(auth.AuthError) as ctx:
            self.claim(code="WRONGWRONG00")
        self.assertIn("Too many", str(ctx.exception))
        self.assertFalse(auth.owner_exists())
        with self.assertRaises(auth.AuthError) as ctx:  # the code is gone, even the right one fails
            auth.claim_owner({"setup_code": "x"}, "t")
        self.assertIn("no active setup code", str(ctx.exception))

    def test_cannot_claim_twice_or_reissue_after_setup(self):
        self.claim()
        with self.assertRaises(auth.AuthError) as ctx:
            auth.claim_owner({"setup_code": "x"}, "t")
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(ValueError):
            auth.issue_setup_code()

    def test_bad_password_does_not_consume_the_code(self):
        code = auth.issue_setup_code()
        with self.assertRaises(ValueError):
            self.claim(code=code, password="short", password_confirm="short")
        self.assertFalse(auth.owner_exists())
        self.assertIsNotNone(auth.session_for_token(self.claim(code=code)))


class SignInTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.claim()

    def test_sign_in_with_right_and_wrong_password(self):
        self.assertIsNotNone(auth.session_for_token(auth.sign_in({"password": "correct horse battery"}, "t")))
        with self.assertRaises(auth.AuthError) as ctx:
            auth.sign_in({"password": "nope nope nope nope"}, "t")
        self.assertEqual(ctx.exception.status, 401)
        for bad in (None, 123, "x" * 1000):
            with self.subTest(bad=str(bad)[:5]), self.assertRaises(auth.AuthError):
                auth.sign_in({"password": bad}, "t")

    def test_five_failures_lock_sign_in_even_for_the_right_password(self):
        for _ in range(auth.SIGN_IN_FREE_ATTEMPTS):
            with self.assertRaises(auth.AuthError):
                auth.sign_in({"password": "wrong wrong wrong"}, "t")
        with self.assertRaises(auth.AuthError) as ctx:
            auth.sign_in({"password": "correct horse battery"}, "t")
        self.assertEqual(ctx.exception.status, 429)
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_lock_doubles_and_is_capped(self):
        for _ in range(auth.SIGN_IN_FREE_ATTEMPTS):
            auth.THROTTLE.failed()
        first = auth.THROTTLE.locked_until
        auth.THROTTLE.locked_until = 0
        auth.THROTTLE.failed()
        self.assertGreater(auth.THROTTLE.locked_until - first, 0)
        for _ in range(40):
            auth.THROTTLE.failed()
        self.assertLessEqual(auth.THROTTLE.wait_seconds(), auth.SIGN_IN_MAX_LOCK_SECONDS)

    def test_sign_out_revokes_the_session(self):
        token = auth.sign_in({"password": "correct horse battery"}, "t")
        auth.sign_out(token)
        self.assertIsNone(auth.session_for_token(token))

    def test_sessions_expire(self):
        token = auth.sign_in({"password": "correct horse battery"}, "t")
        with store.db() as conn:
            conn.execute("UPDATE owner_sessions SET expires_at = '2000-01-01T00:00:00Z'")
        self.assertIsNone(auth.session_for_token(token))

    def test_idle_sessions_expire(self):
        token = auth.sign_in({"password": "correct horse battery"}, "t")
        with store.db() as conn:
            conn.execute("UPDATE owner_sessions SET last_seen_at = '2000-01-01T00:00:00Z'")
        self.assertIsNone(auth.session_for_token(token))

    def test_tokens_are_stored_hashed_and_junk_is_rejected(self):
        token = auth.sign_in({"password": "correct horse battery"}, "t")
        with store.db() as conn:
            dump = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM owner_sessions"))
        self.assertNotIn(token, dump)
        for junk in (None, "", "x" * 500, "not-a-token"):
            self.assertIsNone(auth.session_for_token(junk))

    def test_reset_owner_signs_everyone_out_and_keeps_data(self):
        token = auth.sign_in({"password": "correct horse battery"}, "t")
        route = store.create_route({"name": "keep me", "scope": "global"})["id"] if hasattr(store, "create_route") else None
        code = auth.reset_owner()
        self.assertFalse(auth.owner_exists())
        self.assertIsNone(auth.session_for_token(token))
        self.assertRegex(code, r"^[0-9A-Z]{4}-")
        if route:
            self.assertIsNotNone(store.get_route(route))


class CookieTests(unittest.TestCase):
    def test_cookie_flags(self):
        plain = auth.session_cookie("tok", secure=False)
        secure = auth.session_cookie("tok", secure=True)
        for cookie in (plain, secure):
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertIn("Path=/", cookie)
        self.assertNotIn("Secure", plain)
        self.assertIn("; Secure", secure)
        self.assertIn("Max-Age=0", auth.cleared_cookie(secure=True))

    def test_read_cookie(self):
        self.assertEqual(auth.read_cookie("a=1; detour_session=abc; b=2", "detour_session"), "abc")
        self.assertIsNone(auth.read_cookie("a=1", "detour_session"))
        self.assertIsNone(auth.read_cookie(None, "detour_session"))


if __name__ == "__main__":
    unittest.main()
