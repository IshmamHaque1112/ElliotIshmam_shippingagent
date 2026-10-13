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


def test_dataset_file_has_no_label_fields():
    text = (ROOT / "data" / "shipments_dataset.json").read_text(encoding="utf-8")
    assert "expected_state" not in text and "acceptable_root_causes" not in text
