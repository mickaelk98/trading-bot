"""
Trading strategy module.

STRATEGY CHOICE: EMA Crossover with RSI Confirmation
====================================================

Why this strategy for Hyperliquid perpetuals?

1. TREND FOLLOWING SUITS PERPS:
   Perpetual futures markets tend to exhibit strong trending behavior, especially
   in crypto. EMA crossovers capture these trends effectively without trying to
   predict tops/bottoms.

2. MOMENTUM FILTER (RSI):
   RSI acts as a momentum filter to avoid entering on weak crossovers:
   - For LONG: Fast EMA crosses above Slow EMA + RSI not overbought (< 70)
   - For SHORT: Fast EMA crosses below Slow EMA + RSI not oversold (> 30)

3. ATR-BASED STOPS:
   ATR (Average True Range) adapts to volatility. In crypto, volatility varies
   dramatically - fixed % stops are either too tight (whipsaw) or too loose
   (excessive risk). ATR × multiplier scales with market conditions.

4. TREND REVERSAL EXITS:
   Exit signals are generated when the EMA crossover reverses, allowing the
   strategy to ride trends while protecting profits.

PARAMETERS:
- EMA Fast: 9 periods (responsive to short-term price action)
- EMA Slow: 21 periods (filters noise, identifies trend)
- RSI: 14 periods (standard setting)
- ATR: 14 periods (standard setting)

RISK PROFILE:
- Wins: ~35-45% (trend following has lower win rate)
- Risk/Reward: 1:2 minimum (compensates for lower win rate)
- Expectancy: Positive in trending markets, neutral/slightly negative in ranging

CAVEATS:
- Whipsaws in sideways/ranging markets (no strategy is perfect)
- Lagging indicator - enters after trend starts
- Best performance on higher timeframes (1h, 4h) with clear trends
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import pandas_ta as ta

from config import Config
from hyperliquid_client import Candle


class Signal(Enum):
    """Trading signal types."""
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    NONE = "NONE"


@dataclass
class IndicatorData:
    """Computed indicator values."""
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    close: float
    high: float
    low: float
    
    # Previous values for crossover detection
    prev_ema_fast: float
    prev_ema_slow: float
    prev_rsi: float


class Strategy:
    """
    EMA Crossover + RSI Confirmation Strategy.
    
    Entry conditions:
    - LONG: EMA(9) crosses above EMA(21) AND RSI < overbought level
    - SHORT: EMA(9) crosses below EMA(21) AND RSI > oversold level
    
    Exit conditions:
    - Exit LONG: EMA(9) crosses below EMA(21)
    - Exit SHORT: EMA(9) crosses above EMA(21)
    """
    
    def __init__(self, config: Config):
        self.config = config
        
        # Strategy parameters
        self.ema_fast_period = config.ema_fast_period
        self.ema_slow_period = config.ema_slow_period
        self.rsi_period = config.rsi_period
        self.rsi_overbought = config.rsi_overbought
        self.rsi_oversold = config.rsi_oversold
        self.atr_period = 14  # Standard ATR period
        
        # Minimum candles needed for indicator calculation
        self.min_candles = max(
            self.ema_slow_period,
            self.rsi_period,
            self.atr_period
        ) + 5  # Buffer for accurate calculations
    
    def compute_indicators(self, candles: List[Candle]) -> Optional[IndicatorData]:
        """
        Compute technical indicators from candle data.
        
        Args:
            candles: List of historical candles (oldest first)
            
        Returns:
            IndicatorData with current and previous indicator values
        """
        if len(candles) < self.min_candles:
            return None
        
        # Convert to DataFrame
        df = pd.DataFrame([{
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume
        } for c in candles])
        
        # Compute EMAs
        ema_fast = ta.ema(df["close"], length=self.ema_fast_period)
        ema_slow = ta.ema(df["close"], length=self.ema_slow_period)
        
        # Compute RSI
        rsi = ta.rsi(df["close"], length=self.rsi_period)
        
        # Compute ATR
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        
        # Check if the last two values are computed (not NaN) - only these are used
        if (pd.isna(ema_slow.iloc[-1]) or pd.isna(ema_slow.iloc[-2]) or
            pd.isna(rsi.iloc[-1]) or pd.isna(rsi.iloc[-2]) or
            pd.isna(atr.iloc[-1])):
            return None
        
        # Get last two values for crossover detection
        return IndicatorData(
            ema_fast=float(ema_fast.iloc[-1]),
            ema_slow=float(ema_slow.iloc[-1]),
            rsi=float(rsi.iloc[-1]),
            atr=float(atr.iloc[-1]),
            close=float(df["close"].iloc[-1]),
            high=float(df["high"].iloc[-1]),
            low=float(df["low"].iloc[-1]),
            prev_ema_fast=float(ema_fast.iloc[-2]),
            prev_ema_slow=float(ema_slow.iloc[-2]),
            prev_rsi=float(rsi.iloc[-2])
        )
    
    def generate_signal(
        self,
        candles: List[Candle],
        current_position: Optional[str] = None
    ) -> Tuple[Signal, Optional[IndicatorData]]:
        """
        Generate trading signal based on EMA crossover and RSI confirmation.
        
        Args:
            candles: Historical candle data
            current_position: Current position direction ("LONG", "SHORT", or None)
            
        Returns:
            Tuple of (Signal, IndicatorData)
        """
        indicators = self.compute_indicators(candles)
        
        if indicators is None:
            return Signal.NONE, None
        
        # Detect crossovers
        bullish_crossover = (
            indicators.prev_ema_fast <= indicators.prev_ema_slow and
            indicators.ema_fast > indicators.ema_slow
        )
        
        bearish_crossover = (
            indicators.prev_ema_fast >= indicators.prev_ema_slow and
            indicators.ema_fast < indicators.ema_slow
        )
        
        # Check RSI conditions
        rsi_not_overbought = indicators.rsi < self.rsi_overbought
        rsi_not_oversold = indicators.rsi > self.rsi_oversold
        
        # Generate signals
        if current_position == "LONG":
            # Exit long on bearish crossover
            if bearish_crossover:
                return Signal.EXIT_LONG, indicators
        
        elif current_position == "SHORT":
            # Exit short on bullish crossover
            if bullish_crossover:
                return Signal.EXIT_SHORT, indicators
        
        else:  # No position
            # Long entry: bullish crossover + RSI not overbought
            if bullish_crossover and rsi_not_overbought:
                return Signal.LONG, indicators
            
            # Short entry: bearish crossover + RSI not oversold
            if bearish_crossover and rsi_not_oversold:
                return Signal.SHORT, indicators
        
        return Signal.NONE, indicators
    
    def get_stop_loss_take_profit(
        self,
        signal: Signal,
        entry_price: float,
        atr: float
    ) -> Tuple[float, float]:
        """
        Calculate stop loss and take profit levels based on ATR.
        
        Args:
            signal: Trade signal (LONG or SHORT)
            entry_price: Entry price
            atr: Current ATR value
            
        Returns:
            Tuple of (stop_loss, take_profit)
        """
        # ATR-based stop loss distance
        sl_distance = atr * self.config.atr_multiplier_sl
        
        # Take profit based on risk/reward ratio
        tp_distance = sl_distance * self.config.reward_to_risk_ratio
        
        if signal == Signal.LONG:
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SHORT
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
        
        return stop_loss, take_profit
    
    def should_update_trailing_stop(
        self,
        signal: Signal,
        current_stop: float,
        current_price: float,
        atr: float
    ) -> Tuple[bool, float]:
        """
        Check if trailing stop should be updated.
        
        Args:
            signal: Position direction
            current_stop: Current stop loss price
            current_price: Current market price
            atr: Current ATR value
            
        Returns:
            Tuple of (should_update, new_stop_price)
        """
        if not self.config.trailing_stop_enabled:
            return False, current_stop
        
        trailing_distance = atr * self.config.trailing_stop_atr_mult
        
        if signal == Signal.LONG:
            # For longs, trail stop up (never down)
            new_stop = current_price - trailing_distance
            if new_stop > current_stop:
                return True, new_stop
        
        elif signal == Signal.SHORT:
            # For shorts, trail stop down (never up)
            new_stop = current_price + trailing_distance
            if new_stop < current_stop:
                return True, new_stop
        
        return False, current_stop
    
    def get_signal_reason(
        self,
        signal: Signal,
        indicators: IndicatorData
    ) -> str:
        """Get human-readable reason for the signal."""
        if signal == Signal.LONG:
            return (
                f"EMA({self.ema_fast_period}) crossed above EMA({self.ema_slow_period}) "
                f"at {indicators.close:.4f}, RSI={indicators.rsi:.1f} (not overbought)"
            )
        elif signal == Signal.SHORT:
            return (
                f"EMA({self.ema_fast_period}) crossed below EMA({self.ema_slow_period}) "
                f"at {indicators.close:.4f}, RSI={indicators.rsi:.1f} (not oversold)"
            )
        elif signal == Signal.EXIT_LONG:
            return (
                f"EMA crossover reversed (bearish) at {indicators.close:.4f}, "
                f"RSI={indicators.rsi:.1f}"
            )
        elif signal == Signal.EXIT_SHORT:
            return (
                f"EMA crossover reversed (bullish) at {indicators.close:.4f}, "
                f"RSI={indicators.rsi:.1f}"
            )
        return "No signal"
