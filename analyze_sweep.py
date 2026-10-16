r"""
Operating-point sweep analysis for the RGB LED + LDR reservoir (Phase 3, step 3.1).

Reads the three CSVs logged by phase3_4_reservoir.ino in mode 1 (columns
k,i,u,pwm_in,pwm_fb,adc; '#' lines are comments). In each file, one channel is stepped through
PWM levels 0,2,4,8,12,16,24,32,48,64,96,128,192,255. For every level the LED is ON for 150
samples (3 s) then OFF for 100 samples (2 s), sampled every 20 ms (k = level index, i = sample).

What it reports
  1. Per channel and level: settled plateau, noise, rise/fall times, share of the plateau already
     reached in the first 20 ms sample, and the sensitivity (counts per PWM unit).
  2. Channel efficiency: for green and red, the blue PWM that gives the same plateau
     (a ratio that stays constant means light from different LEDs simply adds up).
  3. Drive window for another channel: the PWM range that reproduces the plateau range of a
     reference window on blue (default PWM 5..75, which is what the blue reservoir runs used),
     with the number of distinct PWM levels that window offers (input resolution).
  4. Feedback estimate for Phase 4: a static (plateau-based) model of the red feedback loop
     for several fb values and xlo/xhi choices: mean state, share of readings clipped by the
     0..1 scaling, and loop gain. The model ignores the 20 ms dynamics, so treat it as an
     upper bound on loop gain and as a guide to avoid clipping, not as a prediction.

Usage:
    python3 analyze_sweep.py sweep_blue.csv sweep_green.csv sweep_red.csv [--out DIR]
        [--blue-bias 40 --blue-amp 35] [--mask "-1 1 1 -1 ..."]
"""
import argparse
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import PchipInterpolator

SLOT_MS = 20
ON_SAMPLES = 150
TAIL = 50                       # last 1 s of each phase is treated as settled
COLORS = {"blue": "#378ADD", "green": "#3B8C2A", "red": "#D85A30"}


def load(path):
    df = pd.read_csv(path, comment="#", names=["k", "i", "u", "pwm_in", "pwm_fb", "adc"], header=0)
    return df.dropna().astype({"k": int, "i": int})


def crossing_time(t, frac, thr):
    """First time the monotone-ish fraction curve reaches thr, linearly interpolated.
    Returns (time_ms, flag) where flag is '<' if already reached by the first sample."""
    idx = np.where(frac >= thr)[0]
    if len(idx) == 0:
        return np.nan, ""
    j = idx[0]
    if j == 0:
        return float(t[0]), "<"
    t0, t1, f0, f1 = t[j - 1], t[j], frac[j - 1], frac[j]
    return float(t0 + (thr - f0) * (t1 - t0) / (f1 - f0)), ""


def analyse_channel(df):
    rows = []
    prev_off = None
    for k, g in df.groupby("k"):
        g = g.sort_values("i")
        on = g[g.i < ON_SAMPLES]
        off = g[g.i >= ON_SAMPLES]
        level = int(on.pwm_in.max())
        plateau = on.adc.values[-TAIL:].mean()
        plateau_sd = on.adc.values[-TAIL:].std()
        off_base = off.adc.values[-TAIL:].mean()
        base = prev_off if prev_off is not None else on.adc.values[:1].mean()
        row = dict(level=level, plateau=plateau, noise=plateau_sd, off_base=off_base)
        if level > 0 and plateau - base > 200:
            frac = (on.adc.values - base) / (plateau - base)
            t = (on.i.values + 1) * SLOT_MS
            row["first_sample_share"] = float(frac[0])
            row["rise_t63"], row["rise_t63_flag"] = crossing_time(t, frac, 0.632)
            row["rise_t90"], row["rise_t90_flag"] = crossing_time(t, frac, 0.90)
            # fall: share of the way back down, from plateau to the OFF baseline
            ff = (plateau - off.adc.values) / (plateau - off_base)
            tf = (off.i.values - ON_SAMPLES + 1) * SLOT_MS
            row["fall_t63"], row["fall_t63_flag"] = crossing_time(tf, ff, 0.632)
            row["fall_t90"], row["fall_t90_flag"] = crossing_time(tf, ff, 0.90)
        rows.append(row)
        prev_off = off_base
    t = pd.DataFrame(rows).sort_values("level").reset_index(drop=True)
    t["sensitivity"] = np.nan
    s = np.diff(t["plateau"].values) / np.diff(t["level"].values)
    t.loc[1:, "sensitivity"] = s       # counts per PWM unit between this level and the previous one
    return t


