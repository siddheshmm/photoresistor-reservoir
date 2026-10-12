"""
Phase 1 analysis for the RGB LED + LDR reservoir: baseline white-light check.

Reads the CSV logged by phase1_baseline_white.ino (columns: stage,idx,pwm,t_ms,adc;
lines starting with '#' are comments, not data). All three LED channels are
driven together.

Stage "onoff": N cycles of all-on (PWM 255) then all-off.
Stage "sweep": PWM 0,64,128,192,255,192,128,64,0 held 4 s each.

Checks printed and plotted:
  1. Light-driven:  ON vs OFF plateau contrast compared with the dark noise.
  2. Repeatability: ON and OFF plateau across cycles (mean, std, CV).
  3. Dynamics:      rise/fall t63 and t90 per cycle (20 ms sampling limit).
  4. Monotonic:     sweep plateau strictly increases going up and decreases going down.
  5. Hysteresis:    up-sweep vs down-sweep plateau at the same PWM.
  6. Compression:   share of the full swing already reached at each PWM level.
  7. Headroom:      highest reading vs ADC full scale (clipping check).

Thresholds below are simple, fixed, and chosen before looking at the data.
They are a screening aid, not a statistical test.

Usage:
    python3 analyze_phase1.py path/to/phase1.csv [output_dir]
"""

'''
PS D:\photoresistor reservoir> python analyze_phase1.py ./data/phase1.csv outputs/phase1
Loaded 3800 rows from ./data/phase1.csv

=== Stage onoff: all channels PWM 255 vs 0 ===
 cycle  on_mean  on_std  off_mean  off_std  rise_t63  rise_t90  fall_t63  fall_t90
     0  15436.8     3.3     361.3      5.5      20.0      20.0     120.0     240.0
     1  15434.2     4.5     362.0      5.4      20.0      20.0     120.0     240.0
     2  15434.2     5.8     360.9      4.8      20.0      20.0     120.0     240.0
     3  15431.8     3.1     361.2      6.6      20.0      20.0     120.0     240.0
     4  15430.9     4.0     361.0      4.5      20.0      20.0     120.0     220.0

ON plateau   15433.6  (CV across cycles 0.015%)
OFF plateau    361.3  (CV across cycles 0.121%)
Contrast     15072.3 counts = 2806x the OFF-plateau noise (5.4)
LDR resistance: OFF ~ 4,435 kOhm, ON ~ 6.2 kOhm (ratio ~ 721x)
rise t63:   20.0 ms  (std 0.0, 20 ms sample spacing)
rise t90:   20.0 ms  (std 0.0, 20 ms sample spacing)
fall t63:  120.0 ms  (std 0.0, 20 ms sample spacing)
fall t90:  236.0 ms  (std 8.9, 20 ms sample spacing)

=== Stage sweep: all channels together ===
 idx  pwm  plateau   std  creep phase
   0    0    353.9   4.3   -0.7    up
   1   64  14082.9 172.3   -0.8    up
   2  128  14852.4 111.4   -0.5    up
   3  192  15220.1  48.9    0.7    up
   4  255  15427.8   4.4   -0.1    up
   5  192  15209.7  56.7   -3.2  down
   6  128  14857.5 106.7    0.4  down
   7   64  14081.2 169.1   -3.2  down
   8    0    360.1   5.2   -3.3  down

Hysteresis (down-sweep minus up-sweep at the same PWM):
 pwm      up    down  down_minus_up  pct_of_swing
   0   353.9   360.1            6.2           0.0
  64 14082.9 14081.2           -1.7          -0.0
 128 14852.4 14857.5            5.1           0.0
 192 15220.1 15209.7          -10.3          -0.1

Share of full swing reached on the way up:
 pwm  plateau  pct_of_swing
   0    353.9           0.0
  64  14082.9          91.1
 128  14852.4          96.2
 192  15220.1          98.6
 255  15427.8         100.0

Largest within-hold creep (seconds 3-4 minus 2-3): 3.3 counts

=== Verdicts ===
PASS  Light-driven (contrast / noise)  2806x
PASS  Repeatable ON plateau  CV 0.015%
PASS  Repeatable OFF plateau  CV 0.121%
PASS  Monotonic going up  
PASS  Monotonic going down  
PASS  Low hysteresis  max 0.07% of swing
PASS  ADC headroom (no clipping)  949 counts below full scale, peak reading 15468

Saved outputs/phase1\onoff_cycles_overlaid.png
Saved outputs/phase1\onoff_rise_fall_zoom.png
Saved outputs/phase1\sweep_timeseries.png
Saved outputs/phase1\sweep_plateau_vs_pwm_and_compression.png
'''


