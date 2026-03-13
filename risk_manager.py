"""
Risk management module for position sizing, stop-loss, and take-profit calculations.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from config import Config
from hyperliquid_client import HyperliquidClient, round_size
from logger import TradeLogger, TradeDirection, get_logger
from strategy import Signal


@dataclass
class PositionSize:
    """Calculated position size details."""
    size: float  # Position size in base currency
    notional_value: float  # Position value in USDC
    risk_amount: float  # Amount at risk in USDC
    max_loss_percent: float  # Max loss as % of capital


class RiskManager:
    """
    Handles risk management calculations:
    - Position sizing based on risk percentage
    - Stop-loss and take-profit calculations
    - Leverage constraints
    - Capital protection rules
    """
    
    def __init__(
        self,
        config: Config,
        client: HyperliquidClient,
        logger: Optional[TradeLogger] = None
    ):
        self.config = config
        self.client = client
        self.logger = logger or get_logger()
        
        # Risk parameters
        self.max_risk_per_trade = config.risk_per_trade  # e.g., 0.01 = 1%
        self.reward_to_risk = config.reward_to_risk_ratio
        self.max_leverage = config.leverage
        
        # Track current capital
        self._capital: Optional[float] = None
    
    def update_capital(self) -> float:
        """Update and return current capital from API."""
        self._capital = self.client.get_account_value()
        self.logger.info(f"Account value updated: {self._capital:.2f} USDC (from API)")
        return self._capital
    
    @property
    def capital(self) -> float:
        """Get current capital (updates if not set)."""
        if self._capital is None:
            self.update_capital()
        if self._capital is None:
            self.logger.warning(f"Could not fetch account value, using config: {self.config.capital_usdc:.2f} USDC")
            return self.config.capital_usdc
        return self._capital
    
    
    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        signal: Signal,
        coin: str = "DEFAULT"
    ) -> PositionSize:
        """
        Calculate position size based on risk parameters.
        
        Uses the formula:
        Position Size = (Capital × Risk %) / |Entry - Stop Loss|
        
        Args:
            entry_price: Planned entry price
            stop_loss_price: Stop loss price
            signal: Trade direction
            coin: Trading pair for size precision (e.g., "BTC", "XRP")
            
        Returns:
            PositionSize with calculated values
        """
        # Calculate risk amount in USDC
        risk_amount = self.capital * self.max_risk_per_trade
        
        # Calculate stop loss distance
        sl_distance = abs(entry_price - stop_loss_price)
        
        if sl_distance <= 0:
            self.logger.warning("Invalid stop loss distance, using minimum")
            sl_distance = entry_price * 0.01  # 1% default
        
        # Calculate raw position size
        # For perps: size in base currency = risk / sl_distance
        raw_size = risk_amount / sl_distance
        
        # Apply leverage to get actual position size
        # Note: leverage affects margin requirement, not position size directly
        leveraged_size = raw_size * self.max_leverage
        
        # Calculate notional value
        notional_value = leveraged_size * entry_price
        
        # Ensure we don't exceed available capital (with leverage buffer)
        max_notional = self.capital * self.max_leverage * 0.95  # 5% buffer
        if notional_value > max_notional:
            leveraged_size = max_notional / entry_price
            notional_value = max_notional
            risk_amount = self.capital * self.max_risk_per_trade
        
        return PositionSize(
            size=round_size(leveraged_size, coin),  # Round to coin-specific precision
            notional_value=notional_value,
            risk_amount=risk_amount,
            max_loss_percent=self.max_risk_per_trade * 100
        )
    def calculate_sl_tp(
        self,
        signal: Signal,
        entry_price: float,
        atr: float
    ) -> Tuple[float, float]:
        """
        Calculate stop-loss and take-profit prices.
        
        Args:
            signal: Trade direction
            entry_price: Entry price
            atr: Current ATR value
            
        Returns:
            Tuple of (stop_loss, take_profit)
        """
        # ATR-based stop loss
        sl_distance = atr * self.config.atr_multiplier_sl
        
        # Risk/reward based take profit
        tp_distance = sl_distance * self.reward_to_risk
        
        if signal == Signal.LONG:
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SHORT
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
        
        # Round to reasonable precision (4 decimals for most crypto)
        stop_loss = round(stop_loss, 4)
        take_profit = round(take_profit, 4)
        
        return stop_loss, take_profit
    
    def calculate_pnl(
        self,
        direction: TradeDirection,
        entry_price: float,
        exit_price: float,
        size: float
    ) -> float:
        """
        Calculate realized PnL.
        
        Args:
            direction: Trade direction
            entry_price: Entry price
            exit_price: Exit price
            size: Position size
            
        Returns:
            PnL in USDC
        """
        if direction == TradeDirection.LONG:
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size
        
        return pnl
    
    def should_trail_stop(
        self,
        direction: TradeDirection,
        entry_price: float,
        current_stop: float,
        current_price: float,
        atr: float
    ) -> Tuple[bool, float]:
        """
        Determine if trailing stop should be updated.
        
        Trailing logic:
        - For LONG: Move stop up when price moves favorably
        - For SHORT: Move stop down when price moves favorably
        - Never move stop in unfavorable direction
        
        Args:
            direction: Position direction
            entry_price: Entry price
            current_stop: Current stop loss price
            current_price: Current market price
            atr: Current ATR value
            
        Returns:
            Tuple of (should_update, new_stop_price)
        """
        if not self.config.trailing_stop_enabled:
            return False, current_stop
        
        trailing_distance = atr * self.config.trailing_stop_atr_mult
        
        if direction == TradeDirection.LONG:
            # For longs: only trail up, never down
            # Also check we're in profit (price > entry)
            if current_price <= entry_price:
                return False, current_stop
            
            new_stop = current_price - trailing_distance
            
            if new_stop > current_stop:
                # Also ensure new stop is above entry (lock in profit)
                if new_stop > entry_price:
                    return True, round(new_stop, 4)
        
        else:  # SHORT
            # For shorts: only trail down, never up
            if current_price >= entry_price:
                return False, current_stop
            
            new_stop = current_price + trailing_distance
            
            if new_stop < current_stop:
                if new_stop < entry_price:
                    return True, round(new_stop, 4)
        
        return False, current_stop
    
    def validate_trade(
        self,
        signal: Signal,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float
    ) -> Tuple[bool, str]:
        """
        Validate trade parameters.
        
        Args:
            signal: Trade signal
            size: Position size
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check size is positive
        if size <= 0:
            return False, "Position size must be positive"
        
        # Check notional doesn't exceed capital × leverage
        notional = size * entry_price
        max_notional = self.capital * self.max_leverage
        
        if notional > max_notional:
            return False, f"Position notional ({notional:.2f}) exceeds max ({max_notional:.2f})"
        
        # Validate SL/TP based on direction
        if signal == Signal.LONG:
            if stop_loss >= entry_price:
                return False, "Stop loss must be below entry for longs"
            if take_profit <= entry_price:
                return False, "Take profit must be above entry for longs"
        
        elif signal == Signal.SHORT:
            if stop_loss <= entry_price:
                return False, "Stop loss must be above entry for shorts"
            if take_profit >= entry_price:
                return False, "Take profit must be below entry for shorts"
        
        # Check risk/reward ratio
        sl_distance = abs(entry_price - stop_loss)
        tp_distance = abs(entry_price - take_profit)
        
        actual_rr = tp_distance / sl_distance if sl_distance > 0 else 0
        
        # Use small epsilon for float comparison to handle precision issues
        if actual_rr < self.reward_to_risk - 0.001:
            return False, f"Risk/reward ratio ({actual_rr:.2f}) below minimum ({self.reward_to_risk})"
        return True, ""
    
    def get_risk_summary(self) -> str:
        """Get current risk settings summary."""
        return (
            f"Risk Settings: "
            f"Capital={self.capital:.2f} USDC, "
            f"Risk/Trade={self.max_risk_per_trade*100:.1f}%, "
            f"Max Leverage={self.max_leverage}x, "
            f"R:R Ratio={self.reward_to_risk}:1, "
            f"Trailing Stop={'ON' if self.config.trailing_stop_enabled else 'OFF'}"
        )
