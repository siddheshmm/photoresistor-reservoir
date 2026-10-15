r"""
Phase 3 analysis for the RGB LED + LDR reservoir (no feedback).

Reads a CSV logged by phase3_4_reservoir.ino in mode 0 (columns k,i,u,pwm_in,pwm_fb,adc;
lines starting with '#' are comments, including the '# PARAMS' line that records the
configuration of the run).

What it does
  1. Integrity: row count, contiguous steps, constant u within a step, firmware timing footer.
  2. State range: percentiles of the readings, suggested xlo/xhi for Phase 4, saturation check.
  3. Node behaviour: input-output map, slot-to-slot lag response (how fast the node forgets).
  4. Readout tasks (ridge regression on the 'nodes' state values of each input step):
       - memory capacity MC_d for d = 0..MAX_DELAY
       - NARMA-2 and NARMA-10 (NMSE, lower is better)
       - delayed XOR of thresholded inputs (accuracy, chance = 0.5)
     each compared with a control that has NO reservoir: the same readout fed the last
     'nodes' raw inputs u[k], u[k-1], ... (linear delay line).
  5. Optional: --test other.csv   train on the main run, test on a different run (different
                                  input seed): the proper out-of-sample test.
               --echo other.csv   echo-state check: same input seed, different start state. The
                                  runs differ by a slowly drifting global offset of a few hundred
                                  counts, so the check removes that offset before comparing, and
                                  prints the raw figure too.
               --center           remove each run's own per-node mean (after washout) from the states before
                                  training and testing. Runs differ by a global additive offset of 100 to
                                  300 counts that does not hurt the squared-correlation memory measure but
                                  does hurt NMSE and accuracy when the node's signal is small (strong
                                  feedback). Uses the test run's own mean, so it is a diagnostic, not a
                                  causal method: a real system would need a reference reading per run.
               --repeat other.csv a repeat of the main run (same settings). Needs --test. Trains on the
                                  repeat instead, tests on the same test run, and prints both
                                  side by side: the reproducibility check.

Default evaluation without --test is a blocked split inside the single run
(first 60% of the post-washout steps train, last 40% test). That is a weaker test than
a separate run with a fresh input stream, because drift in the hardware over the run
cannot be told apart from the readout generalising.

Usage:
    python3 analyze_phase3.py run.csv [--test other.csv] [--echo other.csv]
                              [--repeat same_settings_run.csv] [--center] [--out DIR]
                              [--washout 50] [--max-delay 10]
"""

import argparse
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ADC_MAX = 16383
ALPHAS = np.logspace(-4, 4, 17)
TRAIN_FRAC = 0.6

# provisional pass criteria from the experiment log (fixed before seeing data)
CRIT_ESP = 0.1
CRIT_MC0 = 0.8
CRIT_MC1 = 0.3
CRIT_MC_TOTAL = 1.0


# --------------------------------------------------------------------------- loading
def load_run(path):
    params = {}
    footer = {}
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# PARAMS"):
                for tok in line.replace("# PARAMS", "").split():
                    if "=" in tok:
                        a, b = tok.split("=", 1)
                        params[a] = b
            elif line.startswith("# overruns"):
                for tok in line[1:].split():
                    if "=" in tok:
                        a, b = tok.split("=", 1)
                        footer[a] = b
    df = pd.read_csv(path, comment="#", names=["k", "i", "u", "pwm_in", "pwm_fb", "adc"], header=0)
    df = df.dropna().astype({"k": int, "i": int})
    df = df.sort_values(["k", "i"]).reset_index(drop=True)
    return df, params, footer


def to_matrix(df):
    X = df.pivot(index="k", columns="i", values="adc").values.astype(float)
    P = df.pivot(index="k", columns="i", values="pwm_in").values.astype(float)
    F = df.pivot(index="k", columns="i", values="pwm_fb").values.astype(float)
    u = df.groupby("k")["u"].first().values.astype(float)
    return X, P, F, u


