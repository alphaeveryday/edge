"""Protect other applications' regression gates when reducing CI scope."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "selection", Path(__file__).parents[1] / "scripts/select_python_jobs.py"
)
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)


class SelectionTests(unittest.TestCase):
    def select(self, *paths, available=("analysis-engine-v2", "edge-analysis-tools")):
        return selection.select_jobs(list(paths), set(available))

    def test_v2_keeps_source_lineage_e2e_without_legacy_units_or_images(self):
        result = self.select("src/apps/cloud/analysis-engine-v2/src/edge_analysis_v2/sources/database.py")
        self.assertEqual(result["legacy"], [])
        self.assertEqual(result["images"], [])
        self.assertTrue(result["e2e"])
        self.assertEqual([p["package"] for p in result["optional"]], ["analysis-engine-v2"])

    def test_calculation_library_also_checks_its_agent_consumer(self):
        result = self.select("src/libs/analysis-tools/tool.py")
        self.assertEqual(len(result["optional"]), 2)
        self.assertEqual(result["legacy"], [])

    def test_legacy_app_change_keeps_its_tests_build_and_integration(self):
        for app in ("data-pipeline", "db-query"):
            with self.subTest(app=app):
                result = self.select(f"src/apps/cloud/{app}/source.py")
                self.assertEqual(result["legacy"], [app])
                self.assertEqual(result["images"], [app])
                self.assertTrue(result["e2e"])

    def test_schema_change_keeps_real_database_e2e_not_unchanged_images(self):
        result = self.select("src/libs/schema/migrations-cloud/V202609.sql")
        self.assertEqual(result["legacy"], [])
        self.assertEqual(result["images"], [])
        self.assertTrue(result["e2e"])
        self.assertEqual(len(result["optional"]), 2)

    def test_shared_or_unknown_python_changes_keep_all_regressions(self):
        for path in ("src/pyproject.toml", "src/uv.lock", "src/.dockerignore",
                     "src/libs/ontology/x.py", "src/libs/new-library/x.py",
                     ".github/workflows/test-python.yml", ".github/scripts/select_python_jobs.py"):
            with self.subTest(path=path):
                result = self.select(path)
                self.assertEqual(result["legacy"], ["data-pipeline", "db-query"])
                self.assertEqual(result["images"], result["legacy"])
                self.assertTrue(result["e2e"])

    def test_mixed_changes_preserve_query_package_checks(self):
        result = self.select("src/apps/cloud/analysis-engine-v2/agent.py",
                             "src/apps/cloud/db-query/query.py")
        self.assertEqual(result["legacy"], ["db-query"])
        self.assertTrue(result["e2e"])

    def test_docs_only_has_no_heavy_jobs(self):
        result = self.select("docs/plan.md", "tasks/pr.md")
        self.assertFalse(any(result[key] for key in ("legacy", "images", "e2e", "optional")))

    def test_shared_change_before_v2_registration_does_not_call_missing_package(self):
        result = self.select("src/uv.lock", available=())
        self.assertEqual(result["optional"], [])
        self.assertEqual(len(result["legacy"]), 2)

    def test_calculation_library_can_be_introduced_before_agent(self):
        result = self.select("src/libs/analysis-tools/tool.py", available=("edge-analysis-tools",))
        self.assertEqual([p["package"] for p in result["optional"]], ["edge-analysis-tools"])

    def test_v2_change_without_registration_fails_instead_of_skipping(self):
        with self.assertRaisesRegex(ValueError, "analysis-engine-v2"):
            self.select("src/apps/cloud/analysis-engine-v2/pyproject.toml", available=())

    def test_optional_package_must_be_registered_and_present(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "src"
            source.mkdir()
            (source / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers=[]\n')
            self.assertEqual(selection.available_optional(root), set())
            package = source / "apps/cloud/analysis-engine-v2"
            package.mkdir(parents=True)
            (package / "pyproject.toml").write_text('[project]\nname="analysis-engine-v2"\n')
            with self.assertRaisesRegex(ValueError, "workspace"):
                selection.available_optional(root)
            (source / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers=["apps/cloud/*"]\n')
            self.assertEqual(selection.available_optional(root), {"analysis-engine-v2"})

    def test_expected_skips_pass_but_selected_skips_fail(self):
        import json
        plan = self.select("docs/a.md")
        results = {"changes": {"result": "success", "outputs": {
            key: json.dumps(value) for key, value in plan.items()
        }}}
        for job in ("pytest", "image-smoke", "e2e", "optional-tests"):
            results[job] = {"result": "skipped"}
        selection.check_results(results)
        results["changes"]["outputs"]["e2e"] = "true"
        with self.assertRaisesRegex(ValueError, "e2e"):
            selection.check_results(results)
        results["e2e"]["result"] = "cancelled"
        with self.assertRaisesRegex(ValueError, "e2e"):
            selection.check_results(results)

    def test_detection_failure_never_passes_final_check(self):
        with self.assertRaises(ValueError):
            selection.check_results({"changes": {"result": "failure"}})

    def test_airflow_changes_preserve_pipeline_contract_checks(self):
        for path in ("src/apps/cloud/airflow/dags/investor.py",
                     "src/apps/cloud/airflow/tests/ecs_stop_cases.json"):
            with self.subTest(path=path):
                result = self.select(path)
                self.assertEqual(result["legacy"], ["data-pipeline"])
                self.assertTrue(result["e2e"])

    def test_schema_reading_tests_remain_in_real_database_job(self):
        workflow = (Path(__file__).parents[1] / "workflows/test-python.yml").read_text(encoding="utf-8")
        e2e = workflow.split("\n  e2e:\n", 1)[1].split("\n  python-result:\n", 1)[0]
        self.assertIn("apps/cloud/db-query/tests/e2e", e2e)
        self.assertIn("tests/minute/test_schema_vocab.py", e2e)

    def test_git_rename_preserves_deleted_source_scope(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=folder)
            git("init", "-q")
            file = Path(folder) / "src/apps/cloud/db-query/source.py"
            file.parent.mkdir(parents=True)
            file.write_text("original content")
            git("add", ".")
            git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "base")
            git("mv", "src/apps/cloud/db-query/source.py", "moved.py")
            paths = git("diff", "--cached", "--no-renames", "--name-only", "-z").decode().split("\0")
            result = selection.select_jobs(paths, set())
            self.assertIn("db-query", result["legacy"])
