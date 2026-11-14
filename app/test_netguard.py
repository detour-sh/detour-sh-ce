import asyncio
import unittest

import netguard


class ClientAllowedTests(unittest.TestCase):
    def test_local_and_private_clients_are_allowed(self):
        for ip in ("127.0.0.1", "::1", "10.1.2.3", "172.17.0.1", "192.168.65.1", "169.254.1.1", "fd00::1", "100.64.0.5"):
            with self.subTest(ip=ip):
                self.assertTrue(netguard.client_allowed(ip, allow_public=False))

    def test_public_clients_are_refused(self):
        for ip in ("8.8.8.8", "93.184.216.34", "2606:4700::1111", "::ffff:8.8.8.8"):
            with self.subTest(ip=ip):
                self.assertFalse(netguard.client_allowed(ip, allow_public=False))

    def test_mapped_private_address_is_allowed(self):
        self.assertTrue(netguard.client_allowed("::ffff:172.17.0.1", allow_public=False))

    def test_unparseable_or_missing_address_fails_closed(self):
        for ip in (None, "", "not-an-ip", "unix"):
            with self.subTest(ip=ip):
                self.assertFalse(netguard.client_allowed(ip, allow_public=False))

    def test_opt_in_allows_public_clients(self):
        self.assertTrue(netguard.client_allowed("8.8.8.8", allow_public=True))

    def test_setting_defaults_to_refusing_public_clients(self):
        self.assertFalse(netguard.public_clients_allowed({}))
        self.assertFalse(netguard.public_clients_allowed({"DETOUR_ALLOW_PUBLIC_CLIENTS": "false"}))
        self.assertFalse(netguard.public_clients_allowed({"DETOUR_ALLOW_PUBLIC_CLIENTS": "yes please"}))
        self.assertTrue(netguard.public_clients_allowed({"DETOUR_ALLOW_PUBLIC_CLIENTS": " TRUE "}))


class AsgiGuardTests(unittest.TestCase):
    def run_guard(self, client, allow_public=False, scope_type="http"):
        sent, reached = [], []

        async def app(scope, receive, send):
            reached.append(True)

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request"}

        guard = netguard.ClientGuard(app, allow_public=allow_public)
        asyncio.run(guard({"type": scope_type, "client": client}, receive, send))
        return sent, reached

    def test_private_client_reaches_the_app(self):
        sent, reached = self.run_guard(("172.17.0.1", 5000))
        self.assertTrue(reached)
        self.assertEqual(sent, [])

    def test_public_client_gets_403_and_never_reaches_the_app(self):
        sent, reached = self.run_guard(("93.184.216.34", 5000))
        self.assertFalse(reached)
        self.assertEqual(sent[0]["status"], 403)

    def test_missing_client_fails_closed(self):
        _, reached = self.run_guard(None)
        self.assertFalse(reached)

    def test_lifespan_passes_through(self):
        _, reached = self.run_guard(None, scope_type="lifespan")
        self.assertTrue(reached)


if __name__ == "__main__":
    unittest.main()
