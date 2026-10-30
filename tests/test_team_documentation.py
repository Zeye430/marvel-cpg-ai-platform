"""Shared onboarding stays small and usable without private local documents."""

import ast
import json
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_NAMES = {
    "README.md", "v3-gold-data-dictionary.md",
    *{f"v3-module-{number}-integration.md" for number in range(1, 5)},
}
PUBLIC_DOCS = [ROOT / "docs/data" / name for name in sorted(PUBLIC_NAMES)]
SHARED_DOCS = PUBLIC_DOCS + [
    ROOT / "README.md", ROOT / "data_platform/README.md",
    ROOT / "docs/project-bottlenecks.md",
    ROOT / "docs/solution-specifications/kpi-and-missing-store-day-handling.md",
]


class TeamDocumentationTests(unittest.TestCase):
    def test_only_six_shared_data_documents(self):
        self.assertEqual({p.name for p in (ROOT / "docs/data").iterdir()}, PUBLIC_NAMES)

    def test_shared_links_do_not_require_local_notes_or_artifacts(self):
        forbidden = ("docs/_local/", "docs/development/", "data/raw/", "outputs/", "work/")
        for doc in SHARED_DOCS:
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", doc.read_text()):
                if urlsplit(target).scheme or target.startswith("#"):
                    continue
                with self.subTest(document=doc.relative_to(ROOT), target=target):
                    relative = Path(unquote(target.split("#", 1)[0].strip("<>")))
                    self.assertFalse(relative.is_absolute(), "Use checkout-relative links")
                    resolved = (doc.parent / relative).resolve()
                    self.assertTrue(resolved.is_relative_to(ROOT))
                    name = resolved.relative_to(ROOT).as_posix()
                    self.assertFalse(name.startswith(forbidden), "Shared docs depend on local-only material")
                    self.assertTrue(resolved.exists(), "Missing shared dependency")

    def test_all_modules_have_clear_entry_routes(self):
        landing = (ROOT / "docs/data/README.md").read_text()
        package = (ROOT / "data_platform/README.md").read_text()
        for number in range(1, 5):
            filename = f"v3-module-{number}-integration.md"
            self.assertIn(f"]({filename})", landing)
            self.assertIn(f"](../docs/data/{filename})", package)
            self.assertIn("](README.md)", (ROOT / "docs/data" / filename).read_text())
        self.assertIn("](docs/data/README.md)", (ROOT / "README.md").read_text())

    def test_postgres_path_is_documented_without_claiming_cutover(self):
        landing = (ROOT / "docs/data/README.md").read_text()
        package = (ROOT / "data_platform/README.md").read_text()
        self.assertIn("](../../examples/read_postgres_data.py)", landing)
        self.assertIn("](contracts/v3/postgres-serving.json)", package)
        self.assertIn("already published to Aiven PostgreSQL", landing)
        self.assertIn("](../aiven-connection.md)", landing)
        self.assertIn("does not automatically migrate existing module code", landing)
        self.assertNotIn("Nothing has been published to Aiven yet", landing)
        for number in (1, 3):
            self.assertIn("not", (ROOT / "docs/data" / f"v3-module-{number}-integration.md").read_text().split("PostgreSQL", 1)[1][:400])
        self.assertIn("SHARED_DATA_BACKEND=postgres", (ROOT / "docs/data/v3-module-2-integration.md").read_text())
        self.assertIn("Never give these credentials", (ROOT / "docs/data/v3-module-4-integration.md").read_text())
        for doc in PUBLIC_DOCS + [ROOT / "data_platform/README.md", ROOT / "modules/module_2/README.md",
                                  ROOT / "examples/read_postgres_data.py", ROOT / "compose.postgres-test.yml"]:
            text = doc.read_text()
            with self.subTest(document=doc.relative_to(ROOT)):
                self.assertNotRegex(text, r"TASK-\d{4}|ACR-\d{4}|Claude|Codex|aivencloud\.com")

    def test_onboarding_python_and_json_examples_parse(self):
        for doc in PUBLIC_DOCS + [ROOT / "data_platform/README.md"]:
            for language, code in re.findall(r"```(python|json)\n(.*?)\n```", doc.read_text(), re.S):
                with self.subTest(document=doc.relative_to(ROOT), language=language):
                    if language == "python":
                        ast.parse(code)
                    else:
                        json.loads(code)

    def test_local_archive_and_journal_are_ignored_by_shared_rule(self):
        rules = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("/docs/_local/", rules)
        self.assertIn("/docs/development/data-platform-development-log.md", rules)
        # No private archive is needed to run this suite on a fresh checkout.


if __name__ == "__main__":
    unittest.main()
