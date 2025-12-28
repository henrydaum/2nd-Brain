import unittest
import tempfile
import json
import shutil
import os
from pathlib import Path
from config import Config

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_load_default(self):
        config = Config(self.data_dir)
        self.assertEqual(config.get("batch_size"), 16)
        # Check if file was created
        self.assertTrue((self.data_dir / "config.json").exists())

    def test_load_existing(self):
        config_path = self.data_dir / "config.json"
        custom_config = {"batch_size": 32}
        with open(config_path, "w") as f:
            json.dump(custom_config, f)

        config = Config(self.data_dir)
        self.assertEqual(config.get("batch_size"), 32)

    def test_update_config(self):
        config = Config(self.data_dir)
        config["batch_size"] = 64
        self.assertEqual(config["batch_size"], 64)

        # Verify it persisted
        with open(self.data_dir / "config.json", "r") as f:
            data = json.load(f)
            self.assertEqual(data["batch_size"], 64)

if __name__ == '__main__':
    unittest.main()
