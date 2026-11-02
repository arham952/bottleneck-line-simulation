"""
PROJECT 2: Production Line Simulation with a Bottleneck
Story: A factory line has 4 stations in a row. Each item must pass through all of them.
Processing times are random. We build a computer model of the line (discrete-event simulation)
and use it to find the bottleneck and test improvement ideas BEFORE spending real money.

Run from the repo root:  python src/line_simulation.py            (shows charts and saves them)
                         python src/line_simulation.py --no-show   (just saves charts)
"""

# ---------------------------------------------------------------
# STEP 0: IMPORT THE TOOLS WE NEED
# ---------------------------------------------------------------
import sys                            # sys = lets us read command-line options like --no-show
import random                         # random = random numbers for processing times
from pathlib import Path              # Path = safe way to build folder/file paths
import numpy as np                    # numpy = math on lists of numbers
import pandas as pd                   # pandas = tables
import matplotlib.pyplot as plt       # matplotlib = drawing charts
import simpy                          # simpy = discrete-event simulation library (the engine of this project)
from scipy import stats               # scipy.stats = used for confidence intervals

# ---------------------------------------------------------------
# FOLDERS: where we save pictures and tables (relative to the repo root)
# ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent    # repo root = the folder above "src"
FIG_DIR = ROOT / "figures"                        # charts are saved here
RES_DIR = ROOT / "results"                        # result tables are saved here
for d in (FIG_DIR, RES_DIR):                      # make sure the folders exist
    d.mkdir(exist_ok=True)

SHOW = "--no-show" not in sys.argv                # True unless the user passes --no-show

def finish_plot():
    """Show the chart on screen (if allowed) and close it to free memory."""
    if SHOW:
        plt.show()                                # pop up the chart window
    plt.close()                                   # close the figure

# ---------------------------------------------------------------
# STEP 1: SETTINGS (the "rules of the game")
# ---------------------------------------------------------------
SIM_TIME = 8 * 60 * 20            # simulate 20 working days of 8 hours, measured in minutes = 9,600 minutes
N_REPS = 30                       # replications: we run the whole simulation 30 times (different random numbers)
BASE_MEANS = [1.0, 1.2, 1.6, 1.1] # average minutes per item at stations 1..4 (station 3 is the slowest = bottleneck)
BASE_BUFFER = 5                   # how many items can wait in the buffer in front of each station
STATIONS = ["Station 1", "Station 2", "Station 3", "Station 4"]   # names for the charts

# ---------------------------------------------------------------
# STEP 2: HOW LONG DOES ONE ITEM TAKE? (random processing times)
# ---------------------------------------------------------------
def draw_time(mean, kind):
    """Return one random processing time with the given average.
       kind = 'exp'  -> very random (exponential, coefficient of variation = 1)  [our baseline]
       kind = 'low'  -> less random (gamma, coefficient of variation = 0.5)
       kind = 'det'  -> not random at all (always exactly the average)"""
    if kind == "exp":
        return random.expovariate(1 / mean)       # exponential: average = mean, lots of spread
    if kind == "low":
        return random.gammavariate(4, mean / 4)   # gamma(shape 4): average = mean, less spread
    return mean                                   # deterministic: always the same

# ---------------------------------------------------------------
# STEP 3: THE MODEL - ONE FULL RUN OF THE FACTORY LINE
# ---------------------------------------------------------------
def run_once(means, buffer, kind, seed):
    """Simulate the line for SIM_TIME minutes once. Returns throughput, utilization, WIP, cycle time."""
    random.seed(seed)                             # fix the random numbers so results can be repeated
    env = simpy.Environment()                     # the simulated "world" with its own clock

    # Waiting areas: queues[i] is the buffer IN FRONT of station i (limited size).
    # The extra last store holds finished items (unlimited size).
    queues = [simpy.Store(env, capacity=buffer) for _ in means] + [simpy.Store(env)]

    busy = [0.0] * len(means)                     # total minutes each station spent working
    state = {"wip": 0}                            # how many items are currently inside the line
    wip_samples = []                              # WIP measured every minute (to average later)
    cycle_times = []                              # time each finished item spent in the line

    def feeder():
        """Raw material is always available: keep pushing new items into the first buffer."""
        while True:
            job = {}                              # one item, represented by a small dictionary
            yield queues[0].put(job)              # wait here if the first buffer is full
            job["t_in"] = env.now                 # write down when the item entered the line
            state["wip"] += 1                     # one more item inside the line

    def station(i):
        """Station i repeats forever: take item -> work on it -> pass it on."""
        while True:
            job = yield queues[i].get()           # take an item from the buffer (wait if empty = STARVED)
            t = draw_time(means[i], kind)         # pick a random processing time
            yield env.timeout(t)                  # the station works for t minutes (clock moves forward)
            busy[i] += t                          # record the working time (used for utilization)
            yield queues[i + 1].put(job)          # pass the item on (wait if next buffer is full = BLOCKED)
            if i == len(means) - 1:               # if this was the LAST station, the item is finished
                state["wip"] -= 1                 # one less item inside the line
                cycle_times.append(env.now - job["t_in"])   # record how long it spent in the line

    def monitor():
        """Every minute, write down how many items are inside the line (for average WIP)."""
        while True:
            wip_samples.append(state["wip"])
            yield env.timeout(1)

    env.process(feeder())                         # start the feeder
    for i in range(len(means)):
        env.process(station(i))                   # start each station
    env.process(monitor())                        # start the WIP monitor
    env.run(until=SIM_TIME)                       # run the simulated clock until SIM_TIME

    done = len(cycle_times)                       # number of finished items
    return {
        "throughput": done / SIM_TIME * 60,       # finished items per HOUR
        "util": [b / SIM_TIME for b in busy],     # fraction of time each station was busy
        "wip": float(np.mean(wip_samples)),       # average number of items inside the line
        "cycle_time": float(np.mean(cycle_times)),# average minutes an item spent in the line
    }

