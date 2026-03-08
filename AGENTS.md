# AGENTS.md

Guidelines for AI coding agents working in this Hyperliquid Trading Bot repository.

## Project Overview

Automated trading bot for Hyperliquid DEX perpetual futures. Python 3.11+, modular architecture.

**Stack**: hyperliquid-python-sdk, pandas, pandas-ta, eth-account, python-dotenv

## Build & Run Commands

```bash
# Setup
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Run the bot
python main.py

# Syntax check
python -m py_compile main.py config.py hyperliquid_client.py strategy.py risk_manager.py logger.py

# Docker development (testnet)
docker-compose -f docker-compose.dev.yml up --build

# Docker production
docker-compose -f docker-compose.prod.yml up -d --build
```

## Testing

No test framework is currently set up. If adding tests:

```bash
# Install pytest
pip install pytest pytest-asyncio

# Run all tests
pytest

# Run single test file
pytest tests/test_strategy.py

# Run single test with verbose
pytest tests/test_strategy.py::test_ema_crossover -v

# Run with coverage
pip install pytest-cov
pytest --cov=. tests/
```

## Project Structure

```
├── main.py              # Main trading loop, bot orchestration
├── config.py            # Environment config, validation, global config instance
├── hyperliquid_client.py # API wrapper with retry logic
├── strategy.py          # EMA/RSI indicators, signal generation
├── risk_manager.py      # Position sizing, SL/TP calculations
├── logger.py            # Trade logging with file rotation
├── .env.example         # Environment variable template
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container image
├── docker-compose.dev.yml  # Dev (testnet, hot reload)
├── docker-compose.prod.yml # Prod (production)
└── logs/                # Log files (gitignored)
```

## Code Style Guidelines

### Imports

```python
# Standard library first
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any

# Third-party second
import numpy as np
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv

# Local imports last
from config import Config, config
from logger import TradeLogger, get_logger
```

### Formatting

- **Indentation**: 4 spaces
- **Line length**: ~100 characters (flexible)
- **Quotes**: Double quotes for strings, single quotes inside f-strings if needed
- **Blank lines**: 2 blank lines between classes/functions at module level, 1 inside classes

### Type Hints

Always use type hints for function signatures and class attributes:

```python
from typing import Optional, List, Tuple, Dict

def calculate_position_size(
    self,
    entry_price: float,
    stop_loss_price: float,
    signal: Signal
) -> PositionSize:
    ...

@dataclass
class Position:
    coin: str
    size: float
    entry_price: float
    stop_loss: Optional[float] = None
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Modules | snake_case | `risk_manager.py` |
| Classes | PascalCase | `TradingBot`, `RiskManager` |
| Functions | snake_case | `calculate_sl_tp()` |
| Methods | snake_case | `_process_entry_signal()` |
| Private methods | _leading_underscore | `_validate_config()` |
| Constants | UPPER_SNAKE_CASE | `MAX_ALLOWED_LEVERAGE` |
| Variables | snake_case | `current_price`, `stop_loss` |
| Enums | PascalCase | `Signal.LONG`, `Environment.TESTNET` |

### Data Structures

Use `@dataclass` for structured data:

```python
from dataclasses import dataclass

@dataclass
class PositionSize:
    size: float
    notional_value: float
    risk_amount: float
    max_loss_percent: float
```

Use `Enum` for fixed sets of values:

```python
from enum import Enum

class Signal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    NONE = "NONE"
```

### Error Handling

- Use custom exceptions for domain errors
- Always use try/except around API calls
- Never use bare `except:` - always specify exception type
- Log errors before raising or returning

```python
class HyperliquidClientError(Exception):
    """Custom exception for Hyperliquid client errors."""
    pass

class APITimeoutError(HyperliquidClientError):
    """Raised when API is unavailable for extended period."""
    pass

# Usage
try:
    result = self._exchange.market_open(coin, is_buy, size)
except Exception as e:
    self.logger.error(f"Failed to open position: {e}")
    return False, None
```

### Logging

Use the global logger via `get_logger()`:

```python
from logger import TradeLogger, get_logger

logger = get_logger()
logger.info(f"Processing {coin}")
logger.warning(f"Trade validation failed: {error}")
logger.error(f"API call failed: {e}")
logger.debug(f"Indicators: EMA={ema:.2f}, RSI={rsi:.1f}")
```

For trade-specific logging:
```python
logger.log_entry(coin, direction, price, size, stop_loss, take_profit)
logger.log_exit(coin, direction, exit_price, entry_price, size, pnl, reason)
```

### Configuration

- Access config via the global `config` instance from `config.py`
- Never hardcode values that should be configurable
- Environment variables are loaded from `.env` via python-dotenv

```python
from config import config

# Use config values
leverage = config.leverage
pairs = config.trading_pairs
api_url = config.api_url  # Automatically set based on ENVIRONMENT
```

### Safety Constraints

These are hardcoded and must not be changed without explicit user request:

- Maximum leverage: 3x (`MAX_ALLOWED_LEVERAGE = 3`)
- Default environment: testnet (never production)
- Production mode shows warning + 5 second delay before starting

## Key Patterns

### Retry Logic with Exponential Backoff

```python
for attempt in range(self.config.max_retries):
    try:
        result = func(*args, **kwargs)
        return result
    except Exception as e:
        delay = self.config.retry_base_delay * (2 ** attempt)
        time.sleep(delay)
```

### Position Tracking

Positions are tracked in-memory for trailing stops. Real positions come from API:

```python
def _check_position_status(self, coin: str) -> Optional[str]:
    # Check tracked positions first
    if coin in self.positions:
        return self.positions[coin].direction.value
    # Then check API
    positions = self.client.get_positions()
    ...
```

### Strategy Signal Flow

1. Fetch candles → 2. Compute indicators → 3. Generate signal → 4. Validate trade → 5. Execute

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HYPERLIQUID_PRIVATE_KEY` | Yes | - | Wallet private key |
| `ENVIRONMENT` | No | `testnet` | `testnet` or `production` |
| `TRADING_PAIRS` | No | `BTC,ETH` | Comma-separated, max 3 |
| `LEVERAGE` | No | `1` | 1-3x max |
| `CAPITAL_USDC` | No | `1000` | Allocated capital |
| `TIMEFRAME` | No | `1h` | Candle interval |
| `LOG_LEVEL` | No | `INFO` | DEBUG, INFO, WARNING, ERROR |

## Common Tasks

**Add a new trading pair**: Add to `TRADING_PAIRS` in `.env`

**Change strategy parameters**: Modify in `.env` or defaults in `config.py`

**Add new indicator**: Add to `strategy.py` in `compute_indicators()` method

**Modify risk parameters**: Update `risk_manager.py` or `.env` settings

**Add new API endpoint**: Add method to `hyperliquid_client.py` with retry wrapper
