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
        "axes[0].plot(df['ts'], df.get('tcp_battery_soc', pd.Series([], dtype=float)), label='TCP SoC')\n"
        "axes[0].set_ylabel('SoC %')\n"
        "axes[1].plot(df['ts'], df.get('hr23_pack_current_dA', pd.Series([], dtype=float)) / 10, label='Pack current (A)')\n"
        "axes[1].set_ylabel('A')\n"
        "axes[2].plot(df['ts'], df.get('pack_voltage_mV', pd.Series([], dtype=float)) / 1000, label='Pack V')\n"
        "axes[2].set_ylabel('V')\n"
        "for ax in axes:\n"
        "    ax.legend(loc='upper right')\n"
        "    ax.grid(True, alpha=0.3)\n"
        "plt.tight_layout()\n"
    ))

    cells.append(_md(
        "## A. Reg 11 (`SoC * 100`) -- what does it actually track?\n\n"
        "**PACE hypothesis (test first):** main pack SoC, scaled by 100. PACE clients "
        "compute SoC as `REMAIN_CAP / TOTAL_CAP * 100`; reg 11 might be that value times 100.\n\n"
        "**Fall-through:** if reg 11 does not track main SoC, look for per-cell SoC, "
        "cycle-life percent, or a stale field.\n"
    ))
    cells.append(_code(
        "# PACE hypothesis: reg 11 == main pack SoC * 100\n"
        "if 'tcp_battery_soc' in df.columns and 'hr11_soc_x100' in df.columns:\n"
        "    fig, ax = plt.subplots(figsize=(8, 8))\n"
        "    ax.scatter(df['tcp_battery_soc'] * 100, df['hr11_soc_x100'], s=4, alpha=0.4)\n"
        "    lim = [0, 10000]; ax.plot(lim, lim, 'r--', alpha=0.5)\n"
        "    ax.set_xlabel('TCP SoC * 100'); ax.set_ylabel('HR reg 11')\n"
        "    ax.set_title('PACE hypothesis: reg 11 == main pack SoC * 100')\n"
        "else:\n"
        "    print('Required columns missing; cannot evaluate PACE hypothesis')\n"
    ))
    cells.append(_code(
        "# Fall-through: characterise reg 11's variation if it does not track main SoC.\n"
        "# Look at correlation against pack current, individual cell voltages, etc.\n"
        "df[['hr11_soc_x100']].describe()\n"
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
        "    bits = pd.DataFrame({\n"
        "        f'bit_{i}': (df['hr19_status'] >> i) & 1 for i in range(8)\n"
        "    })\n"
        "    bits['ts'] = df['ts'].values\n"
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
        "## C. Block 3 bytes 32-35 -- static threshold or dynamic tracker?\n\n"
        "**PACE hypothesis (test first):** these are per-cell over-/under-voltage protection "
        "thresholds from PACE `CID2=0x47 ChargeDischargeManagementInfo`. If so they should "
        "be near-static across all scenarios.\n\n"
        "**Fall-through:** if they vary, check whether they track max/min cell voltage "
        "(extreme-cell tracker) or some other dynamic value.\n"
    ))
    cells.append(_code(
        "if {'block3_b32_offset', 'block3_b34_offset'}.issubset(df.columns):\n"
        "    print('block3_b32 stats:'); print(df['block3_b32_offset'].describe())\n"
        "    print('\\nblock3_b34 stats:'); print(df['block3_b34_offset'].describe())\n"
        "    fig, ax = plt.subplots(figsize=(12, 4))\n"
        "    ax.plot(df['ts'], df['block3_b32_offset'], label='b32 (mV)')\n"
        "    ax.plot(df['ts'], df['block3_b34_offset'], label='b34 (mV)')\n"
        "    ax.set_ylabel('mV'); ax.legend(); ax.grid(True, alpha=0.3)\n"
        "else:\n"
        "    print('Block 3 unknown bytes not present')\n"
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
        "to produce the public version that goes in the PR to `kenbell/giv-bms-analysis`.\n"
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
