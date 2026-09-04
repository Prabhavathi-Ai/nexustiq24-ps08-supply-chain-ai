"""Foundation checks that do not require the unsupported system interpreter."""

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class FoundationTests(unittest.TestCase):
    def test_readme_declares_track_id_first(self) -> None:
        first_line = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "TRACK_ID=PS08")

    def test_runtime_is_pinned_to_python_311(self) -> None:
        self.assertEqual((ROOT / ".python-version").read_text(encoding="utf-8").strip(), "3.11")
        self.assertIn('requires-python = "==3.11.*"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_required_boundaries_exist(self) -> None:
        required_paths = (
            "app.py",
            "src",
            "api",
            "models",
            "services",
            "detectors",
            "matching",
            "analysis",
            "gemini",
            "evidence",
            "storage",
            "config",
            "tests",
            "data",
            "frontend",
            "requirements.txt",
            ".gitignore",
        )
        for path in required_paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).exists())

    def test_startup_configuration_is_explicit(self) -> None:
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("port=8000", app_source)
        self.assertIn(".env", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()