def integrity(df, params, footer, name):
    print(f"--- Integrity: {name} ---")
    steps = int(params.get("steps", df["k"].max() + 1))
    nodes = int(params.get("nodes", df["i"].max() + 1))
    ok = True
    exp = steps * nodes
    print(f"rows {len(df)} (expected {exp}); steps {df['k'].nunique()}; nodes {df['i'].nunique()}")
    ok &= len(df) == exp
    ok &= bool((df.groupby("k").size() == nodes).all())
    ok &= bool((df.groupby("k")["u"].nunique() == 1).all())
    if params:
        print("params:", " ".join(f"{a}={b}" for a, b in params.items()))
    if footer:
        late = int(footer.get("max_slot_start_late_us", 0))
        ov = int(footer.get("overruns", 0))
        print(f"firmware timing: overruns={ov}, max slot-start lateness {late} us (slot idle time is ~16000 us)")
        ok &= (ov == 0) and (late < 5000)
    else:
        print("no timing footer found (run may have been cut off)")
        ok = False
    print("INTEGRITY:", "PASS" if ok else "FAIL")
    print()
    return ok


# --------------------------------------------------------------------------- readout
def standardize(Xtr, Xte_list):
    mu = Xtr.mean(0)
    sd = Xtr.std(0)
    sd[sd < 1e-9] = 1.0
    return (Xtr - mu) / sd, [(x - mu) / sd for x in Xte_list]


def ridge_fit(Xs, y, alpha):
    ybar = y.mean()
    A = Xs.T @ Xs + alpha * np.eye(Xs.shape[1])
    w = np.linalg.solve(A, Xs.T @ (y - ybar))
    return w, ybar


def cv_alpha(Xs, y, folds=5):
    n = len(y)
    edges = np.linspace(0, n, folds + 1).astype(int)
    best, best_err = ALPHAS[0], np.inf
    for a in ALPHAS:
        err = 0.0
        for f in range(folds):
            te = np.arange(edges[f], edges[f + 1])
            tr = np.setdiff1d(np.arange(n), te)
            w, yb = ridge_fit(Xs[tr], y[tr], a)
            err += np.mean((Xs[te] @ w + yb - y[te]) ** 2)
        if err < best_err:
            best, best_err = a, err
    return best


def fit_predict(Xtr, ytr, Xte):
    Xs, (Xts,) = standardize(Xtr, [Xte])
    a = cv_alpha(Xs, ytr)
    w, yb = ridge_fit(Xs, ytr, a)
    return Xts @ w + yb, a


def sq_corr(a, b):
    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1] ** 2)


def nmse(y, p):
    return float(np.mean((y - p) ** 2) / np.var(y))


# --------------------------------------------------------------------------- targets
def shifted(u, d):
    out = np.zeros_like(u)
    if d == 0:
        return u.copy()
    out[d:] = u[:-d]
    return out


def narma(s, order):
    """Return y with y[t+1] computed from inputs up to t (y[0] = 0)."""
    K = len(s)
    y = np.zeros(K)
    for t in range(K - 1):
        if order == 10:
            ysum = sum(y[t - j] for j in range(10) if t - j >= 0)
            s9 = s[t - 9] if t - 9 >= 0 else 0.0
            y[t + 1] = 0.3 * y[t] + 0.05 * y[t] * ysum + 1.5 * s9 * s[t] + 0.1
        else:  # NARMA-2
            y1 = y[t - 1] if t - 1 >= 0 else 0.0
            y[t + 1] = 0.4 * y[t] + 0.4 * y[t] * y1 + 0.6 * s[t] ** 3 + 0.1
    return y


def delay_line(u, n):
    cols = [shifted(u, j) for j in range(n)]
    return np.stack(cols, axis=1)


def task_set(u, washout):
    """Return dict name -> (target array over all steps, valid index array, kind)."""
    K = len(u)
    s = 0.5 * u
    bits = (u > 0.5).astype(int)
    tasks = {}
    valid_all = np.arange(washout, K - 1)
    tasks["NARMA-2"] = (narma(s, 2)[1:].tolist() + [np.nan], valid_all, "nmse")
    tasks["NARMA-10"] = (narma(s, 10)[1:].tolist() + [np.nan], valid_all, "nmse")
    for tau in (0, 1):
        b0 = np.roll(bits, tau)
        b1 = np.roll(bits, tau + 1)
        t = np.logical_xor(b0, b1).astype(float) * 2 - 1
        tasks[f"XOR tau={tau}"] = (t.tolist(), np.arange(washout, K), "acc")
    for k, v in list(tasks.items()):
        tasks[k] = (np.array(v[0], dtype=float), v[1], v[2])
    return tasks


