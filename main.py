"""Washout-bounce bot for the BTC 24h race (agent-fun/bars-replay/v1).

Idea: 1-minute cbBTC mean-reverts after sharp washouts, but buying the dip itself
catches falling knives. So this bot only buys the TURN: the price must have
fallen at least DROP_BPS from its rolling LOOKBACK-bar high AND already bounced
at least BOUNCE_BPS off its rolling low. It then exits fast: a take-profit, a
stop-loss, or a time stop, whichever comes first. Long only, one position, at
most one order per batch.

All decisions use exact integer arithmetic on the bar closes (no floats), so
two executions on the same data produce identical orders. Sizing is 100% of
the smaller of cash and equity: with one asset and a 10,000 bps weight cap
that is one full-size position. Orders cap slippage at 100 bps past the close
they were decided on, so a fill into a sudden gap is refused and retried on a
later bar instead of filled at a ruinous price.

Parameters were chosen on the published dev window for a wide plateau, not a
peak: every one-step neighbour of the chosen values in (drop, bounce, take
profit, stop) also ends the dev window in profit, and the bot is profitable
on each half of the window alone.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
# Rolling window (bars) for the high/low the entry reads.
LOOKBACK = 20
# Entry: fall from the window high, in bps, that counts as a washout.
DROP_BPS = 50
# Entry: recovery off the window low, in bps, that confirms the turn.
BOUNCE_BPS = 30
# Exit: take profit / stop loss vs the position's cost (fees included), in bps.
TAKE_PROFIT_BPS = 120
STOP_LOSS_BPS = 50
# Exit: give up on a position held this many bars without hitting either.
MAX_HOLD_BARS = 90
# Slippage cap on every order, in bps past the close it was decided on.
MAX_SLIPPAGE_BPS = 100
BPS = 10_000


class WashoutBounce:
    """The strategy state: the closes seen so far and the open position's age."""

    def __init__(self):
        self.closes = []
        self.hold_bars = 0

    def on_batch(self, state, batch):
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.closes.append(int(bar["c"]))
        if state.warming_up:
            return []
        close = state.close(SYMBOL)
        if close is None:
            return []
        held = state.positions.get(SYMBOL)
        if held is not None:
            return self._maybe_exit(state, held)
        # Flat: the hold timer only ever counts bars actually spent holding, so
        # a sell the market refused does not restart the time stop.
        self.hold_bars = 0
        return self._maybe_enter(state, close)

    def _maybe_exit(self, state, held):
        self.hold_bars += 1
        value = state.value(SYMBOL, held.qty)
        take_profit = value * BPS >= held.cost * (BPS + TAKE_PROFIT_BPS)
        stop_loss = value * BPS <= held.cost * (BPS - STOP_LOSS_BPS)
        timed_out = self.hold_bars >= MAX_HOLD_BARS
        if take_profit or stop_loss or timed_out:
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    def _maybe_enter(self, state, close):
        if len(self.closes) <= LOOKBACK:
            return []
        window = self.closes[-LOOKBACK - 1:]
        high = max(window)
        low = min(window)
        washed_out = (high - close) * BPS >= DROP_BPS * high
        turned = (close - low) * BPS >= BOUNCE_BPS * low
        if not (washed_out and turned):
            return []
        size = min(state.cash, state.equity)
        min_notional = int(state.hello["fees"]["min_notional_quote_units"])
        if size < min_notional:
            return []
        return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": size,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]


def main():
    makefun_bars.run(WashoutBounce().on_batch)


if __name__ == "__main__":
    main()
