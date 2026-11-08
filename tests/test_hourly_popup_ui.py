"""Run actual dashboard functions with a controllable JavaScript clock and DOM fixture."""
import ast
from pathlib import Path
import re
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for dashboard JavaScript checks")
def test_hourly_popup_ui_lifecycle(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "core/ui_dashboard.py").read_text(encoding="utf-8-sig"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(getattr(target, "id", None) == "DASHBOARD_HTML" for target in node.targets))
    html = ast.literal_eval(assignment.value)
    scripts = "\n".join(re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S))
    runtime = tmp_path / "dashboard.js"
    runtime.write_text(scripts, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(root / "tests/hourly_popup_ui_checks.cjs"), str(runtime)],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Hourly popup UI:" in result.stdout and "checks passed" in result.stdout
