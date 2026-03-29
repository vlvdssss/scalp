# ML Filter — Development Plan

## Goal
Train a binary classifier that acts as a **gate** before every trade entry.
Model answers: "Given current market conditions, is this trade worth taking?"

```
[Market conditions at entry] → [ML model] → P(net_win) > threshold → ALLOW / BLOCK
```

The model **never** moves SL/TP or changes entry price. It only blocks bad entries.

---

## Target Variable

**Binary classification:**
```
label = 1  if  (pnl_usd - est_cost) > 0.20
label = 0  otherwise
```

**Estimated real-world cost per 0.01 lot XAUUSD:**
| Cost item        | USD estimate |
|------------------|-------------|
| Spread           | ~$0.04–0.06 |
| Slippage         | ~$0.02–0.04 |
| Commission       | ~$0.05–0.10 |
| **Total**        | **~$0.12–0.20** |

Use `threshold = 0.20` as conservative floor.
Adjust after seeing real broker commission on live account.

---

## Features (recorded per trade at FILL time)

### Market context
| Feature              | How computed                                   |
|----------------------|------------------------------------------------|
| `hour_utc`           | UTC hour of fill (0–23)                        |
| `session`            | ASIA/LONDON/NY/OVERLAP based on hour           |
| `minute_of_session`  | Minutes elapsed since session open             |
| `day_of_week`        | 0=Mon … 4=Fri                                  |

### Volatility / spread
| Feature              | How computed                                   |
|----------------------|------------------------------------------------|
| `atr_pts`            | 14-period Wilder ATR on M1 at fill time        |
| `spread_pts`         | Actual spread at fill                          |
| `spread_med_pts`     | Median spread over last N ticks                |
| `rel_spread`         | `spread_med_pts / atr_pts`                     |
| `candle_range_pts`   | Last M1 candle high−low in points              |
| `candle_range_ratio` | `candle_range_pts / atr_pts`                   |

### Entry pattern
| Feature              | How computed                                   |
|----------------------|------------------------------------------------|
| `side`               | 0=BUY, 1=SELL                                  |
| `offset_pts`         | Distance: `abs(entry_price - mid_at_time) / point` |
| `is_flat`            | 1 if flat-detector was active at fill          |

### Recent history
| Feature              | How computed                                   |
|----------------------|------------------------------------------------|
| `last_trade_pnl_usd` | P&L of previous trade                         |
| `last_trade_side`    | Side of previous trade (0/1)                  |
| `wins_last_5`        | Count of wins in last 5 trades                |
| `time_since_last_trade_sec` | Seconds since previous trade closed    |

---

## Outcome fields (recorded at TRADE_CLOSED)
| Field          | Description                          |
|----------------|--------------------------------------|
| `pnl_usd`      | Raw P&L                              |
| `pnl_pts`      | P&L in points                        |
| `mae_pts`      | Max Adverse Excursion (points)       |
| `mfe_pts`      | Max Favorable Excursion (points)     |
| `hold_sec`     | Position duration in seconds         |
| `exit_reason`  | sl_or_external / emergency / etc     |
| `be_triggered` | 1 if breakeven fired                 |
| `label`        | 1 if net pnl > 0.20, else 0          |

---

## Data storage
File: `logs/ml_features.csv`
- Appended row-by-row (no rewrite)
- One row = one completed trade
- Keep all raw fields; compute derived features during training

---

## Training pipeline (offline, after 1000+ trades)

### Step 1 — EDA (Jupyter)
```python
import pandas as pd, seaborn as sns
df = pd.read_csv("logs/ml_features.csv")
# Check label balance, correlations, feature importance
```

### Step 2 — Feature engineering
- Encode `session` as one-hot
- Compute `rel_spread = spread_med_pts / atr_pts`
- Compute `candle_range_ratio = candle_range_pts / atr_pts`

### Step 3 — Time-split (NEVER random split)
```python
split = int(len(df) * 0.75)
train, test = df.iloc[:split], df.iloc[split:]
# Walk-forward: retrain every 500 new trades
```

### Step 4 — Model
```python
from xgboost import XGBClassifier
model = XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                      scale_pos_weight=1.5,  # class imbalance
                      eval_metric='logloss')
model.fit(X_train, y_train)
```

### Step 5 — Threshold tuning
- Plot precision/recall vs threshold
- Pick threshold where `precision > 0.60` on test set
- Default starting point: `0.55`

### Step 6 — Save model
```python
import joblib
joblib.dump(model, "models/ml_filter_v1.pkl")
```

---

## Integration into bot (Step 3 — future)

```python
# config/default.yaml
ml_filter:
  enabled: false          # flip to true after model is trained
  model_path: models/ml_filter_v1.pkl
  min_proba: 0.58         # block if P(win) < 0.58
```

In `engine.py`, before `ensure_dual_pending()`:
```python
if self._ml_filter.enabled:
    features = self._feature_logger.current_features()
    if not self._ml_filter.allow(features):
        return  # skip this cycle
```

---

## Milestones

| # | Milestone                                  | Status      |
|---|--------------------------------------------|-------------|
| 1 | `FeatureLogger` records to CSV             | ✅ Done      |
| 2 | Hook into `engine.py` at FILL + CLOSE      | ✅ Done      |
| 3 | Accumulate 1000+ trades (demo)             | ⏳ Pending   |
| 4 | EDA notebook + feature selection           | ⏳ Pending   |
| 5 | Train XGBoost, walk-forward validation     | ⏳ Pending   |
| 6 | Save model, integrate MLFilter class       | ⏳ Pending   |
| 7 | A/B test: filter ON vs OFF on demo         | ⏳ Pending   |
| 8 | Deploy on live only if Sharpe improves     | ⏳ Pending   |

---

## Key rules (DO NOT BREAK)

1. **Model is read-only** — it never touches SL, TP, entry price
2. **Time-split only** — never random split for training/test
3. **Threshold conservative** — when in doubt, block (miss a trade > take a bad one)
4. **Retrain every ~500 new trades** — market regime shifts
5. **Demo validation before live** — run filter on demo for 1 week, compare metrics