def evaluate(X_tr, u_tr, X_te, u_te, washout, max_delay, control_n):
    """Train on X_tr/u_tr, test on X_te/u_te. Returns dict of results."""
    res = {"mc": [], "mc_ctrl": [], "tasks": {}}
    C_tr = delay_line(u_tr, control_n)
    C_te = delay_line(u_te, control_n)
    v_tr = np.arange(washout, len(u_tr))
    v_te = np.arange(washout, len(u_te))
    for d in range(max_delay + 1):
        ytr = shifted(u_tr, d)[v_tr]
        yte = shifted(u_te, d)[v_te]
        p, _ = fit_predict(X_tr[v_tr], ytr, X_te[v_te])
        res["mc"].append(sq_corr(yte, p))
        pc, _ = fit_predict(C_tr[v_tr], ytr, C_te[v_te])
        res["mc_ctrl"].append(sq_corr(yte, pc))
    T_tr = task_set(u_tr, washout)
    T_te = task_set(u_te, washout)
    for name in T_tr:
        ytr_all, vtr, kind = T_tr[name]
        yte_all, vte, _ = T_te[name]
        ytr, yte = ytr_all[vtr], yte_all[vte]
        out = {}
        for label, (Atr, Ate) in {"reservoir": (X_tr, X_te), "control": (C_tr, C_te)}.items():
            p, a = fit_predict(Atr[vtr], ytr, Ate[vte])
            if kind == "nmse":
                out[label] = nmse(yte, p)
            else:
                out[label] = float(np.mean(np.sign(p) == np.sign(yte)))
            if label == "reservoir":
                out["pred"] = p
                out["target"] = yte
        out["kind"] = kind
        res["tasks"][name] = out
    return res


# --------------------------------------------------------------------------- node behaviour
def lag_response(df, max_lag=15):
    """Linear lag model over slots: adc[t] ~ sum_l w_l * pwm_in[t-l]. Indicative only (node is nonlinear)."""
    x = df["adc"].values.astype(float)
    p = df["pwm_in"].values.astype(float)
    T = len(x)
    rows = np.arange(max_lag, T)
    A = np.stack([p[rows - l] for l in range(max_lag + 1)], axis=1)
    y = x[rows]
    n_tr = int(0.7 * len(rows))
    Atr, Ate = A[:n_tr], A[n_tr:]
    ytr, yte = y[:n_tr], y[n_tr:]
    Xs, (Xts,) = standardize(Atr, [Ate])
    w, yb = ridge_fit(Xs, ytr, 1e-3)
    pred = Xts @ w + yb
    r2 = 1 - np.sum((yte - pred) ** 2) / np.sum((yte - yte.mean()) ** 2)
    sd = Atr.std(0)
    sd[sd < 1e-9] = 1
    w_counts_per_pwm = w / sd
    return w_counts_per_pwm, float(r2)


def io_map(df):
    g = df.groupby("pwm_in")["adc"].agg(["mean", "std", "count"]).reset_index()
    return g


