import unittest

import hosting


class HostingConfigTests(unittest.TestCase):
    def test_default_is_local_mode(self):
        config = hosting.load({})
        self.assertFalse(config.hosted)
        self.assertIsNone(config.public_url)
        self.assertEqual(config.allowed_origins(), set())

    def test_hosted_needs_a_public_url(self):
        with self.assertRaises(hosting.ConfigError):
            hosting.load({"DETOUR_HOSTED": "true"})

    def test_public_url_without_hosted_is_refused(self):
        # A public hostname with no login would publish the portal to anyone.
        with self.assertRaises(hosting.ConfigError):
            hosting.load({"DETOUR_PUBLIC_URL": "https://detour.example.com"})

    def test_hosted_requires_https(self):
        with self.assertRaises(hosting.ConfigError):
            hosting.load({"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": "http://detour.example.com"})

    def test_http_is_allowed_only_for_localhost(self):
        config = hosting.load({"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": "http://localhost:8080"})
        self.assertFalse(config.secure_cookie)
        self.assertEqual(config.host, "localhost:8080")

    def test_bad_urls_are_refused(self):
        for url in ("detour.example.com", "https://", "https://u:p@detour.example.com", "https://detour.example.com/app",
                    "https://detour.example.com?x=1", "ftp://detour.example.com", "https://detour.example.com#x"):
            with self.subTest(url=url), self.assertRaises(hosting.ConfigError):
                hosting.load({"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": url})

    def test_hosted_config(self):
        config = hosting.load({"DETOUR_HOSTED": " TRUE ", "DETOUR_PUBLIC_URL": "https://Detour.Example.com/"})
        self.assertTrue(config.hosted)
        self.assertTrue(config.secure_cookie)
        self.assertEqual(config.public_url, "https://detour.example.com")
        self.assertEqual(config.host, "detour.example.com")
        self.assertEqual(config.allowed_origins(), {"https://detour.example.com"})
        self.assertEqual(config.mcp_url, "https://detour.example.com:3788/mcp")

    def test_custom_mcp_url(self):
        config = hosting.load({"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": "https://detour.example.com",
                               "DETOUR_MCP_PUBLIC_URL": "https://mcp.example.com/mcp"})
        self.assertEqual(config.mcp_url, "https://mcp.example.com/mcp")

    def test_hosted_refuses_to_run_mcp_without_keys(self):
        base = {"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": "https://detour.example.com"}
        for mode in ("none", "off", ""):
            with self.subTest(mode=mode):
                if mode == "":  # unset or blank means the default, which is keys
                    hosting.load({**base, "DETOUR_MCP_AUTH_MODE": ""})
                else:
                    with self.assertRaises(hosting.ConfigError):
                        hosting.load({**base, "DETOUR_MCP_AUTH_MODE": mode})
        hosting.load({**base, "DETOUR_MCP_AUTH_MODE": " KEY "})

    def test_mcp_url_must_be_https(self):
        with self.assertRaises(hosting.ConfigError):
            hosting.load({"DETOUR_HOSTED": "true", "DETOUR_PUBLIC_URL": "https://detour.example.com",
                          "DETOUR_MCP_PUBLIC_URL": "http://mcp.example.com/mcp"})


if __name__ == "__main__":
    unittest.main()
