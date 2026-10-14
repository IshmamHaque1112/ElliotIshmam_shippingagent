"""The agent, mock service and prompts must never see the hidden labels."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"eval.py"}


def python_sources():
    for path in ROOT.rglob("*.py"):
        parts = set(path.relative_to(ROOT).parts)
        if parts & {"tests", "scripts", ".git", "__pycache__"} or path.name in ALLOWED:
            continue
        yield path


def test_only_eval_reads_ground_truth_labels():
    offenders = [p.name for p in python_sources() if "ground_truth" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_app_modules_do_not_import_the_reference_script_or_eval():
    for path in python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n.split(".")[0] in ("build_dataset", "eval") for n in names), (path.name, names)


def test_dataset_files_have_no_label_fields():
    for name in ("shipments_dataset.json", "shipments_extended.json"):
        text = (ROOT / "data" / name).read_text(encoding="utf-8")
        assert "expected_state" not in text and "acceptable_root_causes" not in text


def test_no_module_names_either_labels_file():
    offenders = [p.name for p in python_sources() if "ground_truth_labels" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_repo_contains_no_holdout_material():
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json", ".md"} and ".git" not in path.parts and ".venv" not in path.parts:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if path.name != "test_ground_truth_isolation.py":
                assert "build_holdout" not in text and "KC-RAMP" not in text, path
