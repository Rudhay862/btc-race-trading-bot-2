"""BTC trading bot for make.fun btc-race-24h-20261113-2314.

Long-only EMA(12)/EMA(26) crossover bot on 1-minute cbBTC closes:
- Entry: fast EMA crosses above slow EMA, 60-minute return above +0 bps,
  and at least 720 bars (12 hours) since the last exit.
- Exit: 1.0% trailing stop from the highest close since entry.
- One open position, full cash on entry, orders fill at the next bar's open.
- Deterministic: no clock, no network, no randomness; pure stdlib + shim.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
FAST_N = 12
SLOW_N = 26
TRAIL_BPS = 100          # 1.0% trailing stop below the peak close
COOLDOWN_BARS = 720      # 12 hours between an exit and the next entry
REENTRY_BPS = 0          # 60-minute return must beat +0 bps to re-enter
SLIPPAGE_BPS = 100       # max slippage allowed from the latest close
MIN_NOTIONAL = 1000000   # 1 USDC in quote units


def ema_update(prev, price, n):
    alpha = 2.0 / (n + 1)
    if prev is None:
        return float(price)
    return alpha * price + (1.0 - alpha) * prev


class Trader:
    def __init__(self):
        self.closes = []
        self.ema_fast = None
        self.ema_slow = None
        self.prev_fast = None
        self.prev_slow = None
        self.peak = None
        self.last_exit_seq = -1000000
        self.sell_sent_seq = None

    def on_batch(self, state, batch):
        close = state.close(SYMBOL)
        if close is None:
            return []
        # Update indicators on every bar, warm-up included.
        self.prev_fast, self.prev_slow = self.ema_fast, self.ema_slow
        self.ema_fast = ema_update(self.ema_fast, close, FAST_N)
        self.ema_slow = ema_update(self.ema_slow, close, SLOW_N)
        self.closes.append(close)
        if state.warming_up:
            return []
        held = state.positions.get(SYMBOL)
        qty = held.qty if held else 0
        if qty > 0:
            # In position: track the peak close, exit on the trailing stop.
            if self.peak is None or close > self.peak:
                self.peak = close
            if self.sell_sent_seq is not None and state.seq > self.sell_sent_seq:
                # The sell either filled (position would be gone) or was
                # rejected; fall through and re-evaluate the stop.
                self.sell_sent_seq = None
            stop = self.peak * (10000 - TRAIL_BPS) // 10000
            if close <= stop:
                self.sell_sent_seq = state.seq
                self.last_exit_seq = state.seq
                self.peak = None
                return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                         "max_slippage_bps": SLIPPAGE_BPS}]
            return []
        # Flat: look for a fresh entry.
        self.peak = None
        if len(self.closes) < 61:
            return []
        if self.prev_fast is None or self.prev_slow is None:
            return []
        if state.seq - self.last_exit_seq < COOLDOWN_BARS:
            return []
        ret60_bps = (close - self.closes[-61]) * 10000 // self.closes[-61]
        if ret60_bps <= REENTRY_BPS:
            return []
        crossed = self.prev_fast <= self.prev_slow and self.ema_fast > self.ema_slow
        if not crossed:
            return []
        spend = min(state.cash, state.weight_room(SYMBOL))
        if spend < MIN_NOTIONAL:
            return []
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": spend,
                 "max_slippage_bps": SLIPPAGE_BPS}]


trader = Trader()


def on_batch(state, batch):
    return trader.on_batch(state, batch)


makefun_bars.run(on_batch)
