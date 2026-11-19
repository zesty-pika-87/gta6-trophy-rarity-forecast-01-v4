"""GTA VI trophy rarity forecast (make.fun gta6-trophy-rarity-forecast-01).

For each trophy, forecast the share of Exophase-tracked players who will
have earned it at the case's target time, as five quantiles (5%, 25%, 50%,
75%, 95%). Python 3.13, standard library only, deterministic: no network,
no wall clock, no randomness — the forecast depends only on the case file.

A trophy's earn rate is a ratio, rate = E / N, with E the tracked players
who earned it and N the players tracked. Exophase refreshes both counts on
its own schedule, hours apart, so the history is a staircase: long flat
stretches, then a jump. Two facts shape the whole design:

1. Only *distinct* states carry information. Consecutive reads of the same
   page (same N, same rates) are one observation, not many. Fitting a trend
   through the flat stretches as if they were many points would overweight
   the stale state.
2. Launch night is a different regime. The first refresh jumped N from 638
   to 3,868 and cut almost every rate about fivefold: a tiny early-adopter
   sample, then the launch crowd. A line fitted through that step treats the
   step as a trend and keeps pushing rates down forever (on the practice
   backtest it scores far worse than persistence). So when the first
   distinct state is dwarfed by the next one (N more than tripled), it is
   dropped before any fit.

Per trophy the median is a damped power law in log-time, anchored on the
last rate:

    median = last_rate * exp(b_adj * (x_target - x_last))

fitted by weighted least squares on the distinct states of the last 48
hours (all of them when there are fewer), weighted by recency with a
24-hour half-life: the regime drifts as the launch crowd matures, so a
refresh from this morning constrains the slope more than one from
yesterday. The slope b is the trophy's own maturity speed: early story
trophies have b > 0, their earners catch up faster than new players join;
grind trophies (platinum, 250 stunts, 100% completion) have b < 0,
dilution wins. Two guards keep the extrapolation honest:

- damping: b is multiplied by 0.6. A slope fitted over hours of noisy
  refresh steps overstates how far a rate travels over days; the adoption
  curve saturates, it does not run on a straight line in log-time.
- anchoring: the median is the last rate moved by the damped slope over
  the log-time gap since the last read, not the fit's intercept at release
  extrapolated to the target. Damping now tempers the trend *from* the last
  rate, and damp -> 0 is persistence, as it should be. The intercept form
  broke the first time a fit went live: in backtest B4 the mean slope was
  -0.777, and damping from the release intercept moved fallers up and
  risers down (Beat Cal, slope +1.34, was forecast 10x too low).
  Anchoring fixed B4 from 0.695 to 0.239 with the other cases unchanged.
  No shrinkage is applied (SHRINK 0): the slope heterogeneity is real —
  risers and grind trophies are different populations, and their mean
  mixes the two.

With fewer than three distinct states there is no slope to fit and the
median is the last rate (persistence). The trophy at 100% (every tracked
player has it at every read) is forecast at 100% flat: its logit is
unbounded and any spread below 100 pays heavily if the rate stays 100.

The spread is honest about a 4.5-day extrapolation from ~2 days of
staircase data. Per trophy, in logit units:

    s^2 = (0.5 * sqrt(days))^2 + s_refresh^2 + resid^2

the house random-walk term, a refresh-surprise term (one discrete refresh
can move a rate by ~a logit; 1.0 when no trend could be fitted at all, 0.7
once a fit absorbs part of the refresh dynamics), and the trophy's own
fit residual (0 when there is no fit). Quantiles are the median shifted
by z_tau * s on the logit scale, clamped to [0.01, 99.99] and rounded to 4
decimals, non-decreasing.

Any failure of the fit for a trophy falls back to persistence with the
same spread, so the program always writes a valid forecast.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader runs with -P, which leaves /bot off sys.path: add it back first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Slope damping (see module docstring). SHRINK is 0: the slope is kept as
# fitted, not pulled toward the cross-trophy mean — risers and grind
# trophies are genuinely different populations, and their mean mixes them.
DAMP = 0.6
SHRINK = 0.0
# Spread: refresh-surprise term, in logit units. Wider when no trend could
# be fitted at all (a flat history leaves the next refresh wholly
# unmodelled); narrower once a fit absorbs part of the refresh dynamics.
REFRESH_SIGMA_FIT = 0.7
REFRESH_SIGMA_NOFIT = 1.0
# Random-walk spread per square-root day, as in the house baseline.
RW_SIGMA = 0.5
# Median shift (logit): refreshes dilute rates one-sidedly and the damped
# trend under-predicts the decline, so the median is biased high; shift it
# down 0.3 logit (~x0.74 in rate). Asymmetric spread: forecast errors
# around the median are downside-heavy (dilution), so the lower quantiles
# get 1.3x spread and the upper quantiles 0.35x.
MEDIAN_SHIFT = -0.3
SPREAD_DOWN = 1.3
SPREAD_UP = 0.35
# Only states from the last 48 hours before the checkpoint enter the fit.
FIT_WINDOW_HOURS = 48.0
# Drop the leading distinct state when the next one tracks >3x the players.
REGIME_JUMP = 3.0
# Sanity guard on the median's total move from the last rate.
MAX_MOVE = 10.0
MIN_MOVE = 0.1


def clamp(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp(pct)
    return math.log(p / (100.0 - p))


def inv_logit(lg):
    return 100.0 / (1.0 + math.exp(-lg))


def round4(x):
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value == int(value) else value


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def wls(xs, ys, ws=None):
    """Weighted least squares y = a + b*x. Returns (a, b, residual_std).

    Weights default to 1. The caller passes recency weights: the regime
    drifts (launch-night players mature, the crowd changes), so a refresh
    from yesterday constrains the slope less than one from this morning.
    """
    n = len(xs)
    if ws is None:
        ws = [1.0] * n
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    den = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if den == 0:
        return my, 0.0, 0.0
    b = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / den
    a = my - b * mx
    resid = 0.0
    if n > 2:
        resid = math.sqrt(
            sum(w * (y - (a + b * x)) ** 2 for w, x, y in zip(ws, xs, ys)) / sw
        )
    return a, b, resid


def collapse_states(history):
    """Collapse consecutive identical reads (same N and rates) to one state."""
    states = []
    last_key = None
    for h in history:
        key = (h["players_tracked"], tuple(h["rates"]))
        if key != last_key:
            states.append(h)
            last_key = key
    return states


def forecast_case(case, name):
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError(f"{name} has no history")
    trophies = case["trophies"]
    horizon_days = case["horizon_minutes"] / 1440.0
    target = parse_time(case["target"])
    checkpoint = parse_time(case["at"])

    # Time origin: the start of release day, from the case's game record.
    release = case.get("game", {}).get("release_date", "2026-11-19")
    origin = datetime.fromisoformat(release + "T00:00:00+00:00").astimezone(timezone.utc)

    def x_of(dt):
        hours = (dt - origin).total_seconds() / 3600.0
        return math.log(1 + max(hours, 0.0))

    x_target = x_of(target)
    states = collapse_states(case["history"])

    # Drop the launch-night regime: a leading state dwarfed by the next one.
    while len(states) >= 2 and states[1]["players_tracked"] > REGIME_JUMP * max(states[0]["players_tracked"], 1):
        states = states[1:]

    # Fit window: distinct states from the last 48 h before the checkpoint.
    window_start = checkpoint.timestamp() - FIT_WINDOW_HOURS * 3600
    fit_states = [s for s in states if parse_time(s["at"]).timestamp() >= window_start]
    if len(fit_states) < 3:
        fit_states = states

    xs = [x_of(parse_time(s["at"])) for s in fit_states]
    # Recency weights: half-life 24 h back from the checkpoint.
    ws = [
        0.5 ** ((checkpoint - parse_time(s["at"])).total_seconds() / (24 * 3600))
        for s in fit_states
    ]
    last = case["history"][-1]
    rw = RW_SIGMA * math.sqrt(horizon_days)

    # First pass: per-trophy slopes, to get the mean slope for shrinkage.
    fits = {}
    slopes = []
    for i, trophy in enumerate(trophies):
        ys = []
        ok = True
        for s in fit_states:
            r = s["rates"][i]
            if r is None:
                ok = False
                break
            ys.append(math.log(max(r, 0.01)))
        if not ok or len(xs) < 3:
            continue
        a, b, resid = wls(xs, ys, ws)
        fits[trophy["id"]] = (a, b, resid)
        slopes.append(b)
    mean_slope = sum(slopes) / len(slopes) if slopes else 0.0

    rows = []
    for i, trophy in enumerate(trophies):
        tid = trophy["id"]
        last_rate = last["rates"][i]
        if last_rate is None:
            last_rate = 0.01
        # The trophy every tracked player holds: forecast 100% flat.
        if last_rate >= 99.99:
            rows.append({"id": tid, "q": [100, 100, 100, 100, 100]})
            continue
        fit = fits.get(tid)
        if fit is None:
            median = last_rate
            resid = 0.0
            refresh_sigma = REFRESH_SIGMA_NOFIT
        else:
            a, b, resid = fit
            b_adj = DAMP * ((1 - SHRINK) * b + SHRINK * mean_slope)
            median = last_rate * math.exp(b_adj * (x_target - x_of(parse_time(last["at"]))))
            median = min(max(median, last_rate * MIN_MOVE), last_rate * MAX_MOVE)
            median = clamp(median)
            refresh_sigma = REFRESH_SIGMA_FIT
        spread = math.sqrt(rw * rw + refresh_sigma * refresh_sigma + min(resid, 1.0) ** 2)
        lg = logit(median) + MEDIAN_SHIFT
        qs = []
        for z in Z:
            sz = spread * (SPREAD_DOWN if z < 0 else SPREAD_UP if z > 0 else 1.0)
            qs.append(round4(clamp(inv_logit(lg + z * sz))))
        # Guarantee non-decreasing after rounding.
        for j in range(1, 5):
            if qs[j] < qs[j - 1]:
                qs[j] = qs[j - 1]
        rows.append({"id": tid, "q": qs})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