# ---------------------------------------------------------------
# STEP 4: REPEAT MANY TIMES AND REPORT MEAN +/- CONFIDENCE INTERVAL
# ---------------------------------------------------------------
def replicate(means, buffer=BASE_BUFFER, kind="exp", n=N_REPS):
    """Run the simulation n times (seeds 0..n-1) and summarize."""
    runs = [run_once(means, buffer, kind, seed) for seed in range(n)]   # n independent runs
    th = np.array([r["throughput"] for r in runs])                      # throughput of each run
    half = stats.t.ppf(0.975, n - 1) * th.std(ddof=1) / np.sqrt(n)      # 95% confidence half-width
    return {
        "throughput": th.mean(),                                        # average throughput
        "ci": half,                                                     # +/- range
        "util": np.mean([r["util"] for r in runs], axis=0),             # average utilization per station
        "wip": np.mean([r["wip"] for r in runs]),                       # average WIP
        "cycle_time": np.mean([r["cycle_time"] for r in runs]),         # average cycle time
    }

# ---------------------------------------------------------------
# STEP 5: BASELINE - HOW DOES THE LINE PERFORM TODAY? WHERE IS THE BOTTLENECK?
# ---------------------------------------------------------------
base = replicate(BASE_MEANS)                                  # run the baseline scenario
ceiling = 60 / max(BASE_MEANS)                                # theoretical maximum = 60 min / slowest station's time
bottleneck = int(np.argmax(base["util"]))                     # station with the highest utilization

print("=== BASELINE ===")
print(f"Theoretical ceiling (set by slowest station): {ceiling:.1f} items/hour")
print(f"Simulated throughput: {base['throughput']:.2f} +/- {base['ci']:.2f} items/hour (95% CI, {N_REPS} reps)")
for name, u in zip(STATIONS, base["util"]):
    print(f"  {name}: utilization {u:.0%}")
print(f"Bottleneck = {STATIONS[bottleneck]}")

# Picture 1: utilization of each station
plt.figure(figsize=(7, 4))
colors = ["tab:red" if i == bottleneck else "tab:blue" for i in range(len(STATIONS))]  # bottleneck in red
plt.bar(STATIONS, base["util"] * 100, color=colors)           # bars = utilization in percent
plt.ylabel("Utilization (%)")
plt.title("Station utilization (the busiest station is the bottleneck)")
plt.ylim(0, 100)
plt.tight_layout()
plt.savefig(FIG_DIR / "1_utilization.png", dpi=150)
finish_plot()

# ---------------------------------------------------------------
# STEP 6: LITTLE'S LAW CHECK (WIP = throughput x cycle time)
# ---------------------------------------------------------------
lam = base["throughput"] / 60                                 # throughput in items per MINUTE
little = lam * base["cycle_time"]                             # predicted WIP = lambda x cycle time
print("\n=== LITTLE'S LAW CHECK ===")
print(f"Measured average WIP:            {base['wip']:.2f} items")
print(f"Throughput x cycle time:         {little:.2f} items")
print(f"Average cycle time:              {base['cycle_time']:.1f} minutes")

# ---------------------------------------------------------------
# STEP 7: EXPERIMENT A - DOES MORE BUFFER SPACE HELP?
# ---------------------------------------------------------------
buffer_sizes = [1, 2, 5, 10, 20]                              # buffer sizes to test
buf_rows = []                                                 # will collect results
for b in buffer_sizes:
    r = replicate(BASE_MEANS, buffer=b)                       # run 30 replications for this buffer size
    buf_rows.append({"buffer": b, "throughput": r["throughput"], "ci": r["ci"], "wip": r["wip"],
                     "cycle_time": r["cycle_time"]})
