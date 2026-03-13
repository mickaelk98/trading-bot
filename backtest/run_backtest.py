#!/usr/bin/env python3
"""
Backtest module for the Hyperliquid Trading Bot strategy.

This module is completely independent from the bot code.
All strategy logic is reimplemented from scratch to match production exactly.

Strategy: EMA Crossover + RSI Confirmation + EMA200 Trend Filter
- Entry LONG: EMA(9) crosses above EMA(21) + RSI < 70 + Close > EMA(200)
- Entry SHORT: EMA(9) crosses below EMA(21) + RSI > 30 + Close < EMA(200)
- Exit on opposite crossover or SL/TP
- Trailing stop: ATR × 1.5 (only in profit)
- Position sizing: 1% risk per trade
- Fees: 0.05% taker × 2 (Hyperliquid)
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import ccxt
import numpy as np
import pandas as pd
import pandas_ta as ta
from backtesting import Backtest, Strategy
from backtesting.lib import crossover, FractionalBacktest

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class BacktestConfig:
    """Backtest configuration parameters (matching bot defaults)."""
    # Strategy parameters
    ema_fast_period: int = 9
    ema_slow_period: int = 21
    ema_trend_period: int = 200  # EMA trend filter
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    atr_period: int = 14
    
    # Risk management
    risk_per_trade: float = 0.01  # 1% of capital
    reward_to_risk_ratio: float = 2.0
    atr_multiplier_sl: float = 2.0
    trailing_stop_enabled: bool = True
    trailing_stop_atr_mult: float = 1.5
    
    # Trading costs
    commission: float = 0.0005  # 0.05% taker fee (Hyperliquid)
    
    # Initial capital
    cash: float = 10000.0
    
    # Leverage (for position sizing reference)
    leverage: int = 1


# ============================================================================
# DATA MANAGEMENT
# ============================================================================

def get_data_path(symbol: str, timeframe: str) -> Path:
    """Get the CSV file path for a symbol and timeframe."""
    script_dir = Path(__file__).parent
    return script_dir / "data" / f"{symbol}_{timeframe}.csv"


def fetch_ohlcv_from_binance(
    symbol: str,
    timeframe: str,
    days: int = 365
) -> pd.DataFrame:
    """
    Fetch OHLCV data from Binance via ccxt.
    
    Args:
        symbol: Trading pair (e.g., 'BTC/USDT')
        timeframe: Candle interval (e.g., '15m', '1h')
        days: Number of days of historical data
        
    Returns:
        DataFrame with OHLCV data
    """
    print(f"Fetching {symbol} {timeframe} data from Binance ({days} days)...")
    
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}  # Use futures for perpetual-like data
    })
    
    # Calculate since timestamp
    since = exchange.milliseconds() - (days * 24 * 60 * 60 * 1000)
    
    # Fetch all candles
    all_candles = []
    current_since = since
    
    while True:
        candles = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=current_since,
            limit=1000
        )
        
        if not candles:
            break
            
        all_candles.extend(candles)
        
        # Update since to last candle timestamp + 1
        current_since = candles[-1][0] + 1
        
        # Check if we've reached current time
        if current_since >= exchange.milliseconds():
            break
    
    # Convert to DataFrame
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    
    print(f"Fetched {len(df)} candles from {df.index[0]} to {df.index[-1]}")
    
    return df


def load_or_fetch_data(
    symbol: str,
    timeframe: str,
    days: int = 365,
    cache_hours: int = 24
) -> pd.DataFrame:
    """
    Load data from CSV cache or fetch from Binance.
    
    Args:
        symbol: Trading pair
        timeframe: Candle interval
        days: Days of history to fetch
        cache_hours: Hours before cache expires
        
    Returns:
        DataFrame with OHLCV data
    """
    csv_path = get_data_path(symbol, timeframe)
    
    # Check if cache exists and is fresh
    if csv_path.exists():
        file_mtime = datetime.fromtimestamp(csv_path.stat().st_mtime)
        cache_age = datetime.now() - file_mtime
        
        if cache_age < timedelta(hours=cache_hours):
            print(f"Loading cached data from {csv_path}")
            df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            print(f"Loaded {len(df)} candles (cache age: {cache_age})")
            return df
    
    # Fetch fresh data
    df = fetch_ohlcv_from_binance(symbol, timeframe, days)
    
    # Save to cache
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path)
    print(f"Saved data to {csv_path}")
    
    return df


def prepare_data_for_backtest(df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """
    Prepare data with all indicators needed for backtesting.
    
    Args:
        df: OHLCV DataFrame
        config: Backtest configuration
        
    Returns:
        DataFrame with indicators added
    """
    df = df.copy()
    
    # Compute EMAs
    df['ema_fast'] = ta.ema(df['Close'], length=config.ema_fast_period)
    df['ema_slow'] = ta.ema(df['Close'], length=config.ema_slow_period)
    df['ema_trend'] = ta.ema(df['Close'], length=config.ema_trend_period)
    
    # Compute RSI
    df['rsi'] = ta.rsi(df['Close'], length=config.rsi_period)
    
    # Compute ATR
    df['atr'] = ta.atr(df['High'], df['Low'], df['Close'], length=config.atr_period)
    
    # Previous values for crossover detection
    df['ema_fast_prev'] = df['ema_fast'].shift(1)
    df['ema_slow_prev'] = df['ema_slow'].shift(1)
    
    # Drop NaN rows (need enough data for all indicators)
    min_periods = max(config.ema_trend_period, config.rsi_period, config.atr_period) + 5
    df = df.dropna()
    
    return df


# ============================================================================
# STRATEGY IMPLEMENTATION
# ============================================================================

class EMACrossoverStrategy(Strategy):
    """
    EMA Crossover + RSI Confirmation + EMA200 Trend Filter Strategy.
    
    This exactly replicates the production bot logic:
    - Entry LONG: EMA(9) crosses above EMA(21) + RSI < 70 + Close > EMA(200)
    - Entry SHORT: EMA(9) crosses below EMA(21) + RSI > 30 + Close < EMA(200)
    - Exit on opposite crossover
    - Stop Loss: ATR × 2
    - Take Profit: SL × reward_ratio (default 2)
    - Trailing Stop: ATR × 1.5 (only in profit)
    """
    
    # Strategy parameters (set from outside)
    ema_fast_period = 9
    ema_slow_period = 21
    ema_trend_period = 200
    rsi_period = 14
    rsi_overbought = 70.0
    rsi_oversold = 30.0
    atr_period = 14
    risk_per_trade = 0.01
    reward_to_risk_ratio = 2.0
    atr_multiplier_sl = 2.0
    trailing_stop_enabled = True
    trailing_stop_atr_mult = 1.5
    leverage = 1
    
    def init(self):
        """Initialize strategy indicators."""
        # Get indicator series from data
        self.ema_fast = self.I(lambda: self.data.ema_fast)
        self.ema_slow = self.I(lambda: self.data.ema_slow)
        self.ema_trend = self.I(lambda: self.data.ema_trend)
        self.rsi = self.I(lambda: self.data.rsi)
        self.atr = self.I(lambda: self.data.atr)
        
        # Previous values for crossover detection
        self.ema_fast_prev = self.I(lambda: self.data.ema_fast_prev)
        self.ema_slow_prev = self.I(lambda: self.data.ema_slow_prev)
    
    def detect_bullish_crossover(self, i: int) -> bool:
        """Detect EMA fast crossing above EMA slow."""
        return (
            self.ema_fast_prev[i] <= self.ema_slow_prev[i] and
            self.ema_fast[i] > self.ema_slow[i]
        )
    
    def detect_bearish_crossover(self, i: int) -> bool:
        """Detect EMA fast crossing below EMA slow."""
        return (
            self.ema_fast_prev[i] >= self.ema_slow_prev[i] and
            self.ema_fast[i] < self.ema_slow[i]
        )
    
    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> float:
        """
        Calculate position size as a fraction of equity (0-1).
        
        backtesting.py requires size to be either:
        - A fraction of equity (0 < size < 1), OR
        - A whole number of units (size >= 1)
        
        For crypto with SL/TP, we use a conservative fraction.
        """
        risk_amount = self.equity * self.risk_per_trade
        sl_distance = abs(entry_price - stop_loss_price)
        
        if sl_distance <= 0:
            return 0.10  # Default 10% if calculation fails
        
        # Calculate how many units we can buy with our risk budget
        units = risk_amount / sl_distance
        
        # Apply leverage to units
        units = units * self.leverage
        
        # Convert to notional value
        notional_value = units * entry_price
        
        # Calculate fraction of equity
        fraction = notional_value / self.equity
        
        # Clamp to valid range - use at least 5% for meaningful trades
        # and cap at 30% to leave margin for SL/TP
        fraction = max(0.05, min(fraction, 0.30))
        
        return fraction
    
    def next(self):
        """Execute strategy logic on each candle."""
        i = len(self.data) - 1
        current_price = self.data.Close[i]
        current_atr = self.atr[i]
        current_rsi = self.rsi[i]
        current_ema_trend = self.ema_trend[i]
        
        # Skip if ATR is not available
        if np.isnan(current_atr) or current_atr <= 0:
            return
        
        # Calculate SL/TP distances
        sl_distance = current_atr * self.atr_multiplier_sl
        tp_distance = sl_distance * self.reward_to_risk_ratio
        
        # Check for crossovers
        bullish_cross = self.detect_bullish_crossover(i)
        bearish_cross = self.detect_bearish_crossover(i)
        
        # RSI conditions
        rsi_not_overbought = current_rsi < self.rsi_overbought
        rsi_not_oversold = current_rsi > self.rsi_oversold
        
        # EMA200 trend filter
        above_trend = current_price > current_ema_trend
        below_trend = current_price < current_ema_trend
        
        # Current position
        in_long = len(self.trades) > 0 and self.trades[0].is_long
        in_short = len(self.trades) > 0 and not self.trades[0].is_long
        
        # EXIT LOGIC
        if in_long and bearish_cross:
            self.position.close()
            return
        
        if in_short and bullish_cross:
            self.position.close()
            return
        
        # TRAILING STOP LOGIC
        if self.trailing_stop_enabled and len(self.trades) > 0:
            trade = self.trades[0]
            trailing_distance = current_atr * self.trailing_stop_atr_mult
            
            if trade.is_long:
                # Only trail when in profit
                if current_price > trade.entry_price:
                    new_sl = current_price - trailing_distance
                    # Only move stop up, and must be above entry
                    if trade.sl is not None and new_sl > trade.sl and new_sl > trade.entry_price:
                        trade.sl = new_sl
            else:
                # Short position
                if current_price < trade.entry_price:
                    new_sl = current_price + trailing_distance
                    # Only move stop down, and must be below entry
                    if trade.sl is not None and new_sl < trade.sl and new_sl < trade.entry_price:
                        trade.sl = new_sl
        
        # ENTRY LOGIC (only if no position)
        if len(self.trades) == 0:
            # LONG ENTRY: bullish crossover + RSI not overbought + above EMA200
            if bullish_cross and rsi_not_overbought and above_trend:
                sl_price = current_price - sl_distance
                tp_price = current_price + tp_distance
                size = self.calculate_position_size(current_price, sl_price)
                
                if size > 0:
                    self.buy(size=size)
            
            # SHORT ENTRY: bearish crossover + RSI not oversold + below EMA200
            elif bearish_cross and rsi_not_oversold and below_trend:
                sl_price = current_price + sl_distance
                tp_price = current_price - tp_distance
                size = self.calculate_position_size(current_price, sl_price)
                
                if size > 0:
                    self.sell(size=size)


# ============================================================================
# BACKTEST EXECUTION
# ============================================================================

def run_backtest(
    symbol: str,
    timeframe: str,
    days: int = 365,
    config: Optional[BacktestConfig] = None
) -> dict:
    """
    Run backtest for a single symbol.
    
    Args:
        symbol: Trading pair (e.g., 'BTC/USDT')
        timeframe: Candle interval
        days: Days of historical data
        config: Backtest configuration
        
    Returns:
        Backtest results dictionary
    """
    if config is None:
        config = BacktestConfig()
    
    print(f"\n{'='*60}")
    print(f"BACKTEST: {symbol} | {timeframe}")
    print(f"{'='*60}")
    
    # Load data
    df = load_or_fetch_data(symbol, timeframe, days)
    
    # Rename columns for backtesting.py convention
    df_bt = df.rename(columns={
        'open': 'Open',
        'high': 'High', 
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    })
    
    # Add indicators
    df_bt = prepare_data_for_backtest(df_bt, config)
    
    print(f"Data range: {df_bt.index[0]} to {df_bt.index[-1]}")
    print(f"Total candles: {len(df_bt)}")
    
    # Set strategy parameters
    EMACrossoverStrategy.ema_fast_period = config.ema_fast_period
    EMACrossoverStrategy.ema_slow_period = config.ema_slow_period
    EMACrossoverStrategy.ema_trend_period = config.ema_trend_period
    EMACrossoverStrategy.rsi_period = config.rsi_period
    EMACrossoverStrategy.rsi_overbought = config.rsi_overbought
    EMACrossoverStrategy.rsi_oversold = config.rsi_oversold
    EMACrossoverStrategy.atr_period = config.atr_period
    EMACrossoverStrategy.risk_per_trade = config.risk_per_trade
    EMACrossoverStrategy.reward_to_risk_ratio = config.reward_to_risk_ratio
    EMACrossoverStrategy.atr_multiplier_sl = config.atr_multiplier_sl
    EMACrossoverStrategy.trailing_stop_enabled = config.trailing_stop_enabled
    EMACrossoverStrategy.trailing_stop_atr_mult = config.trailing_stop_atr_mult
    EMACrossoverStrategy.leverage = config.leverage
    
    # Run backtest using FractionalBacktest for crypto (allows fractional units)
    bt = FractionalBacktest(
        df_bt,
        EMACrossoverStrategy,
        cash=config.cash,
        commission=config.commission,
        exclusive_orders=True,
        margin=1/config.leverage,  # Enable margin trading
        fractional_unit=1/1e8  # Trade in satoshis (supports fractional crypto)
    )
    
    stats = bt.run()
    
    # Generate HTML report
    script_dir = Path(__file__).parent
    results_dir = script_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    symbol_clean = symbol.replace("/", "")
    html_path = results_dir / f"{symbol_clean}_{timeframe}_{date_str}.html"
    
    bt.plot(filename=str(html_path), open_browser=False)
    
    # Print results
    print_results(stats, symbol, df_bt, config)
    
    print(f"\nHTML report saved to: {html_path}")
    
    return stats


def print_results(stats, symbol: str, df: pd.DataFrame, config: BacktestConfig):
    """Print detailed backtest results."""
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    
    # Basic stats
    print(f"\n📊 PERFORMANCE METRICS")
    print(f"   Total Return:        {stats['Return [%]']:.2f}%")
    print(f"   Buy & Hold Return:   {stats['Buy & Hold Return [%]']:.2f}%")
    print(f"   Return vs B&H:       {stats['Return [%]'] - stats['Buy & Hold Return [%]']:.2f}%")
    
    # Annualized metrics
    duration_days = (df.index[-1] - df.index[0]).days
    if duration_days > 0:
        annual_return = ((1 + stats['Return [%]']/100) ** (365/duration_days) - 1) * 100
        print(f"   Annualized Return:   {annual_return:.2f}%")
    
    print(f"\n📈 TRADE STATISTICS")
    print(f"   Total Trades:        {stats['# Trades']}")
    print(f"   Win Rate:            {stats['Win Rate [%]']:.2f}%")
    print(f"   Best Trade:          {stats['Best Trade [%]']:.2f}%")
    print(f"   Worst Trade:         {stats['Worst Trade [%]']:.2f}%")
    print(f"   Avg Trade:           {stats['Avg. Trade [%]']:.2f}%")
    print(f"   Max Trade Duration:  {stats['Max. Trade Duration']}")
    print(f"   Avg Trade Duration:  {stats['Avg. Trade Duration']}")
    
    print(f"\n💰 RISK METRICS")
    print(f"   Max Drawdown:        {stats['Max. Drawdown [%]']:.2f}%")
    print(f"   Avg. Drawdown:       {stats['Avg. Drawdown [%]']:.2f}%")
    print(f"   Sharpe Ratio:        {stats['Sharpe Ratio']:.2f}")
    print(f"   Sortino Ratio:       {stats['Sortino Ratio']:.2f}")
    print(f"   Calmar Ratio:        {stats['Calmar Ratio']:.2f}")
    
    print(f"\n💵 PROFIT FACTOR")
    print(f"   Profit Factor:       {stats['Profit Factor']:.2f}")
    print(f"   Expectancy:          {stats['Expectancy [%]']:.2f}%")
    
    print(f"\n🏦 CAPITAL")
    # Use config.cash for start capital (known value passed to Backtest)
    start_capital = config.cash
    # Calculate end capital from return percentage
    end_capital = start_capital * (1 + stats['Return [%]'] / 100)
    print(f"   Starting Capital:    ${start_capital:,.2f}")
    print(f"   Final Capital:       ${end_capital:,.2f}")
    print(f"   Peak Capital:        ${stats['Equity Peak [$]']:,.2f}")
    
    print(f"\n📅 DURATION")
    print(f"   Backtest Period:     {df.index[0].date()} to {df.index[-1].date()}")
    print(f"   Duration:            {duration_days} days")
    
    # Win/Loss breakdown
    trades = stats['_trades']
    if len(trades) > 0:
        wins = trades[trades['ReturnPct'] > 0]
        losses = trades[trades['ReturnPct'] <= 0]
        
        print(f"\n🎯 WIN/LOSS BREAKDOWN")
        print(f"   Winning Trades:      {len(wins)}")
        print(f"   Losing Trades:       {len(losses)}")
        if len(wins) > 0:
            print(f"   Avg Win:             {wins['ReturnPct'].mean():.2f}%")
            total_wins = float(wins['ReturnPct'].sum()) * start_capital / 100
            print(f"   Total Wins:          ${total_wins:,.2f}")
        if len(losses) > 0:
            print(f"   Avg Loss:            {losses['ReturnPct'].mean():.2f}%")
            total_losses = float(losses['ReturnPct'].sum()) * start_capital / 100
            print(f"   Total Losses:        ${total_losses:,.2f}")


def get_env_default(key: str, default, cast_type=str):
    """Get default value from environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        if cast_type == bool:
            return value.lower() in ('true', '1', 'yes')
        elif cast_type == list:
            return value.split()
        return cast_type(value)
    except (ValueError, TypeError):
        return default