# --------------------------------------------------------------------------- plotting
def save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    print("Saved", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--test", help="separate run (different input seed) used as test set")
    ap.add_argument("--echo", help="run with same seed but different init, for the echo-state check")
    ap.add_argument("--repeat", help="repeat of the main run (same settings); needs --test")
    ap.add_argument("--center", action="store_true",
                    help="remove each run's per-node mean from the states (removes the run-to-run offset)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--washout", type=int, default=50)
    ap.add_argument("--max-delay", type=int, default=10)
    a = ap.parse_args()
    out = a.out or os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)

    df, params, footer = load_run(a.csv)
    ok = integrity(df, params, footer, os.path.basename(a.csv))
    if df["pwm_in"].std() == 0 or df["adc"].std() < 20:
        print("STOP: the input LED was never driven in this run "
              f"(bias={params.get('bias')}, amp={params.get('amp')}, input PWM constant at {df['pwm_in'].iloc[0]}, "
              f"readings stay near {df['adc'].mean():.0f}). There is nothing to analyse.")
        print("Re-run the reservoir with explicit bias= and amp= values for this channel "
              "(choose them from the sweep analysis), then analyse the new file.")
        sys.exit(2)
    X, P, F, u = to_matrix(df)
    K, N = X.shape
    w = a.washout
    X_raw = X.copy()
    if a.center:
        X = X - X[w:].mean(0)
        print("NOTE: --center is on; states have each run's per-node mean removed before training/testing.\n")

    # ---------------- state range ----------------
    v = X_raw[w:].ravel()
    pc = {q: np.percentile(v, q) for q in (1, 5, 25, 50, 75, 95, 99)}
    print("--- State range (post-washout readings) ---")
    print(f"min {v.min():.0f}  p1 {pc[1]:.0f}  p5 {pc[5]:.0f}  median {pc[50]:.0f}  p95 {pc[95]:.0f}  "
          f"p99 {pc[99]:.0f}  max {v.max():.0f}  (ADC full scale {ADC_MAX})")
    xlo, xhi = int(round(pc[5], -2)), int(round(pc[95], -2))
    print(f"Suggested feedback scaling for Phase 4: xlo={xlo} xhi={xhi} (5th/95th percentile, rounded)")
    print(f"Headroom: max reading is {ADC_MAX - v.max():.0f} counts below full scale; "
          f"fraction of readings above 14,500: {(v > 14500).mean() * 100:.2f}%")
    n_half = (K - w) // 2
    m1, m2 = X_raw[w:w + n_half].mean(), X_raw[-n_half:].mean()
    print(f"Stationarity: mean reading first half {m1:.0f}, second half {m2:.0f} (difference {m2 - m1:+.0f})")
    mask_sign = np.nan_to_num(np.array([np.sign(np.corrcoef(P[:, i], u)[0, 1]) for i in range(N)]))
    print("Mask recovered from the data (+1 = brighter for larger u):", " ".join(f"{int(s):+d}" for s in mask_sign))
    pin = df["pwm_in"]
    print(f"Input drive actually used: PWM {pin.min()} to {pin.max()}")
    print()

    # ---------------- node behaviour ----------------
    lagw, lag_r2 = lag_response(df)
    print("--- Lag response (linear approximation over 20 ms slots) ---")
    print("weight of drive l slots ago (counts per PWM unit):",
          " ".join(f"{x:.1f}" for x in lagw[:8]))
    print(f"linear lag model R2 on held-out slots: {lag_r2:.3f} (lower = more nonlinear)")
    print()

    # ---------------- readout tasks ----------------
    control_n = N
    if a.test:
        df2, p2, f2 = load_run(a.test)
        integrity(df2, p2, f2, os.path.basename(a.test))
        X2, _, _, u2 = to_matrix(df2)
        if a.center:
            X2 = X2 - X2[w:].mean(0)
        for key in ("nodes", "mask_seed", "in", "bias", "amp", "theta_ms"):
            if params.get(key) != p2.get(key):
                print(f"WARNING: parameter '{key}' differs between runs: {params.get(key)} vs {p2.get(key)}")
        if params.get("seed") == p2.get("seed"):
            print("WARNING: both runs use the same input seed, so this is not a fresh test stream")
        res = evaluate(X, u, X2, u2, w, a.max_delay, control_n)
        split = "train on main run, test on the second run (different input stream)"
    else:
        cut = w + int(TRAIN_FRAC * (K - w))
        res = None
        # blocked split inside one run: build train/test arrays by slicing, keeping washout at the start
        Xtr, utr = X[:cut], u[:cut]
        Xte, ute = X[cut - 0:], u[cut - 0:]
        # the test block needs its own lag history; give it a short pad from the end of train
        pad = max(control_n, a.max_delay) + 10
        Xte_p, ute_p = X[cut - pad:], u[cut - pad:]
        res = evaluate(Xtr, utr, Xte_p, ute_p, pad, a.max_delay, control_n)
        split = f"blocked split inside one run: train steps {w}-{cut - 1}, test steps {cut}-{K - 1}"
    print("--- Readout evaluation ---")
    print("split:", split)
    mc = np.array(res["mc"])
    mcc = np.array(res["mc_ctrl"])
    print("\nMemory capacity per delay d (squared correlation of readout with u[k-d]):")
    print(" d  reservoir  control(linear delay line)")
    for d, (x, y) in enumerate(zip(mc, mcc)):
        print(f"{d:2d}  {x:9.3f}  {y:9.3f}")
    mc_total = mc[1:].sum()
    print(f"MC_0 = {mc[0]:.3f}   MC_1 = {mc[1]:.3f}   total MC (d=1..{a.max_delay}) = {mc_total:.3f}   "
          f"(control total {mcc[1:].sum():.3f})")
    print("\nTasks (reservoir vs control with the same number of features):")
    for name, r in res["tasks"].items():
        if r["kind"] == "nmse":
            print(f"{name:10s} NMSE  reservoir {r['reservoir']:.3f}   control {r['control']:.3f}   (lower is better, 1.0 = predicting the mean)")
        else:
            print(f"{name:10s} accuracy  reservoir {r['reservoir']:.3f}   control {r['control']:.3f}   (chance 0.5)")
    print()

    # ---------------- echo-state check ----------------
    esp = None
    esp_raw = None
    if a.echo:
        df3, p3, f3 = load_run(a.echo)
        X3, _, _, u3 = to_matrix(df3)
        same_u = np.allclose(u, u3, atol=1e-6)
        print("--- Echo-state check ---")
        print(f"init in main run: {params.get('init')}   init in echo run: {p3.get('init')}   identical inputs: {same_u}")
        if same_u and X3.shape == X_raw.shape:
            d_all = X_raw - X3
            diff_raw = np.abs(d_all).mean(1)
            offset = float(d_all[w:].mean())
            diff_adj = np.abs(d_all - offset).mean(1)
            sd = X_raw[w:].std()
            esp_raw = float(diff_raw[w:].mean() / sd)
            esp = float(diff_adj[w:].mean() / sd)
            node_off = d_all[w:].mean(0)
            print(f"global offset between the two runs: {offset:+.0f} counts "
                  f"(per-node offsets range {node_off.min():+.0f} to {node_off.max():+.0f}, "
                  f"so it is the same for every node)")
            print(f"normalised difference after washout: raw {esp_raw:.4f}, offset removed {esp:.4f} "
                  f"(criterion < {CRIT_ESP} on the offset-removed value)")
            steps_show = [s_ for s_ in (0, 1, 2, 3, 5, 10, 20, 50) if s_ < len(diff_adj)]
            print("mean |difference| with offset removed (counts) at steps",
                  ",".join(str(s_) for s_ in steps_show), ":",
                  " ".join(f"{diff_adj[s_]:.0f}" for s_ in steps_show))
            fig, ax = plt.subplots(figsize=(8, 4.2))
            ax.semilogy(np.arange(len(diff_raw)), np.maximum(diff_raw, 0.1), lw=0.9, label="raw")
            ax.semilogy(np.arange(len(diff_adj)), np.maximum(diff_adj, 0.1), lw=0.9, label="global offset removed")
            ax.axvline(w, color="grey", ls=":")
            ax.set_xlabel("Input step k")
            ax.set_ylabel("mean |state difference| (counts)")
            ax.set_title("Echo-state check: two different start states, same inputs")
            ax.legend()
            ax.grid(alpha=0.3, which="both")
            save(fig, os.path.join(out, "echo_state_check.png"))
        else:
            print("Cannot compare: inputs differ or runs have different shapes.")
        print()

    # ---------------- reproducibility (repeat run) ----------------
    repro_ok = None
    if a.repeat:
        if not a.test:
            print("--repeat needs --test (the shared test run).")
        else:
            df4, p4, f4 = load_run(a.repeat)
            X4, _, _, u4 = to_matrix(df4)
            if a.center:
                X4 = X4 - X4[w:].mean(0)
            same = np.allclose(u, u4, atol=1e-6)
            res4 = evaluate(X4, u4, X2, u2, w, a.max_delay, control_n)
            mc4 = np.array(res4["mc"])
            print("--- Reproducibility: train on the main run vs on the repeat, same test run ---")
            print(f"repeat has identical inputs to the main run: {same}; "
                  f"settings: seed {p4.get('seed')} init {p4.get('init')} in {p4.get('in')} fb {p4.get('fb')}")
            print(f"{'metric':22s}{'main run':>10s}{'repeat':>10s}{'difference':>12s}")
            def line(name, x, y):
                print(f"{name:22s}{x:10.3f}{y:10.3f}{y - x:+12.3f}")
            line("MC_0", mc[0], mc4[0])
            line("MC_1", mc[1], mc4[1])
            line("total MC (d=1..max)", mc[1:].sum(), mc4[1:].sum())
            for name in res["tasks"]:
                line(name + (" NMSE" if res["tasks"][name]["kind"] == "nmse" else " acc"),
                     res["tasks"][name]["reservoir"], res4["tasks"][name]["reservoir"])
            m1, m2 = mc[1:].sum(), mc4[1:].sum()
            rel = abs(m1 - m2) / max(m1, m2, 1e-9)
            repro_ok = rel < 0.10
            print(f"total MC differs by {100 * rel:.1f}% (criterion < 10%)")
            print()

    # ---------------- verdict ----------------
    print("--- Provisional criteria (from the experiment log) ---")
    rows = [
        ("MC_0 >= %.1f" % CRIT_MC0, mc[0] >= CRIT_MC0, f"{mc[0]:.3f}"),
        ("MC_1 >= %.1f" % CRIT_MC1, mc[1] >= CRIT_MC1, f"{mc[1]:.3f}"),
        ("total MC >= %.1f" % CRIT_MC_TOTAL, mc_total >= CRIT_MC_TOTAL, f"{mc_total:.3f}"),
    ]
    for name in ("NARMA-2", "XOR tau=0", "XOR tau=1"):
        r = res["tasks"][name]
        better = r["reservoir"] < r["control"] if r["kind"] == "nmse" else r["reservoir"] > r["control"]
        rows.append((f"{name} beats linear control", better, f"{r['reservoir']:.3f} vs {r['control']:.3f}"))
    if esp is not None:
        rows.append((f"echo-state difference < {CRIT_ESP} (offset removed)", esp < CRIT_ESP,
                     f"{esp:.4f} (raw {esp_raw:.4f})"))
    else:
        rows.append(("echo-state difference (needs --echo run)", None, "not tested"))
    if repro_ok is not None:
        rows.append(("reproducibility: total MC within 10% of the repeat", repro_ok, ""))
    else:
        rows.append(("reproducibility (needs --repeat with --test)", None, "not tested"))
    for name, flag, detail in rows:
        tag = "n/a " if flag is None else ("PASS" if flag else "FAIL")
        print(f"{tag}  {name}  {detail}")
    print()

    # ---------------- plots ----------------
    # 1. example traces
    fig, ax = plt.subplots(figsize=(12, 4.5))
    k0, k1 = w, w + 6
    t = np.arange((k1 - k0) * N)
    ax.plot(t * 20, X[k0:k1].ravel(), lw=1.2, color="#378ADD", label="A0 reading")
    ax.set_xlabel("Time (ms), 20 ms per virtual node")
    ax.set_ylabel("A0 reading (counts)")
    ax2 = ax.twinx()
    ax2.step(t * 20, P[k0:k1].ravel(), where="post", color="0.55", lw=1)
    ax2.set_ylabel("Input LED PWM", color="0.45")
    for j in range(k1 - k0 + 1):
        ax.axvline(j * N * 20, color="0.8", lw=0.8)
    ax.set_title(f"Six consecutive input steps (vertical lines = input sample boundaries)")
    ax.grid(alpha=0.2)
    save(fig, os.path.join(out, "state_traces_example.png"))

    # 2. input-output map
    g = io_map(df)
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.errorbar(g["pwm_in"], g["mean"], yerr=g["std"], fmt="o", ms=3, lw=0.8, capsize=2, color="#378ADD")
    ax.set_xlabel("Input LED PWM in the slot")
    ax.set_ylabel("A0 reading at end of slot (mean +/- std)")
    ax.set_title("Instantaneous input-output map; the spread is the effect of earlier slots")
    ax.grid(alpha=0.3)
    save(fig, os.path.join(out, "input_output_map.png"))

    # 3. state distribution and per-node
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    axs[0].hist(v, bins=80, color="#378ADD", alpha=0.85)
    axs[0].axvline(pc[5], color="k", ls="--", lw=1)
    axs[0].axvline(pc[95], color="k", ls="--", lw=1)
    axs[0].text(pc[5], axs[0].get_ylim()[1] * 0.92, " p5", fontsize=9)
    axs[0].text(pc[95], axs[0].get_ylim()[1] * 0.92, " p95", fontsize=9)
    axs[0].set_xlabel("A0 reading (counts)")
    axs[0].set_ylabel("count")
    axs[0].set_title("State distribution (all nodes, after washout)")
    nm = X_raw[w:].mean(0)
    ns = X_raw[w:].std(0)
    colors = ["#D85A30" if s < 0 else "#378ADD" for s in mask_sign]
    axs[1].bar(np.arange(N), nm, yerr=ns, color=colors, capsize=2)
    axs[1].set_xlabel("Virtual node i")
    axs[1].set_ylabel("mean +/- std of reading")
    axs[1].set_title("Per node (blue = mask +1, orange = mask -1)")
    axs[1].set_ylim(max(0, nm.min() - 2 * ns.max()), nm.max() + 2 * ns.max())
    save(fig, os.path.join(out, "state_distribution_and_nodes.png"))

    # 4. lag response
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar(np.arange(len(lagw)) * 20, lagw, width=14, color="#3B8C2A")
    ax.set_xlabel("Drive applied this many ms earlier")
    ax.set_ylabel("Effect on reading (counts per PWM unit)")
    ax.set_title(f"Linear lag response of the node (held-out R2 = {lag_r2:.2f})")
    ax.grid(alpha=0.3)
    save(fig, os.path.join(out, "lag_response.png"))

    # 5. memory capacity
    fig, ax = plt.subplots(figsize=(8, 4.5))
    xs = np.arange(len(mc))
    ax.bar(xs - 0.2, mc, width=0.4, label="reservoir", color="#378ADD")
    ax.bar(xs + 0.2, mcc, width=0.4, label="control (linear delay line)", color="0.7")
    ax.set_xlabel("Delay d (input steps)")
    ax.set_ylabel("Squared correlation with u[k-d]")
    ax.set_title("Memory capacity per delay")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    save(fig, os.path.join(out, "memory_capacity.png"))

    # 6. tasks vs control
    names = list(res["tasks"].keys())
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    nm_names = [n for n in names if res["tasks"][n]["kind"] == "nmse"]
    ac_names = [n for n in names if res["tasks"][n]["kind"] == "acc"]
    for ax, group, ylab, title in [(axs[0], nm_names, "NMSE (lower is better)", "NARMA tasks"),
                                  (axs[1], ac_names, "Accuracy (chance 0.5)", "Delayed XOR")]:
        x = np.arange(len(group))
        ax.bar(x - 0.2, [res["tasks"][n]["reservoir"] for n in group], width=0.4, label="reservoir", color="#378ADD")
        ax.bar(x + 0.2, [res["tasks"][n]["control"] for n in group], width=0.4, label="control", color="0.7")
        ax.set_xticks(x)
        ax.set_xticklabels(group)
        ax.set_ylabel(ylab)
        ax.set_title(title)
        ax.legend()
        ax.grid(alpha=0.3, axis="y")
    axs[1].axhline(0.5, color="r", ls=":", lw=1)
    save(fig, os.path.join(out, "tasks_vs_control.png"))

    # 7. NARMA prediction example
    fig, axs = plt.subplots(2, 1, figsize=(11, 6))
    for ax, name in zip(axs, ["NARMA-2", "NARMA-10"]):
        r = res["tasks"][name]
        n_show = min(120, len(r["target"]))
        ax.plot(r["target"][:n_show], label="target", color="k", lw=1.2)
        ax.plot(r["pred"][:n_show], label="reservoir readout", color="#378ADD", lw=1.2)
        ax.set_title(f"{name}, first {n_show} test steps (NMSE {r['reservoir']:.3f})")
        ax.legend(loc="upper right")
        ax.grid(alpha=0.3)
    axs[1].set_xlabel("Test step")
    save(fig, os.path.join(out, "narma_prediction_example.png"))


if __name__ == "__main__":
    main()