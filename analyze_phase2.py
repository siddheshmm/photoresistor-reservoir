"""
Phase 2 analysis for the RGB LED + LDR reservoir: per-channel effect,
repeatability, and order-independence.

Reads the CSV logged by phase2_rgb_step_test.ino (columns:
order,channel,repeat,step,t_ms,pwm,adc; lines starting with '#' are
comments/instructions, not data) and produces:

  - Per (order, channel, step) plateau stats (mean/std of the last portion
    of each 4 s hold, where the response has settled)
  - Repeatability check: do the 3 repeats of a given channel+step agree
    within noise, or do they drift/disagree beyond it?
  - Order-independence check: does channel X behave the same whether it
    ran in order A (R,G,B) or order B (B,G,R)?
  - Rise/fall time constants (t63, t90) per channel, pooled across repeats
  - One annotated PNG per channel (all repeats + both orders overlaid) and
    a summary bar chart of plateau values with error bars

Usage:
    python3 analyze_phase2.py path/to/phase2.csv [output_dir]
"""

'''
--OUTPUT--

PS D:\photoresistor reservoir> python analyze_phase2.py ./data/phase2.csv outputs/phase2
Loaded 21600 rows from ./data/phase2.csv

=== Repeatability: spread across the 3 repeats, per (order, channel, step) ===
order ch  step  pwm    mean_of_repeats  std_across_repeats     cv%
A     B   0     255            13357.5                 2.1    0.0%
A     B   1     0                381.9                 2.8    0.7%
A     B   2     64             10668.3                36.1    0.3%
A     B   3     0                385.7                 2.2    0.6%
A     B   4     128            12171.0                21.5    0.2%
A     B   5     0                384.7                 1.7    0.4%
A     G   0     255            14869.2                 0.5    0.0%
A     G   1     0                380.8                 0.3    0.1%
A     G   2     64             13122.9                13.6    0.1%
A     G   3     0                380.7                 0.5    0.1%
A     G   4     128            14110.7                 8.5    0.1%
A     G   5     0                379.4                 1.2    0.3%
A     R   0     255            13747.5                 3.7    0.0%
A     R   1     0                383.1                 1.2    0.3%
A     R   2     64             11087.9                11.3    0.1%
A     R   3     0                383.7                 3.8    1.0%
A     R   4     128            12598.1                10.4    0.1%
A     R   5     0                383.1                 0.7    0.2%
B     B   0     255            13361.0                 0.8    0.0%
B     B   1     0                381.9                 0.7    0.2%
B     B   2     64             10674.9                38.1    0.4%
B     B   3     0                384.7                 0.4    0.1%
B     B   4     128            12173.7                29.0    0.2%
B     B   5     0                382.6                 0.8    0.2%
B     G   0     255            14871.5                 1.7    0.0%
B     G   1     0                375.9                 0.5    0.1%
B     G   2     64             13128.0                11.8    0.1%
B     G   3     0                378.3                 0.3    0.1%
B     G   4     128            14111.1                 4.0    0.0%
B     G   5     0                376.7                 0.5    0.1%
B     R   0     255            13750.3                 7.0    0.1%
B     R   1     0                377.6                 1.1    0.3%
B     R   2     64             11099.8                21.9    0.2%
B     R   3     0                381.2                 0.3    0.1%
B     R   4     128            12603.1                 4.3    0.0%
B     R   5     0                380.3                 0.9    0.2%

Largest repeat-to-repeat spread (top 5 by coefficient of variation):
  order=A ch=R step=3: CV=1.0%
  order=A ch=B step=1: CV=0.7%
  order=A ch=B step=3: CV=0.6%
  order=A ch=B step=5: CV=0.4%
  order=B ch=B step=2: CV=0.4%

=== Order independence: same channel+step, order A vs order B ===
order               A        B  diff  pct_diff
channel step                                  
B       0     13357.5  13361.0  -3.5      -0.0
        1       381.9    381.9   0.0       0.0
        2     10668.3  10674.9  -6.5      -0.1
        3       385.7    384.7   1.0       0.3
        4     12171.0  12173.7  -2.7      -0.0
        5       384.7    382.6   2.1       0.5
G       0     14869.2  14871.5  -2.3      -0.0
        1       380.8    375.9   4.9       1.3
        2     13122.9  13128.0  -5.1      -0.0
        3       380.7    378.3   2.5       0.7
        4     14110.7  14111.1  -0.4      -0.0
        5       379.4    376.7   2.7       0.7
R       0     13747.5  13750.3  -2.7      -0.0
        1       383.1    377.6   5.5       1.4
        2     11087.9  11099.8 -11.9      -0.1
        3       383.7    381.2   2.5       0.7
        4     12598.1  12603.1  -5.0      -0.0
        5       383.1    380.3   2.9       0.7

Largest order A vs B differences (top 3 by % difference):
order             A      B  diff  pct_diff
channel step                              
R       1     383.1  377.6   5.5       1.4
G       1     380.8  375.9   4.9       1.3
R       5     383.1  380.3   2.9       0.7

=== Time constants (pooled across repeats/orders, mean trajectory) ===
channel     segment      x0    xend  t63_ms  t90_ms
      R rise_0to255   501.3 13748.6    20.0    60.0
      R fall_255to0 13745.2   382.3   100.0   240.0
      G rise_0to255   494.5 14870.1    20.0    20.0
      G fall_255to0 14868.3   380.0   100.0   220.0
      B rise_0to255   498.5 13358.1    20.0    40.0
      B fall_255to0 13359.5   383.3    60.0   160.0

Saved outputs/phase2\channel_R_repeats_and_orders.png
Saved outputs/phase2\channel_G_repeats_and_orders.png
Saved outputs/phase2\channel_B_repeats_and_orders.png
Saved outputs/phase2\plateau_summary_by_channel_order.png
PS D:\photoresistor reservoir> 
'''

