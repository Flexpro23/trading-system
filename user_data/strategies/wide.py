"""Research variant (2026-11-16): the paper bot's rule with half the risk per trade,
for testing more coins and more open slots. Not used by the running bot."""
from candidates import TSMomentum


class TSMomentumHalf(TSMomentum):
    RISK_PER_TRADE = 0.005
