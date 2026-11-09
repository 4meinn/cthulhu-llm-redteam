import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WEB_TESTS = Path(__file__).parent / "web"


def _run(script: str, expect: str) -> None:
    result = subprocess.run([NODE, str(WEB_TESTS / script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expect in result.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_layout_logic_in_node():
    _run("layout.test.js", "layout tests ok")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_panel_logic_in_node():
    _run("panel.test.js", "panel tests ok")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sweep_logic_in_node():
    _run("sweep.test.js", "sweep tests ok")
