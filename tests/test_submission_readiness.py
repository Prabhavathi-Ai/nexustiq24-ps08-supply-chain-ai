"""Checks for the evaluator-facing startup and documentation contract."""

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class SubmissionReadinessTests(unittest.TestCase):
    def test_python_311_contract_is_declared(self) -> None:
        self.assertEqual((ROOT / ".python-version").read_text(encoding="utf-8").strip(), "3.11")
        self.assertIn('requires-python = "==3.11.*"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("Python 3.11", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_one_command_startup_and_port_are_documented(self) -> None:
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("uvicorn.run", app_source)
        self.assertIn("port=8000", app_source)
        self.assertIn("python app.py", readme)
        self.assertIn("http://localhost:8000", readme)

    def test_runtime_dependencies_and_gemini_variable_are_declared(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        settings = (ROOT / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("fastapi", requirements)
        self.assertIn("uvicorn", requirements)
        self.assertIn("google-genai", requirements)
        self.assertIn("GEMINI_API_KEY", settings)

    def test_frontend_is_packaged_and_secrets_are_ignored(self) -> None:
        self.assertTrue((ROOT / "frontend" / "index.html").exists())
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore)
        self.assertIn(".venv/", gitignore)


if __name__ == "__main__":
    unittest.main()