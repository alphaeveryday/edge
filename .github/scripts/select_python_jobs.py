"""Select Python regression jobs and reject missing required CI results."""
import fnmatch
import json
import os
from pathlib import Path
import sys
import tomllib

APPS = ("analysis-engine", "data-pipeline", "db-query")
OPTIONAL = {
    "analysis-engine-v2": "apps/cloud/analysis-engine-v2",
    "edge-analysis-tools": "libs/analysis-tools",
}


def available_optional(root: Path) -> set[str]:
    """Find optional packages that can be installed through the uv workspace.

    Args:
        root: Checked-out repository root.

    Returns:
        Registered package names; absent future packages are not enabled.

    Raises:
        ValueError: A present package is incorrectly named or unregistered.
    """
    workspace = tomllib.loads((root / "src/pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]["workspace"]
    found = set()
    for name, path in OPTIONAL.items():
        manifest = root / "src" / path / "pyproject.toml"
        if not manifest.exists():
            continue
        registered = any(fnmatch.fnmatchcase(path, pattern) for pattern in workspace["members"])
        excluded = any(fnmatch.fnmatchcase(path, pattern) for pattern in workspace.get("exclude", []))
        actual = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["name"]
        if actual != name or not registered or excluded:
            raise ValueError(f"{name}: invalid package name or uv workspace registration")
        found.add(name)
    return found


def select_jobs(paths: list[str], available: set[str]) -> dict:
    """Choose jobs from old and new paths, including deleted rename sources.

    Args:
        paths: Repository-relative paths from git diff --no-renames.
        available: Optional packages installed by this checkout's workspace.

    Returns:
        Legacy test/image matrices, optional test matrix and E2E switch.

    Raises:
        ValueError: Changed optional package cannot be tested in this checkout.
    """
    apps, requested = set(), set()
    shared = schema = False
    for path in filter(None, paths):
        if path.startswith("src/libs/schema/migrations-cloud/"):
            schema = True
        elif path.startswith("src/libs/analysis-tools/"):
            requested.add("edge-analysis-tools")
            if "analysis-engine-v2" in available:
                requested.add("analysis-engine-v2")
        elif path.startswith("src/apps/cloud/analysis-engine-v2/"):
            requested.add("analysis-engine-v2")
        elif any(path.startswith(f"src/apps/cloud/{app}/") for app in APPS):
            apps.update(app for app in APPS if path.startswith(f"src/apps/cloud/{app}/"))
        elif path.startswith("src/apps/cloud/airflow/dags/") or path == "src/apps/cloud/airflow/tests/ecs_stop_cases.json":
            apps.add("data-pipeline")  # DAG constants and shared ECS termination cases.
        elif path in {"src/pyproject.toml", "src/uv.lock", "src/.dockerignore", ".github/workflows/test-python.yml"} or path.startswith((".github/scripts/", ".github/tests/")):
            shared = True
        elif path.endswith((".py", ".toml", ".lock")) or path.startswith("src/libs/ontology/"):
            shared = True  # Unknown Python source/config: preserve regressions.
    missing = requested - available
    if missing:
        raise ValueError("Changed packages missing from workspace: " + ", ".join(sorted(missing)))
    if shared:
        apps.update(APPS)
    selected = [app for app in APPS if app in apps]
    optional = available if shared or schema else requested
    return {
        "legacy": selected,
        "images": selected,
        "optional": [{"package": name, "path": path} for name, path in OPTIONAL.items() if name in optional],
        "e2e": bool(selected) or schema or "analysis-engine-v2" in requested,
    }


def check_results(results: dict) -> None:
    """Reject failed detection, cancelled work or a skipped selected job.

    Args:
        results: GitHub Actions needs context for the final aggregate job.

    Raises:
        ValueError: Required work did not succeed or an unexpected job failed.
    """
    if results["changes"]["result"] != "success":
        raise ValueError("Python job selection failed or was cancelled")
    plan = results["changes"]["outputs"]
    for job, output in {"pytest": "legacy", "image-smoke": "images", "optional-tests": "optional", "e2e": "e2e"}.items():
        required = bool(json.loads(plan[output]))
        allowed = ("success",) if required else ("success", "skipped")
        if results[job]["result"] not in allowed:
            raise ValueError(f"{job}: expected {'success' if required else 'success or skipped'}, got {results[job]['result']}")


if __name__ == "__main__":
    if sys.argv[1] == "--check-results":
        check_results(json.loads(os.environ["RESULTS"]))
        print("All selected Python checks passed; unselected jobs were allowed to skip.")
    else:
        root = Path(__file__).resolve().parents[2]
        paths = Path(sys.argv[1]).read_bytes().decode("utf-8").split("\0")
        plan = select_jobs(paths, available_optional(root))
        for key, value in plan.items():
            print(f"{key}={json.dumps(value, separators=(',', ':'))}")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("## Python test scope\n\nSchema-only changes keep real DB E2E; v1 changes keep v1 tests/builds; shared changes keep all registered packages.\n\n```json\n" + json.dumps(plan, indent=2) + "\n```\n")