import sys
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CH_COLOURS = {"R": "#D85A30", "G": "#3B8C2A", "B": "#378ADD"}
ORDER_STYLE = {"A": "-", "B": "--"}  # A = R,G,B ; B = B,G,R
PLATEAU_TAIL_MS = 1500   # use the last 1500 ms of each 4000 ms hold as "settled"


def load_phase2_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, comment="#", names=[
        "order", "channel", "repeat", "step", "t_ms", "pwm", "adc"
    ], header=0)
    df = df.dropna()
    for c in ["repeat", "step", "t_ms", "pwm", "adc"]:
        df[c] = df[c].astype(float)
    df["repeat"] = df["repeat"].astype(int)
    df["step"] = df["step"].astype(int)
    return df


def plateau_table(df: pd.DataFrame) -> pd.DataFrame:
    """Mean/std of the settled tail of every (order, channel, repeat, step)."""
    rows = []
    g_all = df.groupby(["order", "channel", "repeat", "step"])
    for (order, ch, rep, step), g in g_all:
        tail = g[g["t_ms"] >= g["t_ms"].max() - PLATEAU_TAIL_MS]
        rows.append({
            "order": order, "channel": ch, "repeat": rep, "step": step,
            "pwm": g["pwm"].iloc[0],
            "plateau_mean": tail["adc"].mean(),
            "plateau_std": tail["adc"].std(),
        })
    return pd.DataFrame(rows)


def repeatability_report(plat: pd.DataFrame):
    print("=== Repeatability: spread across the 3 repeats, per (order, channel, step) ===")
    print(f"{'order':<6}{'ch':<4}{'step':<6}{'pwm':<6}{'mean_of_repeats':>16}{'std_across_repeats':>20}{'cv%':>8}")
    worst = []
    for (order, ch, step), g in plat.groupby(["order", "channel", "step"]):
        m = g["plateau_mean"].mean()
        s = g["plateau_mean"].std()
        cv = 100 * s / m if m else float("nan")
        print(f"{order:<6}{ch:<4}{step:<6}{int(g['pwm'].iloc[0]):<6}{m:>16.1f}{s:>20.1f}{cv:>7.1f}%")
        worst.append((cv, order, ch, step))
    worst.sort(reverse=True)
    print("\nLargest repeat-to-repeat spread (top 5 by coefficient of variation):")
    for cv, order, ch, step in worst[:5]:
        print(f"  order={order} ch={ch} step={step}: CV={cv:.1f}%")
    print()


def order_independence_report(plat: pd.DataFrame):
    print("=== Order independence: same channel+step, order A vs order B ===")
    pivot = plat.groupby(["channel", "step", "order"])["plateau_mean"].mean().unstack("order")
    pivot["diff"] = pivot["A"] - pivot["B"]
    pivot["pct_diff"] = 100 * pivot["diff"] / pivot[["A", "B"]].mean(axis=1)
    print(pivot.round(1))
    print()
    worst = pivot.reindex(pivot["pct_diff"].abs().sort_values(ascending=False).index)
    print("Largest order A vs B differences (top 3 by % difference):")
    print(worst.head(3).round(1))
    print()


