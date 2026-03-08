"""
Trade logging module with rotating file logs.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


class TradeAction(Enum):
    """Trade action types."""
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    ENTRY = "ENTRY"
    EXIT_SL = "EXIT_SL"
    EXIT_TP = "EXIT_TP"
    EXIT_SIGNAL = "EXIT_SIGNAL"
    TRAILING_STOP_UPDATED = "TRAILING_STOP_UPDATED"
    ERROR = "ERROR"
    INFO = "INFO"


class TradeDirection(Enum):
    """Trade direction types."""
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass
class TradeLog:
    """Trade log entry."""
    timestamp: str
    action: str
    pair: str
    direction: str
    price: Optional[float] = None
    size: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: Optional[float] = None
    reason: Optional[str] = None
    details: Optional[dict] = None


class TradeLogger:
    """
    Handles trade logging with rotating files.
    Creates separate logs for trades and general bot activity.
    """
    
    def __init__(self, log_level: str = "INFO", log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup main logger
        self.logger = logging.getLogger("trading_bot")
        self.logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        self.logger.handlers.clear()
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)
        
        # File handler for general logs (rotating)
        general_log = self.log_dir / "bot.log"
        file_handler = RotatingFileHandler(
            general_log,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        self.logger.addHandler(file_handler)
        
        # Trade-specific JSON log
        self.trade_log_file = self.log_dir / "trades.jsonl"
        
        # Error log (separate)
        error_log = self.log_dir / "errors.log"
        error_handler = RotatingFileHandler(
            error_log,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_format)
        self.logger.addHandler(error_handler)
    
    def _write_trade_json(self, trade_log: TradeLog):
        """Write trade entry to JSON lines file."""
        entry = asdict(trade_log)
        with open(self.trade_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    
    def log_signal(
        self,
        pair: str,
        direction: TradeDirection,
        price: float,
        executed: bool,
        reason: str = "",
        details: Optional[dict] = None
    ):
        """Log a trading signal (whether executed or not)."""
        action = TradeAction.SIGNAL_DETECTED if not executed else TradeAction.ENTRY
        
        trade_log = TradeLog(
            timestamp=datetime.utcnow().isoformat(),
            action=action.value,
            pair=pair,
            direction=direction.value,
            price=price,
            reason=reason,
            details=details
        )
        
        status = "EXECUTED" if executed else "DETECTED"
        self.logger.info(
            f"[{status}] {pair} | {direction.value} | Price: {price:.4f} | {reason}"
        )
        self._write_trade_json(trade_log)
    
    def log_entry(
        self,
        pair: str,
        direction: TradeDirection,
        price: float,
        size: float,
        stop_loss: float,
        take_profit: float
    ):
        """Log a position entry."""
        trade_log = TradeLog(
            timestamp=datetime.utcnow().isoformat(),
            action=TradeAction.ENTRY.value,
            pair=pair,
            direction=direction.value,
            price=price,
            size=size,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        self.logger.info(
            f"ENTRY | {pair} | {direction.value} | "
            f"Size: {size:.6f} @ {price:.4f} | SL: {stop_loss:.4f} | TP: {take_profit:.4f}"
        )
        self._write_trade_json(trade_log)
    
    def log_exit(
        self,
        pair: str,
        direction: TradeDirection,
        exit_price: float,
        entry_price: float,
        size: float,
        pnl: float,
        reason: TradeAction
    ):
        """Log a position exit."""
        trade_log = TradeLog(
            timestamp=datetime.utcnow().isoformat(),
            action=reason.value,
            pair=pair,
            direction=direction.value,
            price=exit_price,
            size=size,
            entry_price=entry_price,
            pnl=pnl
        )
        
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        self.logger.info(
            f"EXIT | {pair} | {direction.value} | "
            f"Exit: {exit_price:.4f} | Entry: {entry_price:.4f} | "
            f"PnL: {pnl_str} USDC | Reason: {reason.value}"
        )
        self._write_trade_json(trade_log)
    
    def log_trailing_stop_update(
        self,
        pair: str,
        direction: TradeDirection,
        old_sl: float,
        new_sl: float,
        current_price: float
    ):
        """Log a trailing stop update."""
        trade_log = TradeLog(
            timestamp=datetime.utcnow().isoformat(),
            action=TradeAction.TRAILING_STOP_UPDATED.value,
            pair=pair,
            direction=direction.value,
            stop_loss=new_sl,
            details={"old_sl": old_sl, "current_price": current_price}
        )
        
        self.logger.info(
            f"TRAILING STOP | {pair} | {direction.value} | "
            f"SL: {old_sl:.4f} -> {new_sl:.4f} | Price: {current_price:.4f}"
        )
        self._write_trade_json(trade_log)
    
    def info(self, message: str):
        """Log info message."""
        self.logger.info(message)
    
    def warning(self, message: str):
        """Log warning message."""
        self.logger.warning(message)
    
    def error(self, message: str):
        """Log error message."""
        self.logger.error(message)
    
    def debug(self, message: str):
        """Log debug message."""
        self.logger.debug(message)
    
    def critical(self, message: str):
        """Log critical message."""
        self.logger.critical(message)


# Global logger instance (initialized in main)
trade_logger: Optional[TradeLogger] = None


def init_logger(log_level: str = "INFO", log_dir: str = "logs") -> TradeLogger:
    """Initialize the global trade logger."""
    global trade_logger
    trade_logger = TradeLogger(log_level=log_level, log_dir=log_dir)
    return trade_logger


def get_logger() -> TradeLogger:
    """Get the global trade logger."""
    if trade_logger is None:
        return init_logger()
    return trade_logger
