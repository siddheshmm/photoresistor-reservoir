"""
Phase 0 ground-check analysis for the RGB LED + LDR reservoir.

Reads the CSV logged by phase0_ground_checks.ino (columns: stage,t_ms,adc;
lines starting with '#' are instructions, not data) and produces:

  - Printed summary stats (mean, std, min, max, drift) per stage
  - One saved PNG per stage, each annotated with its pass/fail threshold
  - A combined overview PNG with all four stages

Usage:
    python3 analyze_phase0.py path/to/phase0.csv [output_dir]

If output_dir is omitted, plots are saved next to this script.
"""

"""
--OUTPUT--
PS D:\reservoir> python analyze_phase0.py ./data/phase0.csv outputs/phase0
D:\reservoir\analyze_phase0.py:32: SyntaxWarning: invalid escape sequence '\A'
  Saved D:\reservoir\A_dark_noise_floor.png
Loaded 15199 rows from ./data/phase0.csv

stage              n      mean      std     min     max     drift
A_dark          5999     351.9     11.8     264     564      -0.0
B_ambient       6000     533.9    167.2     225    1371      -4.1
C_short_gnd     1600       1.3      4.4       0      69       0.1
C_short_5v      1600   16381.8      4.2   16339   16383      -0.1

A_dark:       PASS (noise floor stable, std=11.8)
B_ambient:    FAIL (tube leaking light) (swing=1146 counts while LEDs off)
C_short_gnd:  PASS (mean=1.3, expect ~0)
C_short_5v:   PASS (mean=16381.8, expect ~16383)

Saved outputs/phase0\A_dark_noise_floor.png
Saved outputs/phase0\B_ambient_light_leakage.png
Saved outputs/phase0\C1_short_to_GND.png
Saved outputs/phase0\C2_short_to_5V.png
Saved outputs/phase0\phase0_overview_all_stages.png
PS D:\reservoir> 
"""

import sys
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Config: expected stage names and rough colours for plotting
# ---------------------------------------------------------------------------
STAGE_COLOURS = {
    "A_dark":       "#555555",
    "B_ambient":    "#D85A30",
    "C_short_gnd":  "#555555",
    "C_short_5v":   "#555555",
}

ADC_MAX = 16383  # 14-bit ADC full scale on the Uno R4


def load_phase0_csv(path: str) -> pd.DataFrame:
    """Load the sketch's CSV output, skipping '#' instruction/comment lines."""
    df = pd.read_csv(path, comment="#", names=["stage", "t_ms", "adc"], header=0)
    df = df.dropna()
    df["t_ms"] = df["t_ms"].astype(float)
    df["adc"] = df["adc"].astype(float)
    return df


def summarize_stage(df: pd.DataFrame, stage: str) -> dict:
    g = df[df["stage"] == stage]
    if g.empty:
        return {}
    stats = {
        "n": len(g),
        "mean": g["adc"].mean(),
        "std": g["adc"].std(),
        "min": g["adc"].min(),
        "max": g["adc"].max(),
    }
    # Drift check: compare first half vs second half of the window
    mid = g["t_ms"].median()
    first = g[g["t_ms"] < mid]["adc"]
    second = g[g["t_ms"] >= mid]["adc"]
    stats["first_half_mean"] = first.mean()
    stats["second_half_mean"] = second.mean()
    stats["drift"] = stats["second_half_mean"] - stats["first_half_mean"]
    return stats


