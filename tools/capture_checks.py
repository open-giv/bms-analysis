"""Checks for a joined capture (join_streams.py output) that answer the open G3 LV questions.

1. limit_roles   -- does charging (and discharging) current follow the HR26 or the HR27 limit?
                    docs/02 labels HR26 charge and HR27 discharge; G3 LV firmware suggests the
                    reverse (see the G3 LV note in docs/02).
2. end_of_charge -- charging current by pack voltage (HR22), to see where the inverter tapers
                    and the highest voltage it reaches.
3. cold_boots    -- after each gap in the HR poll (inverter off), how long from the first poll
                    to the first real battery current.

All three read the device-1 FC3 HR poll rows only. HR23 is positive for charge.

Run: python tools/capture_checks.py path/to/joined.parquet
"""
import sys
from pathlib import Path

import pandas as pd


def _hr_rows(df: pd.DataFrame) -> pd.DataFrame:
    hr = df[(df["device"] == 1) & (df["fc"] == 3)].copy()
    return hr.sort_values("ts").reset_index(drop=True)


def _missing(df: pd.DataFrame, cols) -> list:
    return [c for c in cols if c not in df.columns or df[c].isna().all()]


def limit_roles(df: pd.DataFrame, min_current_A: float = 1.0, at_limit_share: float = 0.2) -> dict:
    """For charge and discharge separately, say which of HR26/HR27 the current follows.

    A limit is a candidate if the current (almost) never goes above it. If only one limit is a
    candidate, the current follows it. If both are, the current follows the one it sits at in at
    least `at_limit_share` of rows. Otherwise the result is inconclusive, with the reason.
    """
    hr = _hr_rows(df)
    amps = hr["hr23_pack_current_cA"] / 100
    l26 = hr["hr26_limit_cA"] / 100
    l27 = hr["hr27_limit_cA"] / 100
    result = {}
    for name, mask in (("charge", amps > min_current_A), ("discharge", amps < -min_current_A)):
        i, a, b = amps[mask].abs(), l26[mask], l27[mask]
        entry = {"rows": int(mask.sum()), "max_current_A": float(i.max()) if len(i) else 0.0}
        if not len(i):
            entry["follows"] = "inconclusive (no rows)"
        elif ((a - b).abs() < 0.01).all():
            entry["follows"] = "inconclusive (limits equal)"
        else:
            tol_a, tol_b = (a * 0.03).clip(lower=0.5), (b * 0.03).clip(lower=0.5)
            under_a, under_b = (i <= a + tol_a).mean() >= 0.95, (i <= b + tol_b).mean() >= 0.95
            at_a, at_b = (i >= a - tol_a).mean(), (i >= b - tol_b).mean()
            entry.update({"share_at_hr26": float(at_a), "share_at_hr27": float(at_b)})
            if under_a and not under_b:
                entry["follows"] = "hr26"
            elif under_b and not under_a:
                entry["follows"] = "hr27"
            elif under_a and under_b and (at_a >= at_limit_share) != (at_b >= at_limit_share):
                entry["follows"] = "hr26" if at_a >= at_limit_share else "hr27"
            elif under_a and under_b:
                entry["follows"] = "inconclusive (current below both limits)"
            else:
                entry["follows"] = "inconclusive (current above both limits)"
        result[name] = entry
    return result


def end_of_charge(df: pd.DataFrame, bin_V: float = 0.2, min_current_A: float = 1.0) -> pd.DataFrame:
    """Median charging current per pack-voltage bin. attrs['max_volts'] is the highest voltage seen charging."""
    hr = _hr_rows(df)
    amps = hr["hr23_pack_current_cA"] / 100
    volts = hr["hr22_pack_voltage_cV"] / 100
    charging = amps > min_current_A
    v, i = volts[charging], amps[charging]
    bins = ((v / bin_V).round() * bin_V).round(2)
    table = (pd.DataFrame({"volts": bins, "current": i})
             .groupby("volts")["current"].agg(["median", "size"]).reset_index()
             .rename(columns={"median": "median_current_A", "size": "rows"}))
    table.attrs["max_volts"] = float(v.max()) if len(v) else None
    return table


def cold_boots(df: pd.DataFrame, min_gap_s: float = 60, min_current_A: float = 1.0) -> list:
    """After each HR-poll gap longer than min_gap_s, time from the first poll to the first real current."""
    hr = _hr_rows(df)
    gaps = hr["ts"].diff().dt.total_seconds()
    boots = []
    for k in gaps.index[gaps > min_gap_s]:
        start = hr.loc[k, "ts"]
        after = hr.loc[k:]
        live = after[(after["hr23_pack_current_cA"] / 100).abs() >= min_current_A]
        boots.append({
            "restart": start,
            "gap_s": float(gaps[k]),
            "first_current_after_s": (float((live["ts"].iloc[0] - start).total_seconds()) if len(live) else None),
        })
    return boots


def report(df: pd.DataFrame) -> str:
    """Run each check that the capture has the columns for, and say which were skipped."""
    lines = []
    missing = _missing(df, ("hr23_pack_current_cA", "hr26_limit_cA", "hr27_limit_cA"))
    if missing:
        lines.append(f"== HR26 / HR27: skipped, missing {', '.join(missing)}")
    else:
        lines.append("== HR26 / HR27: which limit does the current follow?")
        for name, r in limit_roles(df).items():
            shares = ""
            if "share_at_hr26" in r:
                shares = f" (at HR26 {r['share_at_hr26']:.0%} of rows, at HR27 {r['share_at_hr27']:.0%})"
            lines.append(f"  {name:9s}: {r['follows']} -- {r['rows']} rows, max {r['max_current_A']:.1f} A{shares}")
    missing = _missing(df, ("hr23_pack_current_cA", "hr22_pack_voltage_cV"))
    if missing:
        lines.append(f"== End of charge: skipped, missing {', '.join(missing)}")
    else:
        table = end_of_charge(df)
        lines.append(f"== End of charge: highest charging voltage {table.attrs['max_volts']} V")
        for _, row in table[table["volts"] >= 53.0].iterrows():
            lines.append(f"  {row['volts']:5.1f} V  median {row['median_current_A']:6.1f} A  ({int(row['rows'])} rows)")
    missing = _missing(df, ("hr23_pack_current_cA",))
    if missing:
        lines.append(f"== Cold boots: skipped, missing {', '.join(missing)}")
    else:
        boots = cold_boots(df)
        lines.append(f"== Cold boots: {len(boots)} found")
        for b in boots:
            after = "no current yet" if b["first_current_after_s"] is None else f"current after {b['first_current_after_s']:.1f} s"
            lines.append(f"  {b['restart']}: poll gap {b['gap_s']:.0f} s, {after}")
    if any("skipped" in l for l in lines):
        lines.append("Skipped checks need columns from the current decoder: rerun join_streams.py on the raw wire log.")
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print("Usage: python tools/capture_checks.py path/to/joined.parquet", file=sys.stderr)
        sys.exit(2)
    print(report(pd.read_parquet(Path(sys.argv[1]))))


if __name__ == "__main__":
    main()
