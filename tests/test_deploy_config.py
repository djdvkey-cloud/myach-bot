"""Единственный production-конфиг Amvera: amvera.yaml (Python 3.11 + singleton). Второй файл amvera.yml (Python 3.12, без singleton)
однажды случайно попал в production через автослияние GitHub и сменил версию Python — такое должно ловиться тестом."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DeployConfig(unittest.TestCase):
    def test_single_amvera_config_python_311_singleton(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "amvera.yml")), "amvera.yml не должен существовать: конфиг один — amvera.yaml")
        path = os.path.join(ROOT, "amvera.yaml")
        if not os.path.exists(path):
            self.skipTest("amvera.yaml нет в этой копии")
        text = open(path, encoding="utf-8").read().replace("\r", "")
        self.assertIn('version: "3.11"', text)
        self.assertIn("singleton: true", text)
        self.assertIn("scriptName: bot.py", text)
        self.assertIn("persistenceMount: /data", text)


if __name__ == "__main__":
    unittest.main()
