"""The analysis notebook must only read TCP columns that tcp_poller actually writes."""
import re
from pathlib import Path

import pytest

from tools.tcp_poller import REGISTER_FIELDS

TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.mark.parametrize("name", ["build_notebook.py", "analysis_template.ipynb"])
def test_notebook_tcp_columns_exist_in_poller_output(name):
    text = (TOOLS / name).read_text()
    referenced = set(re.findall(r"\btcp_(\w+)", text))
    assert referenced, f"{name} reads no tcp_ columns; test is not checking anything"
    unknown = referenced - set(REGISTER_FIELDS)
    assert not unknown, f"{name} reads tcp_ columns the poller never writes: {sorted(unknown)}"
