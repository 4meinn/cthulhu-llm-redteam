import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).parent / "web" / "layout.test.js"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_layout_logic_in_node():
    result = subprocess.run([NODE, str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "layout tests ok" in result.stdout