def time_constants(df: pd.DataFrame) -> pd.DataFrame:
    """t63/t90 for rise (step 0: 0->255) and fall (step 1: 255->0), pooled
    across repeats and orders for each channel, using the mean trajectory."""
    rows = []
    for ch in ["R", "G", "B"]:
        for step, label in [(0, "rise_0to255"), (1, "fall_255to0")]:
            g = df[(df["channel"] == ch) & (df["step"] == step)]
            # average across all repeats/orders at each t_ms
            traj = g.groupby("t_ms")["adc"].mean().sort_index()
            t = traj.index.values
            x = traj.values
            x0, xend = x[0], x[-100:].mean() if len(x) > 100 else x[-1]
            y = (x - x0) / (xend - x0) if xend != x0 else np.zeros_like(x)
            def cross(frac):
                idx = np.where(y >= frac)[0]
                return t[idx[0]] if len(idx) else np.nan
            rows.append({
                "channel": ch, "segment": label,
                "x0": x0, "xend": xend,
                "t63_ms": cross(0.632), "t90_ms": cross(0.9),
            })
    return pd.DataFrame(rows)


def plot_channel(df: pd.DataFrame, ch: str, out_path: str):
    g = df[df["channel"] == ch].copy()
    g["T"] = g.groupby(["order", "repeat"]).cumcount()  # not used, kept simple
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for order in ["A", "B"]:
        for rep in sorted(g["repeat"].unique()):
            gg = g[(g["order"] == order) & (g["repeat"] == rep)].sort_values(["step", "t_ms"])
            # build a continuous time axis across the 6 steps of this repeat
            t_cont = []
            offset = 0.0
            last_step = None
            for _, row in gg.iterrows():
                if last_step is not None and row["step"] != last_step:
                    offset += 4000.0
                t_cont.append(row["t_ms"] + offset)
                last_step = row["step"]
            ax.plot(np.array(t_cont) / 1000.0, gg["adc"],
                     color=CH_COLOURS[ch], linestyle=ORDER_STYLE[order],
                     lw=0.8, alpha=0.6 if order == "B" else 0.9,
                     label=f"order {order}" if rep == 0 else None)
    ax.set_title(f"Channel {ch}: all 3 repeats, both orders overlaid "
                 f"(solid=order A R,G,B / dashed=order B B,G,R)")
    ax.set_xlabel("Time within this channel's run (s)")
    ax.set_ylabel("A0 reading (14-bit counts)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"Saved {out_path}")


def plot_plateau_summary(plat: pd.DataFrame, out_path: str):
    fig, ax = plt.subplots(figsize=(10, 5))
    steps = sorted(plat["step"].unique())
    width = 0.12
    x = np.arange(len(steps))
    offset_i = 0
    for ch in ["R", "G", "B"]:
        for order in ["A", "B"]:
            means, stds = [], []
            for step in steps:
                sub = plat[(plat["channel"] == ch) & (plat["order"] == order) & (plat["step"] == step)]
                means.append(sub["plateau_mean"].mean())
                stds.append(sub["plateau_mean"].std())
            ax.bar(x + offset_i * width, means, width, yerr=stds, capsize=3,
                   color=CH_COLOURS[ch], alpha=0.95 if order == "A" else 0.5,
                   label=f"{ch} order {order}")
            offset_i += 1
    ax.set_xticks(x + width * 2.5)
    pwm_labels = [str(int(plat[plat["step"] == s]["pwm"].iloc[0])) for s in steps]
    ax.set_xticklabels([f"step {s}\n(pwm={p})" for s, p in zip(steps, pwm_labels)])
    ax.set_ylabel("Plateau A0 reading (mean ± std across 3 repeats)")
    ax.set_title("Plateau value per step, channel and order")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
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

    df = load_phase2_csv(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}\n")

    plat = plateau_table(df)
    repeatability_report(plat)
    order_independence_report(plat)

    print("=== Time constants (pooled across repeats/orders, mean trajectory) ===")
    tc = time_constants(df)
    print(tc.round(1).to_string(index=False))
    print()

    for ch in ["R", "G", "B"]:
        plot_channel(df, ch, os.path.join(out_dir, f"channel_{ch}_repeats_and_orders.png"))
    plot_plateau_summary(plat, os.path.join(out_dir, "plateau_summary_by_channel_order.png"))


if __name__ == "__main__":
    main()