import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ADC_MAX = 16383
R_FIXED_OHM = 100_000.0        # divider resistor to GND (LDR sits 5V -> A0)
TAIL_MS = 1000                 # settled-plateau window: last 1000 ms of each hold
HOLD_MS = 4000

# Screening thresholds
MAX_CYCLE_CV_PCT = 1.0         # ON/OFF plateau variation across cycles
MAX_HYSTERESIS_PCT = 2.0       # |up - down| as % of full swing
MIN_CONTRAST_OVER_NOISE = 50   # (ON - OFF) / OFF-plateau std
MIN_HEADROOM_COUNTS = 300      # ON plateau should sit this far below ADC full scale


def load_csv(path):
    df = pd.read_csv(path, comment="#", names=["stage", "idx", "pwm", "t_ms", "adc"], header=0)
    df = df.dropna()
    for c in ["idx", "pwm", "t_ms", "adc"]:
        df[c] = df[c].astype(float)
    df["idx"] = df["idx"].astype(int)
    df["pwm"] = df["pwm"].astype(int)
    return df


def plateau(g):
    tail = g[g["t_ms"] >= g["t_ms"].max() - TAIL_MS]
    return tail["adc"].mean(), tail["adc"].std()


def ldr_kohm(adc):
    v = np.clip(adc, 1, ADC_MAX - 1) / ADC_MAX * 5.0
    return R_FIXED_OHM * (5.0 / v - 1.0) / 1000.0


def crossing(t, y, frac):
    idx = np.where(y >= frac)[0]
    return float(t[idx[0]]) if len(idx) else np.nan


def step_times(g, x0, xend):
    """t63 / t90 of a step from level x0 to level xend (works for rise or fall)."""
    t = g["t_ms"].values
    x = g["adc"].values
    if xend == x0:
        return np.nan, np.nan
    y = (x - x0) / (xend - x0)
    return crossing(t, y, 0.632), crossing(t, y, 0.9)


def onoff_analysis(df):
    on = df[(df.stage == "onoff") & (df.pwm == 255)]
    off = df[(df.stage == "onoff") & (df.pwm == 0)]
    cycles = sorted(on["idx"].unique())
    rows = []
    prev_off = None
    for c in cycles:
        g_on = on[on.idx == c]
        g_off = off[off.idx == c]
        on_m, on_s = plateau(g_on)
        off_m, off_s = plateau(g_off)
        x0_rise = prev_off if prev_off is not None else off_m
        r63, r90 = step_times(g_on, x0_rise, on_m)
        f63, f90 = step_times(g_off, on_m, off_m)
        rows.append(dict(cycle=c, on_mean=on_m, on_std=on_s, off_mean=off_m, off_std=off_s,
                         rise_t63=r63, rise_t90=r90, fall_t63=f63, fall_t90=f90))
        prev_off = off_m
    return pd.DataFrame(rows), on, off