def main():
    """Main entry point."""
    # Get defaults from environment variables (for Docker)
    env_defaults = {
        'symbol': get_env_default('SYMBOL', ['BTC/USDT', 'ETH/USDT'], list),
        'timeframe': get_env_default('TIMEFRAME', '15m', str),
        'days': get_env_default('DAYS', 365, int),
        'cash': get_env_default('CASH', 10000.0, float),
        'risk': get_env_default('RISK', 0.01, float),
        'leverage': get_env_default('LEVERAGE', 1, int),
    }
    
    parser = argparse.ArgumentParser(
        description="Backtest the Hyperliquid Trading Bot strategy"
    )
    parser.add_argument(
        '--symbol', '-s',
        type=str,
        nargs='+',
        default=env_defaults['symbol'],
        help='Trading pairs to backtest (default: BTC/USDT ETH/USDT)'
    )
    parser.add_argument(
        '--timeframe', '-t',
        type=str,
        default=env_defaults['timeframe'],
        help='Candle timeframe (default: 15m)'
    )
    parser.add_argument(
        '--days', '-d',
        type=int,
        default=env_defaults['days'],
        help='Days of historical data (default: 365)'
    )
    parser.add_argument(
        '--cash', '-c',
        type=float,
        default=env_defaults['cash'],
        help='Initial capital (default: 10000)'
    )
    parser.add_argument(
        '--risk',
        type=float,
        default=env_defaults['risk'],
        help='Risk per trade as decimal (default: 0.01 = 1%%)'
    )
    parser.add_argument(
        '--leverage',
        type=int,
        default=env_defaults['leverage'],
        help='Leverage multiplier (default: 1)'
    )
    parser.add_argument(
        '--no-trailing',
        action='store_true',
        help='Disable trailing stops'
    )
    
    args = parser.parse_args()
    
    # Create config
    config = BacktestConfig(
        cash=args.cash,
        risk_per_trade=args.risk,
        leverage=args.leverage,
        trailing_stop_enabled=not args.no_trailing
    )
    
    print("\n" + "="*60)
    print("HYPERLIQUID TRADING BOT - BACKTEST")
    print("="*60)
    print(f"\nStrategy: EMA({config.ema_fast_period})/EMA({config.ema_slow_period}) + RSI({config.rsi_period}) + EMA({config.ema_trend_period}) Trend Filter")
    print(f"Risk per trade: {config.risk_per_trade*100:.1f}%")
    print(f"Reward:Risk ratio: {config.reward_to_risk_ratio}:1")
    print(f"Stop Loss: ATR × {config.atr_multiplier_sl}")
    print(f"Take Profit: SL × {config.reward_to_risk_ratio}")
    print(f"Trailing Stop: {'Enabled' if config.trailing_stop_enabled else 'Disabled'}")
    if config.trailing_stop_enabled:
        print(f"Trailing Distance: ATR × {config.trailing_stop_atr_mult}")
    print(f"Commission: {config.commission*100:.2f}% (Hyperliquid taker)")
    print(f"Leverage: {config.leverage}x")
    print(f"Initial Capital: ${config.cash:,.2f}")
    
    # Run backtest for each symbol
    results = {}
    for symbol in args.symbol:
        try:
            stats = run_backtest(
                symbol=symbol,
                timeframe=args.timeframe,
                days=args.days,
                config=config
            )
            results[symbol] = stats
        except Exception as e:
            print(f"\n❌ Error backtesting {symbol}: {e}")
            import traceback
            traceback.print_exc()
    
    # Summary if multiple symbols
    if len(results) > 1:
        print("\n" + "="*60)
        print("MULTI-ASSET SUMMARY")
        print("="*60)
        print(f"\n{'Symbol':<15} {'Return':>12} {'Win Rate':>12} {'Trades':>10} {'Sharpe':>10}")
        print("-" * 60)
        for symbol, stats in results.items():
            print(f"{symbol:<15} {stats['Return [%]']:>11.2f}% {stats['Win Rate [%]']:>11.2f}% {stats['# Trades']:>10} {stats['Sharpe Ratio']:>10.2f}")
    
    print("\n" + "="*60)
    print("BACKTEST COMPLETE")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
