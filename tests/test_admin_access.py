from __future__ import annotations

import unittest

from backend.admin_access import is_admin_client, parse_admin_networks


class AdminAccessTests(unittest.TestCase):
    def test_default_only_allows_loopback(self):
        networks = parse_admin_networks("")
        self.assertEqual(len(networks), 2)
        self.assertTrue(is_admin_client("127.0.0.1", networks))
        self.assertTrue(is_admin_client("::1", networks))
        self.assertTrue(is_admin_client("localhost", networks))
        self.assertFalse(is_admin_client("10.10.10.104", networks))

    def test_accepts_comma_and_space_separated_networks(self):
        networks = parse_admin_networks("127.0.0.1/32, 10.10.10.0/24 ::1/128")
        self.assertTrue(is_admin_client("10.10.10.3", networks))
        self.assertTrue(is_admin_client("10.10.10.104", networks))
        self.assertFalse(is_admin_client("10.10.11.3", networks))

    def test_single_ip_and_ipv4_mapped_ipv6(self):
        networks = parse_admin_networks("10.10.10.3")
        self.assertTrue(is_admin_client("10.10.10.3", networks))
        self.assertTrue(is_admin_client("::ffff:10.10.10.3", networks))
        self.assertFalse(is_admin_client("::ffff:10.10.10.4", networks))

    def test_rejects_invalid_or_empty_entries(self):
        with self.assertRaises(ValueError):
            parse_admin_networks("10.10.10.0/33")
        with self.assertRaises(ValueError):
            parse_admin_networks(" , ")

    def test_rejects_non_ip_hosts(self):
        networks = parse_admin_networks("10.10.10.0/24")
        self.assertFalse(is_admin_client("", networks))
        self.assertFalse(is_admin_client("example.com", networks))
        self.assertFalse(is_admin_client(None, networks))


if __name__ == "__main__":
    unittest.main()
