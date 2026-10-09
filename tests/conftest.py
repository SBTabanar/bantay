import pytest
from bantay.types import ToolCall

@pytest.fixture
def bash():
    def _b(cmd, cwd="/Users/dev/proj", branch="main"):
        return ToolCall("Bash", {"command": cmd}, cwd=cwd, branch=branch)
    return _b
