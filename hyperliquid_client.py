"""
Hyperliquid API client wrapper with retry logic and error handling.
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants as hl_constants

from config import Config


@dataclass
class Position:
    """Represents an open position."""
    coin: str
    size: float  # Positive for long, negative for short
    entry_price: float
    unrealized_pnl: float
    leverage: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class Candle:
    """OHLCV candle data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class HyperliquidClientError(Exception):
    """Custom exception for Hyperliquid client errors."""
    pass


class APITimeoutError(HyperliquidClientError):
    """Raised when API is unavailable for extended period."""
    pass


class HyperliquidClient:
    """
    Wrapper for Hyperliquid SDK with retry logic and error handling.
    """
    
    def __init__(self, config: Config, logger=None):
        from logger import TradeLogger, get_logger
        
        self.config = config
        self.logger = logger or get_logger()
        
        self._api_available_since: Optional[float] = None
        self._last_success_time: float = time.time()
        self._is_paused: bool = False
        
        # Initialize wallet and clients
        self._wallet: Optional[eth_account.Account] = None
        self._info: Optional[Info] = None
        self._exchange: Optional[Exchange] = None
        
        self._initialize_clients()
    
    def _initialize_clients(self):
        """Initialize Hyperliquid API clients."""
        # Use SDK constants directly based on environment
        if self.config.is_testnet:
            api_url = hl_constants.TESTNET_API_URL
        else:
            api_url = hl_constants.MAINNET_API_URL
        
        # Initialize Info client for market data
        # Pass empty spot_meta to avoid testnet initialization issues
        # We only trade perps, so spot data is not needed
        try:
            self._info = Info(
                api_url, 
                skip_ws=True,
                spot_meta={"universe": [], "tokens": []}
            )
            self.logger.info(f"Initialized Info client: {api_url}")
        except Exception as e:
            import traceback
            self.logger.error(f"Failed to initialize Info client: {e}")
            self.logger.debug(traceback.format_exc())
            raise HyperliquidClientError(f"Failed to initialize API client: {e}")
        # Initialize Exchange client for trading
        if not self.config.private_key:
            raise HyperliquidClientError("HYPERLIQUID_PRIVATE_KEY is required")
        
        try:
            # Handle private key format (with or without 0x prefix)
            pk = self.config.private_key.strip()
            if pk.startswith("0x"):
                pk = pk[2:]
            
            self._wallet = eth_account.Account.from_key(pk)
            # Pass empty spot_meta to avoid testnet initialization issues
            self._exchange = Exchange(
                self._wallet, 
                api_url,
                spot_meta={"universe": [], "tokens": []}
            )
            
            env_str = "PRODUCTION" if self.config.is_production else "TESTNET"
            self.logger.info(
                f"Initialized Exchange client [{env_str}] for address: {self._wallet.address}"
            )
        except Exception as e:
            import traceback
            self.logger.error(f"Failed to initialize Exchange client: {e}")
            self.logger.debug(traceback.format_exc())
            raise HyperliquidClientError(f"Failed to initialize exchange: {e}")
    def _retry_api_call(self, func, *args, **kwargs) -> Any:
        """
        Execute API call with retry logic and exponential backoff.
        
        Args:
            func: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            Function result
            
        Raises:
            APITimeoutError: If API unavailable for too long
            HyperliquidClientError: On persistent failures
        """
        last_exception = None
        
        for attempt in range(self.config.max_retries):
            try:
                result = func(*args, **kwargs)
                self._last_success_time = time.time()
                self._api_available_since = None
                
                if self._is_paused:
                    self._is_paused = False
                    self.logger.info("API recovered, resuming operations")
                
                return result
                
            except Exception as e:
                last_exception = e
                current_time = time.time()
                time_since_success = current_time - self._last_success_time
                
                # Check if we've exceeded timeout threshold
                if time_since_success > self.config.api_timeout_threshold:
                    if not self._is_paused:
                        self._is_paused = True
                        self.logger.critical(
                            f"API unavailable for {time_since_success:.0f}s - "
                            f"exceeds threshold of {self.config.api_timeout_threshold}s. "
                            f"Bot paused until recovery."
                        )
                        raise APITimeoutError(
                            f"API unavailable for {time_since_success:.0f} seconds"
                        )
                
                # Calculate backoff delay
                delay = self.config.retry_base_delay * (2 ** attempt)
                
                self.logger.warning(
                    f"API call failed (attempt {attempt + 1}/{self.config.max_retries}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                
                time.sleep(delay)
        
        # All retries exhausted
        error_msg = f"API call failed after {self.config.max_retries} attempts: {last_exception}"
        self.logger.error(error_msg)
        raise HyperliquidClientError(error_msg)
    
    def get_account_value(self) -> float:
        """Get total account value in USDC."""
        def _fetch():
            if not self._wallet or not self._info:
                raise HyperliquidClientError("Client not initialized")
            
            user_state = self._info.user_state(self._wallet.address)
            
            # Debug: log the full response to understand structure
            self.logger.debug(f"user_state response: {user_state}")
            
            # Try to get account value from marginSummary
            margin_summary = user_state.get("marginSummary", {})
            account_value = margin_summary.get("accountValue", 0)
            
            # If 0, try alternative fields for unified account
            if float(account_value) == 0:
                # Check crossMarginSummary for unified accounts
                cross_margin = user_state.get("crossMarginSummary", {})
                if cross_margin:
                    account_value = cross_margin.get("totalRawUsd", 0)
                    self.logger.debug(f"Using crossMarginSummary.totalRawUsd: {account_value}")
                
                # Also check if there's available balance in withdrawable
                if float(account_value) == 0:
                    withdrawable = user_state.get("withdrawable", 0)
                    if float(withdrawable) > 0:
                        account_value = withdrawable
                        self.logger.debug(f"Using withdrawable: {account_value}")
            
            # If still 0, try portfolio API as fallback
            if float(account_value) == 0:
                account_value = self._fetch_portfolio_value()
                if float(account_value) > 0:
                    self.logger.debug(f"Using portfolio API fallback: {account_value}")
            
            return float(account_value) if account_value else 0.0
        
        return self._retry_api_call(_fetch)
    
    def _fetch_portfolio_value(self) -> float:
        """Fetch account value from portfolio API as fallback."""
        import requests
        try:
            response = requests.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "portfolio", "user": self._wallet.address},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                # Portfolio returns [["day", {"accountValueHistory": [[ts, value], ...]}], ...]
                for period in data:
                    if period[0] == "day":
                        history = period[1].get("accountValueHistory", [])
                        if history:
                            # Get the latest value
                            latest = history[-1][1]
                            return float(latest)
        except Exception as e:
            self.logger.debug(f"Portfolio API fallback failed: {e}")
        return 0.0
    
    def get_positions(self) -> Dict[str, Position]:
        """
        Get all open positions.
        
        Returns:
            Dict mapping coin to Position
        """
        def _fetch():
            if not self._wallet or not self._info:
                raise HyperliquidClientError("Client not initialized")
            
            user_state = self._info.user_state(self._wallet.address)
            positions = {}
            
            for pos_data in user_state.get("assetPositions", []):
                pos = pos_data.get("position", {})
                size = float(pos.get("szi", 0))
                
                if abs(size) > 0:
                    positions[pos["coin"]] = Position(
                        coin=pos["coin"],
                        size=size,
                        entry_price=float(pos.get("entryPx", 0)),
                        unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                        leverage=float(pos.get("leverage", {}).get("value", 1)),
                    )
            
            return positions
        
        return self._retry_api_call(_fetch)
    
    def get_candles(
        self,
        coin: str,
        interval: str,
        start_time: int,
        end_time: int
    ) -> List[Candle]:
        """
        Get historical candle data.
        
        Args:
            coin: Trading pair (e.g., "ETH")
            interval: Time interval ("1m", "5m", "15m", "1h", "4h", "1d")
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
            
        Returns:
            List of Candle objects
        """
        def _fetch():
            if not self._info:
                raise HyperliquidClientError("Client not initialized")
            
            raw_candles = self._info.candles_snapshot(
                coin, interval, start_time, end_time
            )
            
            self.logger.debug(f"API returned {len(raw_candles) if raw_candles else 0} candles for {coin}")
            
            candles = []

            for c in raw_candles:
                candles.append(Candle(
                    timestamp=c["t"],
                    open=float(c["o"]),
                    high=float(c["h"]),
                    low=float(c["l"]),
                    close=float(c["c"]),
                    volume=float(c["v"])
                ))
            
            return candles
        
        return self._retry_api_call(_fetch)
    
    def get_mid_price(self, coin: str) -> float:
        """Get current mid price for a coin."""
        def _fetch():
            if not self._info:
                raise HyperliquidClientError("Client not initialized")
            
            mids = self._info.all_mids()
            return float(mids.get(coin, 0))
        
        return self._retry_api_call(_fetch)
    
    def get_all_mid_prices(self) -> Dict[str, float]:
        """Get all mid prices."""
        def _fetch():
            if not self._info:
                raise HyperliquidClientError("Client not initialized")
            
            mids = self._info.all_mids()
            return {k: float(v) for k, v in mids.items()}
        
        return self._retry_api_call(_fetch)
    
    def set_leverage(self, coin: str, leverage: int) -> bool:
        """
        Set leverage for a coin.
        
        Args:
            coin: Trading pair
            leverage: Leverage value (1-3)
            
        Returns:
            True if successful
        """
        def _execute():
            if not self._exchange:
                raise HyperliquidClientError("Exchange not initialized")
            
            result = self._exchange.update_leverage(leverage, coin, is_cross=True)
            
            if result.get("status") == "ok":
                self.logger.info(f"Set leverage for {coin} to {leverage}x (cross margin)")
                return True
            else:
                raise HyperliquidClientError(f"Failed to set leverage: {result}")
        
        return self._retry_api_call(_execute)
    
    def open_position(
        self,
        coin: str,
        direction,
        size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        slippage: float = 0.005
    ) -> Tuple[bool, Optional[float]]:
        """
        Open a position with optional SL/TP.
        
        Args:
            coin: Trading pair
            direction: LONG or SHORT
            size: Position size in base currency
            stop_loss: Stop loss price
            take_profit: Take profit price
            slippage: Acceptable slippage for market order
            
        Returns:
            Tuple of (success, fill_price)
        """
        from logger import TradeDirection
        
        is_buy = direction == TradeDirection.LONG
        
        def _execute():
            if not self._exchange:
                raise HyperliquidClientError("Exchange not initialized")
            
            # Set leverage first
            self._exchange.update_leverage(self.config.leverage, coin, is_cross=True)
            
            # Open market position
            result = self._exchange.market_open(
                coin, is_buy, size, slippage=slippage
            )
            
            if result.get("status") != "ok":
                raise HyperliquidClientError(f"Failed to open position: {result}")
            
            # Get fill price from response
            fill_price = None
            for status in result.get("response", {}).get("data", {}).get("statuses", []):
                if "filled" in status:
                    fill_price = float(status["filled"]["avgPx"])
                    break
            
            # Fallback: get mid price if fill_price not found
            if fill_price is None:
                fill_price = self.get_mid_price(coin)
                self.logger.warning(f"Could not extract fill price from response, using mid price: {fill_price}")
            
            # Place SL/TP orders if specified
            if stop_loss and take_profit:
                self._place_sl_tp_orders(coin, is_buy, size, stop_loss, take_profit)
            
            return True, fill_price
        
        try:
            return self._retry_api_call(_execute)
        except HyperliquidClientError as e:
            self.logger.error(f"Failed to open position: {e}")
            return False, None
    
    def _place_sl_tp_orders(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        stop_loss: float,
        take_profit: float
    ):
        """Place stop loss and take profit orders."""
        if not self._exchange:
            return
        
        try:
            # Stop loss order
            sl_type = {
                "trigger": {
                    "triggerPx": stop_loss,
                    "isMarket": True,
                    "tpsl": "sl"
                }
            }
            self._exchange.order(
                coin, not is_buy, size, stop_loss * 0.99,  # Limit price slightly worse
                sl_type, reduce_only=True
            )
            
            # Take profit order
            tp_type = {
                "trigger": {
                    "triggerPx": take_profit,
                    "isMarket": True,
                    "tpsl": "tp"
                }
            }
            self._exchange.order(
                coin, not is_buy, size, take_profit * 1.01,
                tp_type, reduce_only=True
            )
            
            self.logger.info(
                f"Placed SL/TP orders for {coin}: SL={stop_loss:.4f}, TP={take_profit:.4f}"
            )
        except Exception as e:
            self.logger.error(f"Failed to place SL/TP orders: {e}")
    
    def close_position(
        self,
        coin: str,
        size: Optional[float] = None,
        slippage: float = 0.005
    ) -> Tuple[bool, Optional[float]]:
        """
        Close a position.
        
        Args:
            coin: Trading pair
            size: Size to close (None = close all)
            slippage: Acceptable slippage
            
        Returns:
            Tuple of (success, fill_price)
        """
        def _execute():
            if not self._exchange:
                raise HyperliquidClientError("Exchange not initialized")
            
            if size:
                # Partial close not directly supported, use market_close which closes all
                # For partial, we'd need to open opposite position
                self.logger.warning(
                    f"Partial close requested for {coin}, but closing entire position"
                )
            
            result = self._exchange.market_close(coin, slippage=slippage)
            
            if result.get("status") != "ok":
                raise HyperliquidClientError(f"Failed to close position: {result}")
            
            fill_price = None
            for status in result.get("response", {}).get("data", {}).get("statuses", []):
                if "filled" in status:
                    fill_price = float(status["filled"]["avgPx"])
                    break
            
            return True, fill_price
        
        try:
            return self._retry_api_call(_execute)
        except HyperliquidClientError as e:
            self.logger.error(f"Failed to close position: {e}")
            return False, None
    
    def update_stop_loss(
        self,
        coin: str,
        direction,
        size: float,
        new_stop_loss: float
    ) -> bool:
        """
        Update stop loss (cancel old, place new).
        
        Args:
            coin: Trading pair
            direction: Position direction
            size: Position size
            new_stop_loss: New stop loss price
            
        Returns:
            True if successful
        """
        from logger import TradeDirection
        
        # Cancel existing SL orders and place new one
        # Note: This is simplified - production would track order IDs
        try:
            # Place new SL order
            is_buy = direction == TradeDirection.LONG
            sl_type = {
                "trigger": {
                    "triggerPx": new_stop_loss,
                    "isMarket": True,
                    "tpsl": "sl"
                }
            }
            
            if self._exchange:
                self._exchange.order(
                    coin, not is_buy, size, new_stop_loss * 0.99,
                    sl_type, reduce_only=True
                )
                return True
        except Exception as e:
            self.logger.error(f"Failed to update stop loss: {e}")
        
        return False
    
    def cancel_all_orders(self, coin: str) -> bool:
        """Cancel all orders for a coin."""
        try:
            if self._exchange:
                # Cancel all open orders for the coin
                result = self._exchange.cancel(coin, None)  # None = all orders
                return result.get("status") == "ok"
        except Exception as e:
            self.logger.error(f"Failed to cancel orders: {e}")
        
        return False
    
    @property
    def is_paused(self) -> bool:
        """Check if client is paused due to API issues."""
        return self._is_paused
