"""Generate analysis_template.ipynb -- the analyst's starting point for a campaign.

Run: `python tools/build_notebook.py`
Output: tools/analysis_template.ipynb (overwritten if it exists).

The notebook is structured as: setup, PACE reference import, timeline overview,
then one section per unknown (A, B, C, D) scaffolded as PACE-hypothesis-first
analysis with a fall-through to general analysis.
"""
import json
from pathlib import Path

import nbformat as nbf


def _md(text: str):
    return nbf.v4.new_markdown_cell(text)


def _code(text: str):
    return nbf.v4.new_code_cell(text)


def build():
    nb = nbf.v4.new_notebook()
    cells = []

    cells.append(_md(
        "# BMS Validation Lab -- Analysis Notebook\n\n"
        "This is a scaffold. Fill in the path to your campaign's `joined.parquet` "
        "and work through each unknown section. Each section starts with the most "
        "likely PACE hypothesis and falls through to general analysis if the PACE "
        "hypothesis does not fit.\n\n"
        "This notebook is the analyst's starting point for a validation-lab campaign capture "
        "(see `docs/06-wire-captures.md` for the methodology)."
    ))

    cells.append(_md("## Setup"))
    cells.append(_code(
        "from pathlib import Path\n"
        "import pandas as pd\n"
        "import numpy as np\n"
        "import matplotlib.pyplot as plt\n"
        "\n"
        "CAPTURE = Path('path/to/your/capture/directory')  # set this to your captures directory before running\n"
        "df = pd.read_parquet(CAPTURE / 'joined.parquet')\n"
        "print(df.shape, df.columns.tolist())\n"
    ))

    cells.append(_md("## PACE reference"))
    cells.append(_code(
        "import sys\n"
        "sys.path.insert(0, str(Path('.').resolve().parent))\n"
        "from tools.pace_reference import (\n"
        "    PACK_ALARM_BITS, PACK_STATUS_BITS, PROTECTION_FIELD_NAMES,\n"
        ")\n"
        "PACK_ALARM_BITS\n"
    ))

    cells.append(_md("## Timeline overview"))
    cells.append(_code(
        "fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)\n"
        "axes[0].plot(df['ts'], df.get('tcp_soc', pd.Series([], dtype=float)), label='TCP SoC')\n"
        "axes[0].set_ylabel('SoC %')\n"
        "axes[1].plot(df['ts'], df.get('hr23_pack_current_cA', pd.Series([], dtype=float)) / 100, label='Pack current (A)')\n"
        "axes[1].set_ylabel('A')\n"
        "axes[2].plot(df['ts'], df.get('pack_voltage_mV', pd.Series([], dtype=float)) / 1000, label='Pack V')\n"
        "axes[2].set_ylabel('V')\n"
        "for ax in axes:\n"
        "    ax.legend(loc='upper right')\n"
        "    ax.grid(True, alpha=0.3)\n"
        "plt.tight_layout()\n"
    ))

    cells.append(_md(
        "## A. Reg 11 (`hr11_capacity_Ah`) -- installed capacity?\n\n"
        "**Current reading:** HR11 is the capacity of the batteries online, in whole Ah. "
        "In a 66-hour G3 capture it stayed at 186 (one 9.5 kWh pack) while SoC moved "
        "between 4% and 95%. See `docs/06-wire-captures.md`.\n\n"
        "**Check:** HR11 should equal the IR Block 2 design capacity (`design_cap_cAh / 100`) "
        "times the number of packs online, and should not move with SoC.\n"
    ))
    cells.append(_code(
        "# Check: HR11 is fixed and equals design capacity x packs online\n"
        "if {'hr11_capacity_Ah', 'design_cap_cAh', 'device'}.issubset(df.columns):\n"
        "    packs = df.loc[df['design_cap_cAh'] > 0, 'device'].nunique()\n"
        "    design_ah = df.loc[df['device'] == 1, 'design_cap_cAh'].dropna().median() / 100\n"
        "    print('HR11 values seen:', sorted(df['hr11_capacity_Ah'].dropna().unique()))\n"
        "    print(f'Design capacity {design_ah:.0f} Ah x {packs} pack(s) = {design_ah * packs:.0f} Ah')\n"
        "else:\n"
        "    print('Required columns missing; cannot check HR11')\n"
    ))
    cells.append(_code(
        "# Fall-through: if HR11 changes, plot it to see what it follows.\n"
        "if 'hr11_capacity_Ah' in df.columns and df['hr11_capacity_Ah'].nunique() > 1:\n"
        "    fig, ax = plt.subplots(figsize=(12, 4))\n"
        "    ax.plot(df['ts'], df['hr11_capacity_Ah'], label='HR11 (Ah)')\n"
        "    ax.set_ylabel('Ah'); ax.legend(); ax.grid(True, alpha=0.3)\n"
    ))

    cells.append(_md(
        "## B. Reg 19 status flags -- which PACE alarm bit is which?\n\n"
        "**PACE hypothesis (test first):** reg 19's 8 bits map 1:1 to PACE "
        "`CID2=0x44 GetAlarmInfo` pack-alarm-byte bit positions (see `PACK_ALARM_BITS`).\n\n"
        "Find scenarios where each TCP-reported state changes (charge, discharge, "
        "balancing, low-voltage protection, etc.) and check which reg 19 bits transition "
        "at the same moment.\n"
    ))
    cells.append(_code(
        "# Per-bit transition map: for each bit position, find the rows where it changes.\n"
        "if 'hr19_status' in df.columns:\n"
        "    # Only HR rows carry reg 19; the column is float because IR rows are NaN.\n"
        "    status = df['hr19_status'].dropna().astype(int)\n"
        "    bits = pd.DataFrame({\n"
        "        f'bit_{i}': (status.to_numpy() >> i) & 1 for i in range(8)\n"
        "    })\n"
        "    bits['ts'] = df.loc[status.index, 'ts'].values\n"
        "    transitions = {}\n"
        "    for i in range(8):\n"
        "        col = f'bit_{i}'\n"
        "        diff = bits[col].diff().abs() > 0\n"
        "        transitions[i] = bits.loc[diff, ['ts', col]]\n"
        "        print(f'bit {i} ({PACK_ALARM_BITS.get(i, \"?\")}): {len(transitions[i])} transitions')\n"
        "else:\n"
        "    print('reg 19 not present in this campaign')\n"
    ))

    cells.append(_md(
        "## C. Block 3 bytes 32-35 -- max and min temperature\n\n"
        "**Current reading:** max and min temperature in 0.1 degC. In a 66-hour G3 capture "
        "they match the inverter's reported `t_max` and `t_min` exactly, about 20 s later. "
        "See `docs/03-input-registers.md`.\n\n"
        "**Check:** plot them against the TCP values.\n"
    ))
    cells.append(_code(
        "if {'max_temp_decidegC', 'min_temp_decidegC'}.issubset(df.columns):\n"
        "    temps = df[['ts', 'max_temp_decidegC', 'min_temp_decidegC']].dropna()\n"
        "    fig, ax = plt.subplots(figsize=(12, 4))\n"
        "    ax.plot(temps['ts'], temps['max_temp_decidegC'] / 10, label='Block 3 max (degC)')\n"
        "    ax.plot(temps['ts'], temps['min_temp_decidegC'] / 10, label='Block 3 min (degC)')\n"
        "    if {'tcp_t_max', 'tcp_t_min'}.issubset(df.columns):\n"
        "        ax.plot(df['ts'], df['tcp_t_max'], '--', alpha=0.6, label='TCP t_max')\n"
        "        ax.plot(df['ts'], df['tcp_t_min'], '--', alpha=0.6, label='TCP t_min')\n"
        "    ax.set_ylabel('degC'); ax.legend(); ax.grid(True, alpha=0.3)\n"
        "else:\n"
        "    print('Block 3 temperatures not present')\n"
    ))

    cells.append(_md(
        "## D. Reg 17 dynamics -- counter, hash, or something else?\n\n"
        "**PACE hypothesis (test first):** reg 17 is a packet sequence counter -- "
        "increments monotonically each request. If so, `df['hr17_dynamic'].diff()` "
        "should be mostly +1 (modulo 65536).\n\n"
        "**Fall-through:** check entropy and correlation against pack current, voltage, "
        "and per-cell readings.\n"
    ))
    cells.append(_code(
        "if 'hr17_dynamic' in df.columns:\n"
        "    diffs = df['hr17_dynamic'].diff().value_counts().head(10)\n"
        "    print('Top 10 reg-17 inter-frame deltas:'); print(diffs)\n"
        "    print('\\nUnique values:', df['hr17_dynamic'].nunique())\n"
        "else:\n"
        "    print('reg 17 not present')\n"
    ))

    cells.append(_md(
        "## Findings summary\n\n"
        "Fill in below as conclusions emerge. Then export to "
        "`<capture>/findings.md` (un-redacted) and run `tools/redact.py` "
        "to produce the public version that goes in the PR to `open-giv/bms-analysis`.\n"
    ))
    cells.append(_md(
        "**A. Reg 11:** _to be filled in_\n\n"
        "**B. Reg 19 bits:** _to be filled in (per-bit table)_\n\n"
        "**C. Block 3 bytes 32-35:** _to be filled in_\n\n"
        "**D. Reg 17:** _to be filled in_\n"
    ))

    nb["cells"] = cells
    out = Path(__file__).parent / "analysis_template.ipynb"
    with open(out, "w") as f:
        nbf.write(nb, f)
    print(f"Wrote {out}")


if __name__ == "__main__":
    build()