buf_df = pd.DataFrame(buf_rows)                               # make a table
buf_df.round(3).to_csv(RES_DIR / "experiment_buffer.csv", index=False)   # save it
print("\n=== EXPERIMENT A: BUFFER SIZE ===")
print(buf_df.round(2).to_string(index=False))

# Picture 2: throughput vs buffer size, with the theoretical ceiling
plt.figure(figsize=(7, 4))
plt.errorbar(buf_df["buffer"], buf_df["throughput"], yerr=buf_df["ci"], marker="o", capsize=4)  # points with CI bars
plt.axhline(ceiling, color="red", linestyle="--", label=f"Ceiling ({ceiling:.1f}/h)")           # theoretical limit
plt.xlabel("Buffer size between stations")
plt.ylabel("Throughput (items/hour)")
plt.title("More buffer helps, but only up to the bottleneck ceiling")
plt.legend()
plt.tight_layout()
plt.savefig(FIG_DIR / "2_buffer_vs_throughput.png", dpi=150)
finish_plot()

# ---------------------------------------------------------------
# STEP 8: EXPERIMENT B - WHICH IMPROVEMENT IS WORTH THE MONEY?
# ---------------------------------------------------------------
scenarios = {                                                 # name -> (station means, variability kind)
    "Baseline": (BASE_MEANS, "exp"),
    "Speed up Station 1\n(not bottleneck)": ([0.8, 1.2, 1.6, 1.1], "exp"),     # 1.0 -> 0.8 min
    "Speed up Station 3\n(bottleneck) by 10%": ([1.0, 1.2, 1.44, 1.1], "exp"), # 1.6 -> 1.44 min
    "Less variability\n(same averages)": (BASE_MEANS, "low"),
    "No variability\n(same averages)": (BASE_MEANS, "det"),
}
sc_rows = []
for name, (means, kind) in scenarios.items():
    r = replicate(means, kind=kind)                           # 30 replications of each scenario
    sc_rows.append({"scenario": name.replace("\n", " "), "throughput": r["throughput"], "ci": r["ci"]})
sc_df = pd.DataFrame(sc_rows)
sc_df["change_vs_baseline_%"] = (sc_df["throughput"] / sc_df.loc[0, "throughput"] - 1) * 100   # percent change
sc_df.round(3).to_csv(RES_DIR / "experiment_scenarios.csv", index=False)
print("\n=== EXPERIMENT B: IMPROVEMENT SCENARIOS ===")
print(sc_df.round(2).to_string(index=False))

# Picture 3: compare scenarios
plt.figure(figsize=(10, 4.5))
labels = [n for n in scenarios]                               # names (with line breaks for neat labels)
bar_colors = ["gray"] + ["tab:orange", "tab:green", "tab:blue", "tab:blue"]
plt.bar(labels, sc_df["throughput"], yerr=sc_df["ci"], capsize=4, color=bar_colors)
plt.axhline(ceiling, color="red", linestyle="--", label=f"Baseline ceiling ({ceiling:.1f}/h)")
plt.ylim(30, 40)                                              # zoom in so the differences are visible
plt.ylabel("Throughput (items/hour)")
plt.title("What actually improves throughput? (y-axis starts at 30)")
plt.legend()
plt.xticks(fontsize=8)
plt.tight_layout()
plt.savefig(FIG_DIR / "3_scenario_comparison.png", dpi=150)
finish_plot()

# ---------------------------------------------------------------
# STEP 9: SAVE A SUMMARY AND PRINT THE CONCLUSION
# ---------------------------------------------------------------
pd.DataFrame({"station": STATIONS, "utilization": base["util"].round(3)}).to_csv(
    RES_DIR / "baseline_utilization.csv", index=False)        # save utilization table

gain_bn = sc_df.loc[2, "change_vs_baseline_%"]                # % gain from speeding up the bottleneck
gain_nb = sc_df.loc[1, "change_vs_baseline_%"]                # % gain from speeding up a non-bottleneck
gain_var = sc_df.loc[4, "change_vs_baseline_%"]               # % gain from removing variability
print("\n=== CONCLUSION ===")
print(f"Bottleneck: {STATIONS[bottleneck]} ({base['util'][bottleneck]:.0%} utilized). Ceiling {ceiling:.1f} items/hour.")
print(f"Speeding up a non-bottleneck station: {gain_nb:+.1f}% throughput (essentially nothing).")
print(f"Speeding up the bottleneck by 10%:    {gain_bn:+.1f}% throughput.")
print(f"Removing variability (same averages): {gain_var:+.1f}% throughput.")