def sweep_analysis(df):
    sw = df[df.stage == "sweep"]
    rows = []
    for idx, g in sw.groupby("idx"):
        m, s = plateau(g)
        # settling check: does the plateau still creep between seconds 2-3 and 3-4?
        a = g[(g.t_ms >= 2000) & (g.t_ms < 3000)]["adc"].mean()
        b = g[(g.t_ms >= 3000)]["adc"].mean()
        rows.append(dict(idx=idx, pwm=int(g.pwm.iloc[0]), plateau=m, std=s, creep=b - a))
    t = pd.DataFrame(rows).sort_values("idx").reset_index(drop=True)
    n = len(t)
    peak = int(t["plateau"].idxmax())
    t["phase"] = ["up" if i <= peak else "down" for i in range(n)]
    return t, sw


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    csv_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)

    df = load_csv(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}\n")

    # ---------------- on/off ----------------
    cyc, on, off = onoff_analysis(df)
    print("=== Stage onoff: all channels PWM 255 vs 0 ===")
    print(cyc.round(1).to_string(index=False))
    on_mean, off_mean = cyc["on_mean"].mean(), cyc["off_mean"].mean()
    on_cv = 100 * cyc["on_mean"].std() / on_mean
    off_cv = 100 * cyc["off_mean"].std() / off_mean
    contrast = on_mean - off_mean
    noise = cyc["off_std"].mean()
    print(f"\nON plateau  {on_mean:8.1f}  (CV across cycles {on_cv:.3f}%)")
    print(f"OFF plateau {off_mean:8.1f}  (CV across cycles {off_cv:.3f}%)")
    print(f"Contrast    {contrast:8.1f} counts = {contrast / noise:.0f}x the OFF-plateau noise ({noise:.1f})")
    print(f"LDR resistance: OFF ~ {ldr_kohm(off_mean):,.0f} kOhm, ON ~ {ldr_kohm(on_mean):,.1f} kOhm "
          f"(ratio ~ {ldr_kohm(off_mean) / ldr_kohm(on_mean):,.0f}x)")
    for col, name in [("rise_t63", "rise t63"), ("rise_t90", "rise t90"),
                      ("fall_t63", "fall t63"), ("fall_t90", "fall t90")]:
        print(f"{name}: {cyc[col].mean():6.1f} ms  (std {cyc[col].std():.1f}, 20 ms sample spacing)")
    print()

    # ---------------- sweep ----------------
    sw_t, sw = sweep_analysis(df)
    print("=== Stage sweep: all channels together ===")
    print(sw_t.round(1).to_string(index=False))
    up = sw_t[sw_t.phase == "up"]
    down = sw_t[sw_t.phase == "down"]
    mono_up = bool(np.all(np.diff(up["plateau"].values) > 0))
    mono_down = bool(np.all(np.diff(down["plateau"].values) < 0))
    swing = up["plateau"].max() - up["plateau"].min()

    # hysteresis: compare up and down plateaus at matching PWM (excluding the peak itself)
    hyst_rows = []
    for p in sorted(set(up["pwm"]) & set(down["pwm"])):
        u = up[up.pwm == p]["plateau"].iloc[0]
        d = down[down.pwm == p]["plateau"].iloc[0]
        hyst_rows.append((p, u, d, d - u, 100 * (d - u) / swing))
    hyst = pd.DataFrame(hyst_rows, columns=["pwm", "up", "down", "down_minus_up", "pct_of_swing"])
    print("\nHysteresis (down-sweep minus up-sweep at the same PWM):")
    print(hyst.round(1).to_string(index=False))
    max_hyst = hyst["pct_of_swing"].abs().max()

    # compression: share of the full dark->255 swing reached at each PWM on the way up
    lo = up["plateau"].iloc[0]
    hi = up["plateau"].max()
    up = up.assign(pct_of_swing=100 * (up["plateau"] - lo) / (hi - lo))
    print("\nShare of full swing reached on the way up:")
    print(up[["pwm", "plateau", "pct_of_swing"]].round(1).to_string(index=False))
    print(f"\nLargest within-hold creep (seconds 3-4 minus 2-3): "
          f"{sw_t['creep'].abs().max():.1f} counts")
    print()

    # ---------------- verdicts ----------------
    peak_reading = df["adc"].max()
    headroom = ADC_MAX - on_mean
    checks = [
        ("Light-driven (contrast / noise)", contrast / noise >= MIN_CONTRAST_OVER_NOISE,
         f"{contrast / noise:.0f}x"),
        ("Repeatable ON plateau", on_cv <= MAX_CYCLE_CV_PCT, f"CV {on_cv:.3f}%"),
        ("Repeatable OFF plateau", off_cv <= MAX_CYCLE_CV_PCT, f"CV {off_cv:.3f}%"),
        ("Monotonic going up", mono_up, ""),
        ("Monotonic going down", mono_down, ""),
        ("Low hysteresis", max_hyst <= MAX_HYSTERESIS_PCT, f"max {max_hyst:.2f}% of swing"),
        ("ADC headroom (no clipping)", headroom >= MIN_HEADROOM_COUNTS,
         f"{headroom:.0f} counts below full scale, peak reading {peak_reading:.0f}"),
    ]
    print("=== Verdicts ===")
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    print()

    # ---------------- plots ----------------
    # 1. on/off overlay
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    for c in sorted(on["idx"].unique()):
        g = on[on.idx == c]
        axs[0].plot(g.t_ms, g.adc, lw=1, alpha=0.8, label=f"cycle {c}")
        h = off[off.idx == c]
        axs[1].plot(h.t_ms, h.adc, lw=1, alpha=0.8, label=f"cycle {c}")
    axs[0].set_title("All channels OFF to ON (PWM 255), 5 cycles overlaid")
    axs[1].set_title("All channels ON to OFF, 5 cycles overlaid")
    for ax in axs:
        ax.set_xlabel("Time after step (ms)")
        ax.set_ylabel("A0 reading (14-bit counts)")
        ax.grid(alpha=0.3)
    axs[0].legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    p1 = os.path.join(out_dir, "onoff_cycles_overlaid.png")
    fig.savefig(p1, dpi=140)
    plt.close(fig)

    # 2. first 400 ms zoom
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    for c in sorted(on["idx"].unique()):
        g = on[(on.idx == c) & (on.t_ms <= 400)]
        h = off[(off.idx == c) & (off.t_ms <= 400)]
        axs[0].plot(g.t_ms, g.adc, "o-", ms=3, lw=0.8, alpha=0.8)
        axs[1].plot(h.t_ms, h.adc, "o-", ms=3, lw=0.8, alpha=0.8)
    axs[0].set_title("Rise, first 400 ms (20 ms sampling)")
    axs[1].set_title("Fall, first 400 ms (20 ms sampling)")
    for ax in axs:
        ax.set_xlabel("Time after step (ms)")
        ax.set_ylabel("A0 reading (14-bit counts)")
        ax.grid(alpha=0.3)
    fig.tight_layout()
    p2 = os.path.join(out_dir, "onoff_rise_fall_zoom.png")
    fig.savefig(p2, dpi=140)
    plt.close(fig)

    # 3. sweep time series
    fig, ax = plt.subplots(figsize=(12, 4.5))
    t_cont = (sw["idx"] * HOLD_MS + sw["t_ms"]) / 1000.0
    ax.plot(t_cont, sw["adc"], lw=0.8, color="#444444")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("A0 reading (14-bit counts)")
    ax.set_title("Brightness sweep, all channels together (PWM staircase in grey)")
    ax.grid(alpha=0.3)
    ax2 = ax.twinx()
    ax2.step(t_cont, sw["pwm"], where="post", color="0.6", lw=1)
    ax2.set_ylabel("PWM", color="0.5")
    ax2.set_ylim(0, 300)
    fig.tight_layout()
    p3 = os.path.join(out_dir, "sweep_timeseries.png")
    fig.savefig(p3, dpi=140)
    plt.close(fig)

    # 4. plateau vs PWM, up and down, with compression
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    axs[0].errorbar(up["pwm"], up["plateau"], yerr=up["std"], fmt="o-", capsize=3, label="sweep up")
    axs[0].errorbar(down["pwm"], down["plateau"], yerr=down["std"], fmt="s--", capsize=3, label="sweep down")
    axs[0].axhline(ADC_MAX, color="red", ls=":", lw=1)
    axs[0].text(5, ADC_MAX - 500, "ADC full scale", color="red", fontsize=8, va="top")
    axs[0].set_xlabel("PWM (all channels)")
    axs[0].set_ylabel("Plateau A0 (counts, mean +/- std over last 1 s)")
    axs[0].set_title("Plateau vs brightness: up and down overlap")
    axs[0].legend()
    axs[0].grid(alpha=0.3)
    axs[1].plot(up["pwm"], up["pct_of_swing"], "o-", color="#D85A30")
    axs[1].set_xlabel("PWM (all channels)")
    axs[1].set_ylabel("% of full dark-to-255 swing reached")
    axs[1].set_title("Compression: most of the swing happens at low PWM")
    axs[1].set_ylim(0, 105)
    axs[1].grid(alpha=0.3)
    fig.tight_layout()
    p4 = os.path.join(out_dir, "sweep_plateau_vs_pwm_and_compression.png")
    fig.savefig(p4, dpi=140)
    plt.close(fig)

    for p in (p1, p2, p3, p4):
        print(f"Saved {p}")


if __name__ == "__main__":
    main()
