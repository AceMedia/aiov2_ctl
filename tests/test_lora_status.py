import unittest

from ops_agent.lora import explain_tcp_failure


class TcpFailureTests(unittest.TestCase):
    def test_local_refused_points_at_meshtasticd(self):
        m = explain_tcp_failure(ConnectionRefusedError(111, "Connection refused"), "localhost", 4403)
        self.assertIn("meshtasticd is not running", m)
        self.assertIn("systemctl start meshtasticd", m)

    def test_remote_refused_names_the_address(self):
        m = explain_tcp_failure(ConnectionRefusedError(), "10.0.0.5", 4403)
        self.assertEqual(m, "Nothing is listening on 10.0.0.5:4403")

    def test_timeout(self):
        m = explain_tcp_failure(OSError("timed out"), "10.0.0.5", 4403)
        self.assertIn("Timed out", m)

    def test_other_errors_are_trimmed(self):
        self.assertEqual(len(explain_tcp_failure(ValueError("x" * 200), "localhost", 4403)), 50)


if __name__ == "__main__":
    unittest.main()