def print_summary(df: pd.DataFrame) -> dict:
    print(f"{'stage':<14}{'n':>6}{'mean':>10}{'std':>9}{'min':>8}{'max':>8}{'drift':>10}")
    all_stats = {}
    for stage in df["stage"].unique():
        s = summarize_stage(df, stage)
        all_stats[stage] = s
        print(f"{stage:<14}{s['n']:>6}{s['mean']:>10.1f}{s['std']:>9.1f}"
              f"{s['min']:>8.0f}{s['max']:>8.0f}{s['drift']:>10.1f}")

    # Pass/fail notes printed after the table
    print()
    if "A_dark" in all_stats:
        s = all_stats["A_dark"]
        verdict = "PASS" if abs(s["drift"]) < 0.1 * s["mean"] else "CHECK"
        print(f"A_dark:       {verdict} (noise floor stable, std={s['std']:.1f})")
    if "B_ambient" in all_stats:
        s = all_stats["B_ambient"]
        swing = s["max"] - s["min"]
        verdict = "PASS" if swing < 1000 else "FAIL (tube leaking light)"
        print(f"B_ambient:    {verdict} (swing={swing:.0f} counts while LEDs off)")
    if "C_short_gnd" in all_stats:
        s = all_stats["C_short_gnd"]
        verdict = "PASS" if s["mean"] < 50 else "FAIL (A0 not shorting to GND)"
        print(f"C_short_gnd:  {verdict} (mean={s['mean']:.1f}, expect ~0)")
    if "C_short_5v" in all_stats:
        s = all_stats["C_short_5v"]
        verdict = "PASS" if s["mean"] > ADC_MAX - 200 else "FAIL (A0 not shorting to 5V)"
        print(f"C_short_5v:   {verdict} (mean={s['mean']:.1f}, expect ~{ADC_MAX})")
    return all_stats


def plot_stage(df: pd.DataFrame, stage: str, out_path: str, title: str,
               ylim=None, annotation: str = None):
    g = df[df["stage"] == stage]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(g["t_ms"] / 1000.0, g["adc"], lw=0.6, color=STAGE_COLOURS.get(stage, "#333333"))
    ax.set_title(title)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("A0 reading (14-bit counts, 0-16383)")
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(alpha=0.3)
    if annotation:
        ax.text(0.98, 0.02, annotation, transform=ax.transAxes,
                 ha="right", va="bottom", fontsize=9, color="0.3",
                 bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.85))
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"Saved {out_path}")


def plot_overview(df: pd.DataFrame, out_path: str):
    fig, axs = plt.subplots(2, 2, figsize=(13, 8))
    specs = [
        ("A_dark", "A: Dark noise floor (tube sealed, LEDs off)", (0, 700)),
        ("B_ambient", "B: Ambient leakage (tube sealed, LEDs off,\nroom light / torch varied outside)", (0, 1500)),
        ("C_short_gnd", "C1: A0 shorted to GND (expect ~0)", (-5, 60)),
        ("C_short_5v", "C2: A0 shorted to 5V (expect ~16383)", (16300, 16400)),
    ]
    for ax, (stage, title, ylim) in zip(axs.flat, specs):
        g = df[df["stage"] == stage]
        ax.plot(g["t_ms"] / 1000.0, g["adc"], lw=0.6, color=STAGE_COLOURS.get(stage, "#333333"))
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("A0 counts")
        ax.set_ylim(*ylim)
        ax.grid(alpha=0.3)
    fig.suptitle("Phase 0 ground checks — overview", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"Saved {out_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    csv_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)

    df = load_phase0_csv(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}\n")
    print_summary(df)
    print()

    plot_stage(
        df, "A_dark",
        os.path.join(out_dir, "A_dark_noise_floor.png"),
        "Phase 0A: Dark noise floor (tube sealed, LEDs off)",
        ylim=(0, 700),
        annotation="Pass: flat, no drift between first/second half of window",
    )
    plot_stage(
        df, "B_ambient",
        os.path.join(out_dir, "B_ambient_light_leakage.png"),
        "Phase 0B: Ambient light leakage (tube sealed, LEDs off,\nroom light / phone torch varied outside the tube)",
        ylim=(0, 1500),
        annotation="Pass: stays near the dark baseline regardless of outside light",
    )
    plot_stage(
        df, "C_short_gnd",
        os.path.join(out_dir, "C1_short_to_GND.png"),
        "Phase 0C1: A0 node shorted to GND (LDR removed)",
        ylim=(-5, 60),
        annotation="Pass: reads near 0",
    )
    plot_stage(
        df, "C_short_5v",
        os.path.join(out_dir, "C2_short_to_5V.png"),
        "Phase 0C2: A0 node shorted to 5V (LDR removed)",
        ylim=(16300, 16400),
        annotation="Pass: reads near 16383 (14-bit full scale)",
    )
    plot_overview(df, os.path.join(out_dir, "phase0_overview_all_stages.png"))


if __name__ == "__main__":
    main()
