"""The analysis notebook must only read columns that the capture pipeline actually writes."""
import re
from pathlib import Path

import pytest

from tools.decode_fields import (
    decode_hr_response,
    decode_ir_block1,
    decode_ir_block2,
    decode_ir_block3,
)
from tools.tcp_poller import REGISTER_FIELDS

TOOLS = Path(__file__).resolve().parent.parent / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Columns join_streams adds around the decoded fields.
JOIN_COLUMNS = {"ts", "device", "fc", "addr", "count", "active_tag", "tag_source"}


def _decoded_columns():
    cols = set()
    for decode, name in ((decode_hr_response, "sample_hr_response.bin"),
                         (decode_ir_block1, "sample_ir_block1.bin"),
                         (decode_ir_block2, "sample_ir_block2.bin"),
                         (decode_ir_block3, "sample_ir_block3.bin")):
        cols |= decode((FIXTURES / name).read_bytes()).keys()
    return cols


@pytest.mark.parametrize("name", ["build_notebook.py", "analysis_template.ipynb"])
def test_notebook_tcp_columns_exist_in_poller_output(name):
    text = (TOOLS / name).read_text()
    referenced = set(re.findall(r"\btcp_(\w+)", text))
    assert referenced, f"{name} reads no tcp_ columns; test is not checking anything"
    unknown = referenced - set(REGISTER_FIELDS)
    assert not unknown, f"{name} reads tcp_ columns the poller never writes: {sorted(unknown)}"


@pytest.mark.parametrize("name", ["build_notebook.py", "analysis_template.ipynb"])
def test_notebook_wire_columns_exist_in_decoder_output(name):
    text = (TOOLS / name).read_text()
    # df['x'], df.get('x', ...), 'x' in df.columns, {'x', 'y'}.issubset(df.columns)
    referenced = set(re.findall(r"df(?:\.get\(|\[\[?)\s*'(\w+)'", text))
    referenced |= set(re.findall(r"'(\w+)' in df\.columns", text))
    for group in re.findall(r"\{([^{}]*)\}\.issubset\(df\.columns\)", text):
        referenced |= set(re.findall(r"'(\w+)'", group))
    wire = {c for c in referenced if not c.startswith("tcp_")}
    assert wire, f"{name} reads no wire columns; test is not checking anything"
    unknown = wire - _decoded_columns() - JOIN_COLUMNS
    assert not unknown, f"{name} reads columns the decoder never writes: {sorted(unknown)}"


@pytest.mark.parametrize("name", ["build_notebook.py", "analysis_template.ipynb"])
def test_notebook_labels_hr19_bits_from_decoder_not_pace(name):
    text = (TOOLS / name).read_text()
    assert "HR19_BITS" in text
    assert "PACK_ALARM_BITS.get" not in text
