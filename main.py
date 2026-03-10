"""
Main trading bot loop.

Orchestrates:
- Fetching market data
- Computing indicators and signals
- Managing positions
- Executing trades
- Handling errors and recovery
"""

import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from config import config
from hyperliquid_client import HyperliquidClient, APITimeoutError, HyperliquidClientError
from logger import (
    TradeLogger, TradeDirection, TradeAction,
    init_logger, get_logger
)
from risk_manager import RiskManager
from strategy import Strategy, Signal


@dataclass
class TrackedPosition:
    """Tracks position state for trailing stops."""
    coin: str
    direction: TradeDirection
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    atr: float


class TradingBot:
    """
    Main trading bot class.
    
    Implements a continuous loop that:
    1. Fetches candle data for configured pairs
    2. Computes technical indicators
    3. Generates trading signals
    4. Manages positions (open, close, trail stops)
    5. Logs all activity
    """
    
    # Timeframe to sleep interval mapping (in seconds)
    TIMEFRAME_INTERVALS = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }
    
    def __init__(self):
        # Initialize logger
        self.logger = init_logger(config.log_level, "logs")
        self.logger.info(f"Starting Trading Bot | {config}")
        
        # Initialize components
        self.client = HyperliquidClient(config, self.logger)
        self.strategy = Strategy(config)
        self.risk_manager = RiskManager(config, self.client, self.logger)
        
        # Position tracking for trailing stops
        self.positions: Dict[str, TrackedPosition] = {}
        
        # Bot state
        self.running = True
        self.paused = False
        self.last_loop_time: Optional[datetime] = None
        
        # Setup signal handlers
        self._setup_signal_handlers()
        
        # Fetch actual capital from API
        try:
            self.risk_manager.update_capital()
        except Exception as e:
            self.logger.warning(f"Could not fetch account value: {e}")
        
        self.logger.info(self.risk_manager.get_risk_summary())
    
    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers."""
        def handle_shutdown(signum, frame):
            self.logger.info(f"Received signal {signum}, shutting down...")
            self.running = False
        
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
    
    def _get_loop_interval(self) -> int:
        """Get sleep interval based on timeframe."""
        return self.TIMEFRAME_INTERVALS.get(config.timeframe, 3600)
    
    def _get_required_candles(self) -> int:
        """Get number of candles needed for strategy."""
        return config.candles_limit  # Hardcoded 500 candles for reliable indicators
    
    def _fetch_candles(self, coin: str) -> Optional[list]:
        """Fetch historical candles for a coin."""
        try:
            # Calculate time range
            end_time = int(time.time() * 1000)
            
            # Estimate candles needed based on timeframe
            interval_ms = self._get_loop_interval() * 1000
            candles_needed = self._get_required_candles()
            start_time = end_time - (candles_needed * interval_ms)
            
            self.logger.debug(f"Fetching {candles_needed} candles for {coin}, timeframe={config.timeframe}, interval_ms={interval_ms}")
            
            candles = self.client.get_candles(
                coin, config.timeframe, start_time, end_time
            )
            
            self.logger.debug(f"Got {len(candles) if candles else 0} candles for {coin}")
            
            return candles
        except (HyperliquidClientError, APITimeoutError) as e:
            self.logger.error(f"Failed to fetch candles for {coin}: {e}")
            return None
    

    def _check_position_status(self, coin: str) -> Optional[str]:
        """
        Check current position status for a coin.
        
        Returns:
            "LONG", "SHORT", or None
        """
        # Check tracked positions first
        if coin in self.positions:
            return self.positions[coin].direction.value
        
        # Check actual positions from API
        positions = self.client.get_positions()
        if coin in positions:
            pos = positions[coin]
            if pos.size > 0:
                return "LONG"
            elif pos.size < 0:
                return "SHORT"
        
        return None
    
    def _process_entry_signal(
        self,
        coin: str,
        signal: Signal,
        indicators
    ):
        """Process an entry signal."""
        # Get current price
        current_price = self.client.get_mid_price(coin)
        
        # Calculate position size and SL/TP
        stop_loss, take_profit = self.risk_manager.calculate_sl_tp(
            signal, current_price, indicators.atr
        )
        
        position_size = self.risk_manager.calculate_position_size(
            current_price, stop_loss, signal
        )
        
        # Validate trade
        is_valid, error = self.risk_manager.validate_trade(
            signal, position_size.size, current_price, stop_loss, take_profit
        )
        
        if not is_valid:
            self.logger.warning(f"Trade validation failed for {coin}: {error}")
            return
        
        direction = TradeDirection.LONG if signal == Signal.LONG else TradeDirection.SHORT
        
        # Log signal
        reason = self.strategy.get_signal_reason(signal, indicators)
        self.logger.log_signal(
            coin, direction, current_price, True, reason,
            {"atr": indicators.atr, "rsi": indicators.rsi}
        )
        
        # Execute trade
        success, fill_price = self.client.open_position(
            coin, direction, position_size.size, stop_loss, take_profit
        )
        
        if success and fill_price:
            # Recalculate SL/TP with actual fill price
            stop_loss, take_profit = self.risk_manager.calculate_sl_tp(
                signal, fill_price, indicators.atr
            )
            
            # Log entry
            self.logger.log_entry(
                coin, direction, fill_price, position_size.size,
                stop_loss, take_profit
            )
            
            # Track position
            self.positions[coin] = TrackedPosition(
                coin=coin,
                direction=direction,
                size=position_size.size,
                entry_price=fill_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_time=datetime.utcnow(),
                atr=indicators.atr
            )
        else:
            self.logger.error(f"Failed to open position for {coin}")
    
    def _process_exit_signal(
        self,
        coin: str,
        signal: Signal,
        indicators
    ):
        """Process an exit signal."""
        if coin not in self.positions:
            # Try to get from API
            positions = self.client.get_positions()
            if coin not in positions:
                return
            
            # Build tracked position from API data
            pos = positions[coin]
            direction = TradeDirection.LONG if pos.size > 0 else TradeDirection.SHORT
            
            self.positions[coin] = TrackedPosition(
                coin=coin,
                direction=direction,
                size=abs(pos.size),
                entry_price=pos.entry_price,
                stop_loss=pos.stop_loss or 0,
                take_profit=pos.take_profit or 0,
                entry_time=datetime.utcnow(),
                atr=0
            )
        
        position = self.positions[coin]
        
        # Log exit signal
        reason = self.strategy.get_signal_reason(signal, indicators)
        self.logger.info(f"Exit signal for {coin}: {reason}")
        
        # Execute close
        success, fill_price = self.client.close_position(coin)
        
        if success and fill_price:
            # Calculate PnL
            pnl = self.risk_manager.calculate_pnl(
                position.direction, position.entry_price, fill_price, position.size
            )
            
            # Determine exit reason
            exit_reason = TradeAction.EXIT_SIGNAL
            
            # Log exit
            self.logger.log_exit(
                coin, position.direction, fill_price,
                position.entry_price, position.size, pnl,
                exit_reason
            )
            
            # Remove tracked position
            del self.positions[coin]
        else:
            self.logger.error(f"Failed to close position for {coin}")
    
    def _update_trailing_stop(self, coin: str, atr: float):
        """Update trailing stop if conditions met."""
        if coin not in self.positions:
            return
        
        position = self.positions[coin]
        current_price = self.client.get_mid_price(coin)
        
        # Check if we should trail
        should_update, new_stop = self.risk_manager.should_trail_stop(
            position.direction, position.entry_price,
            position.stop_loss, current_price, atr
        )
        
        if should_update:
            old_stop = position.stop_loss
            
            # Update on exchange
            self.client.update_stop_loss(
                coin, position.direction, position.size, new_stop
            )
            
            # Update tracked position
            position.stop_loss = new_stop
            
            # Log update
            self.logger.log_trailing_stop_update(
                coin, position.direction, old_stop, new_stop,
                current_price
            )
    
    def _process_coin(self, coin: str):
        """Process a single trading pair."""
        # Fetch candles
        candles = self._fetch_candles(coin)
        if not candles:
            self.logger.warning(f"No candles data for {coin}")
            return
        
        self.logger.debug(f"Fetched {len(candles)} candles for {coin}")
        """Process a single trading pair."""
        # Fetch candles
        candles = self._fetch_candles(coin)
        if not candles:
            self.logger.warning(f"No candles data for {coin}")
            return
        
        # Check current position
        current_position = self._check_position_status(coin)
        
        # Generate signal
        signal, indicators = self.strategy.generate_signal(candles, current_position)
        
        if indicators is None:
            self.logger.debug(f"Insufficient indicator data for {coin}")
            return
        
        # Log indicator values
        self.logger.debug(
            f"{coin} | EMA9={indicators.ema_fast:.2f} | "
            f"EMA21={indicators.ema_slow:.2f} | "
            f"RSI={indicators.rsi:.1f} | "
            f"ATR={indicators.atr:.4f} | "
            f"Signal={signal.value}"
        )
        
        # Update trailing stop
        if coin in self.positions:
            self._update_trailing_stop(coin, indicators.atr)
        
        # Process signals
        if signal == Signal.LONG and current_position is None:
            self._process_entry_signal(coin, signal, indicators)
        
        elif signal == Signal.SHORT and current_position is None:
            self._process_entry_signal(coin, signal, indicators)
        
        elif signal == Signal.EXIT_LONG and current_position == "LONG":
            self._process_exit_signal(coin, signal, indicators)
        
        elif signal == Signal.EXIT_SHORT and current_position == "SHORT":
            self._process_exit_signal(coin, signal, indicators)
    
    def run(self):
        """Main bot loop."""
        self.logger.info(f"Starting main loop | Pairs: {config.trading_pairs}")
        self.logger.info(f"Loop interval: {config.timeframe} ({self._get_loop_interval()}s)")
        
        while self.running:
            try:
                loop_start = datetime.utcnow()
                
                # Check if API is available
                if self.client.is_paused:
                    self.logger.warning("Bot paused due to API issues, waiting...")
                    time.sleep(60)
                    continue
                
                # Update capital
                self.risk_manager.update_capital()
                
                # Process each trading pair
                for coin in config.trading_pairs:
                    try:
                        self._process_coin(coin)
                    except Exception as e:
                        self.logger.error(f"Error processing {coin}: {e}")
                        continue
                
                # Calculate sleep time
                loop_duration = (datetime.utcnow() - loop_start).total_seconds()
                sleep_time = max(0, self._get_loop_interval() - loop_duration)
                
                # Sleep until next interval
                if sleep_time > 0:
                    self.logger.debug(f"Sleeping for {sleep_time:.0f}s until next interval")
                    time.sleep(sleep_time)
                
                self.last_loop_time = datetime.utcnow()
                
            except APITimeoutError:
                self.logger.error("API timeout, pausing operations")
                self.paused = True
                time.sleep(60)
                
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
                self.running = False
                
            except Exception as e:
                self.logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(10)  # Brief pause before retry
        
        self.logger.info("Bot shutdown complete")


def main():
    """Entry point."""
    try:
        bot = TradingBot()
        bot.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