def fmt_time(v, flag):
    if pd.isna(v):
        return "   n/a"
    return f"{flag}{v:5.0f}"


def make_interp(table):
    return PchipInterpolator(table["level"].values.astype(float), table["plateau"].values)


def inverse(interp, y, grid=np.linspace(0, 255, 5101)):
    return np.interp(y, interp(grid), grid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blue")
    ap.add_argument("green")
    ap.add_argument("red")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--blue-bias", type=int, default=40)
    ap.add_argument("--blue-amp", type=int, default=35)
    ap.add_argument("--mask", default="-1 1 1 -1 1 1 -1 -1 -1 -1 1 1 1 -1 1 1 1 -1 -1 -1",
                    help="mask signs of the reservoir runs (printed by analyze_phase3.py)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    tables = {}
    for name, path in (("blue", a.blue), ("green", a.green), ("red", a.red)):
        tables[name] = analyse_channel(load(path))

    # ------------------------------------------------------------- 1. per channel
    for name, t in tables.items():
        print(f"=== {name}: plateau (last 1 s of 3 s ON), times in ms with 20 ms sample spacing ===")
        print("level  plateau  noise  1st-sample%  rise t63  rise t90  fall t63  fall t90  counts/PWM")
        for _, r in t.iterrows():
            fs = f"{100 * r.first_sample_share:6.0f}" if "first_sample_share" in r and not pd.isna(r.get("first_sample_share")) else "   n/a"
            print(f"{int(r.level):5d} {r.plateau:8.0f} {r.noise:6.1f}   {fs}      "
                  f"{fmt_time(r.get('rise_t63'), r.get('rise_t63_flag', ''))}     "
                  f"{fmt_time(r.get('rise_t90'), r.get('rise_t90_flag', ''))}     "
                  f"{fmt_time(r.get('fall_t63'), r.get('fall_t63_flag', ''))}     "
                  f"{fmt_time(r.get('fall_t90'), r.get('fall_t90_flag', ''))}    "
                  f"{'' if pd.isna(r.sensitivity) else f'{r.sensitivity:8.1f}'}")
        print(f"dark baseline before first level ~ {t.loc[0, 'plateau']:.0f}; "
              f"OFF baseline after bright levels up to {t['off_base'].max():.0f} (slow residual after exposure)")
        print("('<' = already reached by the first 20 ms sample, so the true time is shorter)\n")

    # ------------------------------------------------------------- 2. channel efficiency
    hb = make_interp(tables["blue"])
    ymin, ymax = tables["blue"]["plateau"].min(), tables["blue"]["plateau"].max()
    print("=== Channel efficiency: blue PWM that gives the same plateau as 1 PWM unit of the channel ===")
    eff = {}
    for name in ("green", "red"):
        t = tables[name]
        rat = []
        for _, r in t.iterrows():
            if r.level >= 2 and ymin < r.plateau < ymax:
                rat.append((int(r.level), float(inverse(hb, r.plateau) / r.level)))
        eff[name] = rat
        txt = "  ".join(f"{l}:{x:.2f}" for l, x in rat)
        print(f"{name:5s} (level:ratio)  {txt}")
        mid = [x for l, x in rat if 8 <= l <= 96]
        print(f"      typical ratio {np.median(mid):.2f}, range {min(mid):.2f} to {max(mid):.2f} "
              f"(a constant ratio would mean light adds up simply)")
    print()

    # ------------------------------------------------------------- 3. drive window
    lo, hi = a.blue_bias - a.blue_amp, a.blue_bias + a.blue_amp
    x_lo, x_hi = float(hb(lo)), float(hb(hi))
    print(f"=== Drive window matching blue PWM {lo}..{hi} (plateau {x_lo:.0f} to {x_hi:.0f}) ===")
    print(f"blue : PWM {lo}..{hi} -> {2 * a.blue_amp + 1} distinct drive levels")
    windows = {}
    for name in ("green", "red"):
        h = make_interp(tables[name])
        p_lo, p_hi = float(inverse(h, x_lo)), float(inverse(h, x_hi))
        bias = int(round((p_lo + p_hi) / 2))
        amp = int(round((p_hi - p_lo) / 2))
        windows[name] = (bias, amp)
        print(f"{name:5s}: PWM {p_lo:.1f}..{p_hi:.1f} -> bias={bias} amp={amp}  "
              f"({2 * amp + 1} distinct drive levels across the input range)")
    print("Fewer distinct levels means coarser input resolution (PWM is an integer 0..255).\n")

    # ------------------------------------------------------------- 4. red feedback model
    hr = make_interp(tables["red"])
    grid = np.linspace(0, 255, 5101)
    yb_grid = hb(grid)

    def red_equiv(q):
        return np.interp(hr(np.clip(q, 0, 255)), yb_grid, grid)

    mask = np.array([float(s) for s in a.mask.split()])
    N = len(mask)
    rng = np.random.default_rng(0)

    def simulate(fb, xlo, xhi, K=3000):
        xs = np.zeros(N)
        xs_all, rho, c1, c0, cnt = [], [], 0, 0, 0
        for k in range(K):
            u0 = rng.uniform(-1, 1)
            p = np.clip(np.round(a.blue_bias + a.blue_amp * mask * u0), 0, 255)
            q = fb * xs
            L = np.clip(p + red_equiv(q), 0, 255)
            x = hb(L)
            raw = (x - xlo) / (xhi - xlo)
            if k > 100:
                xs_all.append(x)
                c1 += (raw >= 1).sum()
                c0 += (raw <= 0).sum()
                cnt += N
                dx = (hb(np.clip(p + red_equiv(q + 0.5), 0, 255)) - x) / 0.5
                ok = (raw > 0) & (raw < 1)
                rho.extend((fb * dx[ok] / (xhi - xlo)).tolist())
            xs = np.clip(raw, 0, 1)
        xs_all = np.array(xs_all)
        return (xs_all.mean(), np.percentile(xs_all, 5), np.percentile(xs_all, 95),
                100 * c1 / cnt, 100 * c0 / cnt, float(np.mean(rho)) if rho else 0.0)

    fb_list = [0, 8, 16, 32, 48, 64, 96]
    ranges = [(7600, 11100), (7500, 12500), (6000, 12500)]
    print("=== Red feedback, static plateau model (blue input at the reservoir drive window) ===")
    print("fb = red PWM at scaled state 1.0. clip_hi / clip_lo = share of readings outside xlo..xhi.")
    print("loop gain = change of next scaled state per unit of the scaled state, unclipped readings only.")
    model_rows = []
    for (xlo, xhi) in ranges:
        print(f"\nxlo={xlo} xhi={xhi}")
        print("  fb   mean_x    p5     p95   clip_hi%  clip_lo%  loop_gain")
        for fb in fb_list:
            m, p5, p95, c1, c0, rho = simulate(fb, xlo, xhi)
            print(f"{fb:4d} {m:8.0f} {p5:6.0f} {p95:6.0f}   {c1:6.1f}    {c0:6.1f}     {rho:5.2f}")
            model_rows.append((xlo, xhi, fb, m, p5, p95, c1, c0, rho))
    print()

    # ------------------------------------------------------------- plots
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.8))
    for name, t in tables.items():
        axs[0].plot(t["level"], t["plateau"], "o-", ms=4, color=COLORS[name], label=name)
        axs[1].plot(t["level"][1:], t["sensitivity"][1:], "o-", ms=4, color=COLORS[name], label=name)
    axs[0].axvspan(lo, hi, color="0.9", zorder=0)
    axs[0].set_xlabel("PWM")
    axs[0].set_ylabel("Settled plateau (counts)")
    axs[0].set_title("Plateau vs PWM (grey band = blue reservoir drive window)")
    axs[1].set_xscale("log")
    axs[1].set_yscale("log")
    axs[1].set_xlabel("PWM (upper end of the step)")
    axs[1].set_ylabel("Sensitivity (counts per PWM unit)")
    axs[1].set_title("Sensitivity falls steeply with drive (compression)")
    for ax in axs:
        ax.grid(alpha=0.3, which="both")
        ax.legend()
    fig.tight_layout()
    p = os.path.join(a.out, "sweep_plateau_and_sensitivity.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("Saved", p)

    # time response at a few levels
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.5), sharey=False)
    dfs = {"blue": load(a.blue), "green": load(a.green), "red": load(a.red)}
    for ax, name in zip(axs, ["blue", "green", "red"]):
        d = dfs[name]
        for lvl in (8, 16, 32, 64):
            g = d[(d.pwm_in == lvl) & (d.i < ON_SAMPLES)].sort_values("i")
            if g.empty:
                continue
            t = tables[name]
            pl = float(t.loc[t.level == lvl, "plateau"].iloc[0])
            base = float(t.loc[t.level == lvl, "off_base"].iloc[0])
            ax.plot((g.i + 1) * SLOT_MS, (g.adc - 500) / (pl - 500), "o-", ms=3, lw=1, label=f"PWM {lvl}")
        ax.set_xlim(0, 400)
        ax.set_ylim(0, 1.05)
        ax.set_xlabel("Time after LED on (ms)")
        ax.set_ylabel("Share of settled plateau")
        ax.set_title(f"{name}: rise at four levels (20 ms samples)")
        ax.grid(alpha=0.3)
        ax.legend(loc="lower right")
    fig.tight_layout()
    p = os.path.join(a.out, "sweep_rise_shapes.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("Saved", p)

    fig, ax = plt.subplots(figsize=(8, 4.8))
    for name in ("green", "red"):
        ls = [l for l, _ in eff[name]]
        ax.plot(ls, [x for _, x in eff[name]], "o-", color=COLORS[name], label=name)
    ax.set_xscale("log")
    ax.set_xlabel("Channel PWM")
    ax.set_ylabel("Equivalent blue PWM per PWM unit")
    ax.set_title("Channel efficiency relative to blue (flat = light adds up simply)")
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    fig.tight_layout()
    p = os.path.join(a.out, "sweep_channel_efficiency.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("Saved", p)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    for (xlo, xhi), c in zip(ranges, ["#D85A30", "#378ADD", "#3B8C2A"]):
        rows = [r for r in model_rows if r[0] == xlo and r[1] == xhi]
        axs[0].plot([r[2] for r in rows], [r[6] for r in rows], "o-", color=c, label=f"xlo={xlo} xhi={xhi}")
        axs[1].plot([r[2] for r in rows], [r[8] for r in rows], "o-", color=c, label=f"xlo={xlo} xhi={xhi}")
    axs[0].set_xlabel("fb")
    axs[0].set_ylabel("Readings clipped at the top of the scale (%)")
    axs[0].set_title("Clipping vs feedback strength (static model)")
    axs[1].set_xlabel("fb")
    axs[1].set_ylabel("Loop gain (unclipped readings)")
    axs[1].set_title("Loop gain vs feedback strength (static model, upper bound)")
    for ax in axs:
        ax.grid(alpha=0.3)
        ax.legend()
    fig.tight_layout()
    p = os.path.join(a.out, "sweep_red_feedback_model.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("Saved", p)


if __name__ == "__main__":
    main()
