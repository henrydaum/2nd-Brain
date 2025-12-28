import unittest
import tempfile
import os
import shutil
from pathlib import Path
from Parsers import parse_code_or_text, parse_csv

class TestParsers(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.test_dir)

    def test_parse_code_or_text(self):
        file_path = Path(self.test_dir) / "test.txt"
        content = "Hello, World!"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        result = parse_code_or_text(file_path, 100)
        self.assertEqual(result, content)

    def test_parse_code_or_text_limit(self):
        file_path = Path(self.test_dir) / "test_limit.txt"
        content = "A" * 200
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        result = parse_code_or_text(file_path, 100)
        self.assertEqual(len(result), 100)
        self.assertEqual(result, "A" * 100)

    def test_parse_csv(self):
        file_path = Path(self.test_dir) / "test.csv"
        content = "Name,Age\nAlice,30\nBob,25"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        # The expected output depends on how parse_csv formats it.
        # Looking at the code: row_str = ", ".join([f"{h}: {v}" for h, v in zip(headers, row) if v.strip()])
        # Expect: "Name: Alice, Age: 30\nName: Bob, Age: 25"

        expected = "Name: Alice, Age: 30\nName: Bob, Age: 25"
        result = parse_csv(file_path, 1000)
        self.assertEqual(result, expected)

if __name__ == '__main__':
    unittest.main()
