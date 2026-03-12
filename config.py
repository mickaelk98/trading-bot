"""
Configuration module for the Hyperliquid trading bot.
Loads and validates environment variables.
"""

import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Hardcoded safety limits
MAX_ALLOWED_LEVERAGE = 3
MIN_CAPITAL_USDC = 10

# API URLs (not configurable - determined by ENVIRONMENT)
API_URLS = {
    "testnet": "https://api.hyperliquid-testnet.xyz",
    "production": "https://api.hyperliquid.xyz",
}


class Environment(Enum):
    """Valid environment types."""
    TESTNET = "testnet"
    PRODUCTION = "production"


@dataclass
class Config:
    """Bot configuration loaded from environment variables."""
    
    # Authentication
    private_key: str = field(default_factory=lambda: os.getenv("HYPERLIQUID_PRIVATE_KEY", ""))
    
    # Environment (determines API URL)
    environment: Environment = Environment.TESTNET
    
    # Trading settings
    trading_pairs: List[str] = field(default_factory=list)
    leverage: int = 1
    capital_usdc: float = 1000.0
    timeframe: str = "1h"
    
    # API settings
    log_level: str = "INFO"
    
    # Candles limit for API fetch (hardcoded minimum for reliable indicators)
    candles_limit: int = 500
    
    # Strategy parameters
    ema_fast_period: int = 9
    ema_slow_period: int = 21
    ema_trend_period: int = 200  # EMA trend filter (background trend)
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    
    # Risk management
    risk_per_trade: float = 0.01  # 1% of capital
    reward_to_risk_ratio: float = 2.0
    atr_multiplier_sl: float = 2.0
    trailing_stop_enabled: bool = True
    trailing_stop_atr_mult: float = 1.5
    
    # API retry settings
    max_retries: int = 3
    retry_base_delay: float = 1.0
    api_timeout_threshold: int = 300  # 5 minutes
    
    # Wallet address (set after initialization)
    wallet_address: Optional[str] = None
    
    def __post_init__(self):
        """Load and validate configuration after initialization."""
        self._load_from_env()
        self._validate()
        self._show_production_warning()
    
    def _load_from_env(self):
        """Load all configuration from environment variables."""
        # Environment
        env_str = os.getenv("ENVIRONMENT", "testnet").lower().strip()
        try:
            self.environment = Environment(env_str)
        except ValueError:
            valid_values = [e.value for e in Environment]
            print("\n" + "=" * 60)
            print("CONFIGURATION ERROR:")
            print("=" * 60)
            print(f"  ENVIRONMENT='{env_str}' is invalid.")
            print(f"  Valid values: {', '.join(valid_values)}")
            print("=" * 60)
            print("\nPlease fix your .env file and restart.\n")
            sys.exit(1)
        
        # Trading pairs
        pairs_str = os.getenv("TRADING_PAIRS", "BTC,ETH")
        self.trading_pairs = [p.strip().upper() for p in pairs_str.split(",") if p.strip()]
        
        # Leverage
        self.leverage = int(os.getenv("LEVERAGE", "1"))
        
        # Capital
        self.capital_usdc = float(os.getenv("CAPITAL_USDC", "1000"))
        
        # Timeframe
        self.timeframe = os.getenv("TIMEFRAME", "1h")
        
        # Candles limit (hardcoded minimum for reliable indicators)
        self.candles_limit = int(os.getenv("CANDLES_LIMIT", "500"))
        
        # Log level
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        
        # Strategy parameters
        self.ema_fast_period = int(os.getenv("EMA_FAST_PERIOD", "9"))
        self.ema_slow_period = int(os.getenv("EMA_SLOW_PERIOD", "21"))
        self.ema_trend_period = int(os.getenv("EMA_TREND_PERIOD", "200"))
        self.rsi_period = int(os.getenv("RSI_PERIOD", "14"))
        self.rsi_overbought = float(os.getenv("RSI_OVERBOUGHT", "70"))
        self.rsi_oversold = float(os.getenv("RSI_OVERSOLD", "30"))
        
        # Risk management
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        self.reward_to_risk_ratio = float(os.getenv("REWARD_TO_RISK_RATIO", "2.0"))
        self.atr_multiplier_sl = float(os.getenv("ATR_MULTIPLIER_SL", "2.0"))
        self.trailing_stop_enabled = os.getenv("TRAILING_STOP_ENABLED", "true").lower() in ("true", "1", "yes")
        self.trailing_stop_atr_mult = float(os.getenv("TRAILING_STOP_ATR_MULT", "1.5"))
        
        # API retry
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.retry_base_delay = float(os.getenv("RETRY_BASE_DELAY", "1.0"))
        self.api_timeout_threshold = int(os.getenv("API_TIMEOUT_THRESHOLD", "300"))
    
    def _validate(self):
        """Validate configuration and exit on critical errors."""
        errors = []
        
        # Check private key (required for both testnet and production to interact)
        if not self.private_key:
            errors.append("HYPERLIQUID_PRIVATE_KEY is required")
        
        # Validate leverage
        if self.leverage > MAX_ALLOWED_LEVERAGE:
            errors.append(f"LEVERAGE={self.leverage} exceeds maximum allowed ({MAX_ALLOWED_LEVERAGE}x)")
        
        if self.leverage < 1:
            errors.append("LEVERAGE must be at least 1")
        
        # Validate trading pairs
        if not self.trading_pairs:
            errors.append("TRADING_PAIRS cannot be empty")
        
        if len(self.trading_pairs) > 3:
            errors.append(f"TRADING_PAIRS limited to 3 maximum (got {len(self.trading_pairs)})")
        
        # Validate capital
        if self.capital_usdc < MIN_CAPITAL_USDC:
            errors.append(f"CAPITAL_USDC must be at least {MIN_CAPITAL_USDC}")
        
        # Validate risk per trade
        if not 0 < self.risk_per_trade <= 0.1:
            errors.append("RISK_PER_TRADE must be between 0 and 0.1 (10%)")
        
        # Validate EMA periods
        if self.ema_fast_period >= self.ema_slow_period:
            errors.append("EMA_FAST_PERIOD must be less than EMA_SLOW_PERIOD")
        
        # Validate timeframe
        valid_timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
        if self.timeframe not in valid_timeframes:
            errors.append(f"TIMEFRAME must be one of: {', '.join(valid_timeframes)}")
        
        if errors:
            print("\n" + "=" * 60)
            print("CONFIGURATION ERRORS:")
            print("=" * 60)
            for error in errors:
                print(f"  - {error}")
            print("=" * 60)
            print("\nPlease fix the errors in your .env file and restart.\n")
            sys.exit(1)
    
    def _show_production_warning(self):
        """Show warning and wait if running in production mode."""
        if self.environment == Environment.PRODUCTION:
            # Get wallet address from private key
            try:
                import eth_account
                pk = self.private_key.strip()
                if pk.startswith("0x"):
                    pk = pk[2:]
                wallet = eth_account.Account.from_key(pk)
                self.wallet_address = wallet.address
            except Exception:
                self.wallet_address = "Unable to derive"
            
            print("\n" + "=" * 70)
            print("  ⚠️  PRODUCTION MODE - REAL FUNDS AT RISK ⚠️")
            print("=" * 70)
            print(f"  Environment:  PRODUCTION")
            print(f"  API URL:      {self.api_url}")
            print(f"  Wallet:       {self.wallet_address}")
            print(f"  Capital:      {self.capital_usdc} USDC")
            print(f"  Leverage:     {self.leverage}x")
            print(f"  Pairs:        {', '.join(self.trading_pairs)}")
            print("=" * 70)
            print("  Starting in 5 seconds... Press Ctrl+C to abort.")
            print("=" * 70 + "\n")
            
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                print("\nAborted by user.\n")
                sys.exit(0)
    
    @property
    def api_url(self) -> str:
        """Get API URL based on environment (not configurable)."""
        return API_URLS[self.environment.value]
    
    @property
    def is_testnet(self) -> bool:
        """Check if using testnet."""
        return self.environment == Environment.TESTNET
    
    @property
    def is_production(self) -> bool:
        """Check if using production."""
        return self.environment == Environment.PRODUCTION
    
    def __repr__(self) -> str:
        """Safe string representation (hides private key)."""
        return (
            f"Config("
            f"env={self.environment.value}, "
            f"pairs={self.trading_pairs}, "
            f"leverage={self.leverage}x, "
            f"capital={self.capital_usdc} USDC, "
            f"timeframe={self.timeframe})"
        )


# Global config instance
config = Config()
