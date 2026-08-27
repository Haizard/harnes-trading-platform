"""
📐 Moon Dev's Position Sizing Optimizer
DSH Pattern: Goal System — persistent objectives influencing every trade.

Right-sizes every trade based on:
- Signal confidence (higher confidence = larger position)
- Market volatility (higher vol = smaller position)
- Portfolio heat (more positions = smaller new ones)
- Risk/reward ratio (better R:R = larger position)

Usage:
    sizer = PositionSizer(config)
    size = sizer.calculate_size(
        signal_confidence=0.8,
        volatility=35.0,
        portfolio_value=1000,
        existing_positions=3,
    )
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSize:
    """The result of a position sizing calculation."""
    amount_usd: float
    pct_of_portfolio: float
    confidence_factor: float
    volatility_factor: float
    heat_factor: float
    kelly_fraction: float
    risk_per_trade_pct: float
    reasoning: str

    def to_dict(self) -> dict:
        return {
            'amount_usd': round(self.amount_usd, 2),
            'pct_of_portfolio': round(self.pct_of_portfolio, 2),
            'confidence_factor': round(self.confidence_factor, 3),
            'volatility_factor': round(self.volatility_factor, 3),
            'heat_factor': round(self.heat_factor, 3),
            'kelly_fraction': round(self.kelly_fraction, 3),
            'risk_per_trade_pct': round(self.risk_per_trade_pct, 2),
            'reasoning': self.reasoning,
        }


class PositionSizer:
    """
    Volatility-adjusted position sizing using Kelly Criterion.

    Instead of fixed $25 positions, sizes each trade based on:
    1. Kelly Criterion (optimal bet size from win rate and odds)
    2. Confidence scaling (higher confidence = closer to Kelly)
    3. Volatility adjustment (higher vol = smaller position)
    4. Portfolio heat (more exposure = smaller new positions)
    """

    def __init__(self, config: dict = None):
        config = config or {}
        self.max_position_pct = config.get('max_position_pct', 0.25)
        self.max_total_exposure_pct = config.get('max_total_exposure_pct', 0.80)
        self.min_position_usd = config.get('min_position_usd', 1.0)
        self.risk_per_trade_pct = config.get('risk_per_trade_pct', 0.02)  # 2% risk per trade

        # Historical stats (updated by feedback loop)
        self.win_rate = config.get('win_rate', 0.5)
        self.avg_win = config.get('avg_win', 0.05)  # 5% avg win
        self.avg_loss = config.get('avg_loss', 0.03)  # 3% avg loss

    def calculate_size(self, signal_confidence: float, volatility: float,
                       portfolio_value: float, existing_positions: int = 0,
                       current_exposure_pct: float = 0.0,
                       token_volatility: float = None) -> PositionSize:
        """
        Calculate optimal position size.

        Args:
            signal_confidence: 0.0 to 1.0
            volatility: Market volatility (higher = more volatile)
            portfolio_value: Total portfolio value in USD
            existing_positions: Number of open positions
            current_exposure_pct: Current exposure as decimal (0.0 to 1.0)
            token_volatility: Token-specific volatility (optional)

        Returns:
            PositionSize with recommended amount and reasoning
        """
        if portfolio_value <= 0:
            return PositionSize(
                amount_usd=0, pct_of_portfolio=0,
                confidence_factor=0, volatility_factor=0, heat_factor=0,
                kelly_fraction=0, risk_per_trade_pct=0,
                reasoning='Portfolio value is zero',
            )

        # 1. Kelly Criterion (optimal fraction)
        kelly = self._kelly_criterion()

        # 2. Confidence scaling
        confidence_factor = self._confidence_factor(signal_confidence)

        # 3. Volatility adjustment
        vol_factor = self._volatility_factor(volatility, token_volatility)

        # 4. Portfolio heat (more exposure = smaller new positions)
        heat_factor = self._heat_factor(current_exposure_pct, existing_positions)

        # 5. Combined sizing
        adjusted_kelly = kelly * confidence_factor * vol_factor * heat_factor

        # 6. Calculate USD amount
        max_amount = portfolio_value * self.max_position_pct
        kelly_amount = portfolio_value * adjusted_kelly
        risk_amount = portfolio_value * self.risk_per_trade_pct / max(self.avg_loss, 0.01)

        # Take the most conservative of the three
        amount_usd = min(max_amount, kelly_amount, risk_amount)

        # Apply minimum
        amount_usd = max(amount_usd, self.min_position_usd)

        # Apply maximum total exposure cap
        available = portfolio_value * self.max_total_exposure_pct - (portfolio_value * current_exposure_pct)
        amount_usd = min(amount_usd, max(available, 0))

        # Round to reasonable precision
        if amount_usd >= 100:
            amount_usd = round(amount_usd, 0)
        elif amount_usd >= 10:
            amount_usd = round(amount_usd, 1)
        else:
            amount_usd = round(amount_usd, 2)

        pct = (amount_usd / portfolio_value * 100) if portfolio_value > 0 else 0

        reasoning = self._build_reasoning(
            kelly, confidence_factor, vol_factor, heat_factor,
            amount_usd, portfolio_value
        )

        return PositionSize(
            amount_usd=amount_usd,
            pct_of_portfolio=round(pct, 2),
            confidence_factor=confidence_factor,
            volatility_factor=vol_factor,
            heat_factor=heat_factor,
            kelly_fraction=kelly,
            risk_per_trade_pct=self.risk_per_trade_pct * 100,
            reasoning=reasoning,
        )

    def update_stats(self, win_rate: float = None, avg_win: float = None,
                    avg_loss: float = None):
        """Update historical stats from feedback loop."""
        if win_rate is not None:
            self.win_rate = win_rate
        if avg_win is not None:
            self.avg_win = avg_win
        if avg_loss is not None:
            self.avg_loss = avg_loss

    # ── Internal Calculations ─────────────────────────────────

    def _kelly_criterion(self) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        Where:
          b = avg_win / avg_loss (odds)
          p = win_rate
          q = 1 - win_rate
        """
        if self.avg_loss <= 0 or self.win_rate <= 0:
            return 0.05  # Default 5%

        b = self.avg_win / self.avg_loss  # Odds
        p = self.win_rate
        q = 1 - p

        kelly = (b * p - q) / b

        # Half-Kelly for safety (standard practice)
        return max(0, min(kelly * 0.5, 0.25))

    def _confidence_factor(self, confidence: float) -> float:
        """
        Scale position by confidence.
        Low confidence (0.3) → 0.3x size
        High confidence (0.9) → 0.9x size
        """
        return max(0.1, min(confidence, 1.0))

    def _volatility_factor(self, volatility: float,
                          token_volatility: float = None) -> float:
        """
        Reduce position in high volatility.
        Normal vol (30) → 1.0x
        High vol (60) → 0.5x
        Extreme vol (100) → 0.3x
        """
        vol = token_volatility or volatility

        if vol <= 20:
            return 1.0
        elif vol <= 40:
            return 0.8
        elif vol <= 60:
            return 0.6
        elif vol <= 80:
            return 0.4
        else:
            return 0.3

    def _heat_factor(self, current_exposure_pct: float,
                    existing_positions: int) -> float:
        """
        Reduce position when portfolio is already loaded.
        0% exposure → 1.0x
        50% exposure → 0.6x
        80% exposure → 0.2x
        """
        if current_exposure_pct >= self.max_total_exposure_pct:
            return 0.0  # No room

        # Exposure factor
        exposure_ratio = current_exposure_pct / self.max_total_exposure_pct
        exposure_factor = max(0.1, 1.0 - exposure_ratio)

        # Position count factor
        if existing_positions >= 5:
            count_factor = 0.5
        elif existing_positions >= 3:
            count_factor = 0.7
        else:
            count_factor = 1.0

        return exposure_factor * count_factor

    def _build_reasoning(self, kelly, conf, vol, heat, amount, portfolio) -> str:
        """Build human-readable reasoning."""
        parts = []

        if kelly < 0.05:
            parts.append(f"Kelly={kelly:.1%} (low edge)")
        elif kelly > 0.15:
            parts.append(f"Kelly={kelly:.1%} (strong edge)")
        else:
            parts.append(f"Kelly={kelly:.1%}")

        if conf < 0.5:
            parts.append(f"low confidence ({conf:.0%})")
        elif conf > 0.8:
            parts.append(f"high confidence ({conf:.0%})")

        if vol < 0.7:
            parts.append(f"vol adjustment ({vol:.0%})")
        if heat < 0.7:
            parts.append(f"heat reduction ({heat:.0%})")

        pct = (amount / portfolio * 100) if portfolio > 0 else 0
        parts.append(f"→ ${amount:.2f} ({pct:.1f}% of portfolio)")

        return ' | '.join(parts)
