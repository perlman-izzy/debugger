import unittest

from source_guard import validate_proposal


class FrozenSourceGuardTests(unittest.TestCase):
    def test_allows_current_epistemic_import_surface(self):
        src = "from dataclasses import dataclass\nfrom enum import Enum\nfrom typing import Iterable\n"
        ok, reason = validate_proposal("epistemic.py", src, "")
        self.assertTrue(ok, reason)

    def test_rejects_process_import(self):
        ok, _ = validate_proposal("epistemic.py", "import subprocess\n", "")
        self.assertFalse(ok)

    def test_rejects_dynamic_import(self):
        ok, _ = validate_proposal("epistemic.py", "x = __import__('os')\n", "")
        self.assertFalse(ok)

    def test_rejects_file_write_attribute(self):
        src = "from dataclasses import dataclass\nx.write_text('bad')\n"
        ok, _ = validate_proposal("epistemic.py", src, "")
        self.assertFalse(ok)

    def test_rejects_syntax_error(self):
        ok, _ = validate_proposal("epistemic.py", "def broken(:\n", "")
        self.assertFalse(ok)

    def test_existing_test_semantics_cannot_change(self):
        original = "import unittest\n\nclass A(unittest.TestCase):\n    def test_old(self):\n        self.assertTrue(True)\n"
        modified = original.replace("assertTrue(True)", "assertFalse(False)") + "\nclass B(unittest.TestCase):\n    def test_new(self):\n        self.assertTrue(True)\n"
        ok, _ = validate_proposal("test_epistemic.py", modified, original)
        self.assertFalse(ok)

    def test_test_change_requires_new_test(self):
        original = "import unittest\n"
        ok, _ = validate_proposal("test_epistemic.py", original + "\n# comment\n", original)
        self.assertFalse(ok)

    def test_new_test_is_allowed(self):
        original = "import unittest\n"
        replacement = original + "\nclass Added(unittest.TestCase):\n    def test_added(self):\n        self.assertTrue(True)\n"
        ok, reason = validate_proposal("test_epistemic.py", replacement, original)
        self.assertTrue(ok, reason)

    def test_reformatting_existing_test_plus_new_test_is_allowed(self):
        original = "import unittest\n\nclass A(unittest.TestCase):\n    def test_old(self):\n        self.assertEqual(1 + 1, 2)\n"
        replacement = "import unittest\n\n\nclass A( unittest.TestCase ):\n\n    def test_old( self ):\n        self.assertEqual(1+1,2)\n\n    def test_new(self):\n        self.assertTrue(True)\n"
        ok, reason = validate_proposal("test_epistemic.py", replacement, original)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
