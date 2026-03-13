# Hyperliquid Trading Bot

Automated trading bot for Hyperliquid DEX perpetual futures using EMA Crossover + RSI strategy.

## Strategy

### EMA Crossover with RSI Confirmation

This bot uses a **trend-following strategy** optimized for crypto perpetual futures:

#### Entry Conditions
- **LONG**: EMA(9) crosses above EMA(21) AND RSI < 70 (not overbought)
- **SHORT**: EMA(9) crosses below EMA(21) AND RSI > 30 (not oversold)

#### Exit Conditions
- **Exit LONG**: EMA(9) crosses below EMA(21)
- **Exit SHORT**: EMA(9) crosses above EMA(21)
- Stop Loss or Take Profit hit

#### Why This Strategy?

1. **Trend Following**: Perpetual futures markets exhibit strong trending behavior. EMA crossovers capture these trends effectively.

2. **Momentum Filter (RSI)**: Prevents entry on weak crossovers by requiring RSI confirmation.

3. **ATR-Based Stops**: Adapts stop-loss to current volatility (2× ATR). Fixed percentage stops are too rigid for crypto's varying volatility.

4. **Risk/Reward**: Minimum 1:2 ratio compensates for lower win rate (~35-45%).

5. **Trailing Stops**: Lock in profits as trends develop.

#### Best Performance
- Higher timeframes (1h, 4h) with clear directional moves
- Trending markets (avoid extended consolidation)

---

## Installation

### Prerequisites
- Python 3.11+
- Docker (optional, for containerized deployment)

### Local Development

1. **Clone and setup**
```bash
git clone <your-repo>
cd trading-bot
cp .env.example .env
```

2. **Configure environment** (edit `.env`)
```bash
# REQUIRED: Your wallet private key
HYPERLIQUID_PRIVATE_KEY=your_private_key_here

# Environment: testnet (fake tokens) or production (real money)
ENVIRONMENT=testnet

# Trading configuration
TRADING_PAIRS=BTC,ETH
LEVERAGE=1
CAPITAL_USDC=1000
TIMEFRAME=1h
```

3. **Install dependencies**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

4. **Run the bot**
```bash
python main.py
```

### Docker Deployment

#### Development (testnet with hot reload)
```bash
# Build and run with ENVIRONMENT=testnet
docker-compose -f docker-compose.dev.yml up --build

# View logs
docker-compose -f docker-compose.dev.yml logs -f bot
```

#### Production (real money)
```bash
# 1. Copy and configure .env
cp .env.example .env
vim .env  # Set ENVIRONMENT=production and configure

# 2. Build and run
docker-compose -f docker-compose.prod.yml up -d --build

# 3. View logs
docker-compose -f docker-compose.prod.yml logs -f bot

# 4. Stop
docker-compose -f docker-compose.prod.yml down
```

#### Backtest (on-demand)

The backtest service runs once and stops. It doesn't start with normal `docker compose up`.

```bash
# Run backtest with default settings (BTC/USDT ETH/USDT, 15m, 365 days)
docker-compose -f docker-compose.prod.yml run backtest

# Run backtest with custom parameters
docker-compose -f docker-compose.prod.yml run backtest \
  --symbol BTC/USDT --timeframe 1h --days 180 --cash 5000

# Override via environment variables
docker-compose -f docker-compose.prod.yml run \
  -e SYMBOL=SOL/USDT -e DAYS=90 -e TIMEFRAME=4h backtest

# Results are saved to backtest/results/ as HTML files
```

**Available CLI arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `--symbol` / `-s` | BTC/USDT ETH/USDT | Trading pairs (space-separated) |
| `--timeframe` / `-t` | 15m | Candle interval |
| `--days` / `-d` | 365 | Historical data length |
| `--cash` / `-c` | 10000 | Initial capital |
| `--risk` | 0.01 | Risk per trade (0.01 = 1%) |
| `--leverage` | 1 | Leverage multiplier |
| `--no-trailing` | - | Disable trailing stops |

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HYPERLIQUID_PRIVATE_KEY` | Yes | - | Wallet private key (without 0x prefix) |
| `ENVIRONMENT` | No | `testnet` | `testnet` (fake tokens) or `production` (real money) |
| `TRADING_PAIRS` | No | `BTC,ETH` | Comma-separated trading pairs (1-3 max) |
| `LEVERAGE` | No | `1` | Leverage (1-3x max, hard limit) |
| `CAPITAL_USDC` | No | `1000` | Capital allocated to bot |
| `TIMEFRAME` | No | `1h` | Candle interval: `1m`, `5m`, `15m`, `1h`, `4h`, `1d` |
| `LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Environment Modes

| ENVIRONMENT | API URL | Tokens | Use Case |
|-------------|---------|--------|----------|
| `testnet` | `https://api.hyperliquid-testnet.xyz` | Fake | Test bot, strategy, configuration |
| `production` | `https://api.hyperliquid.xyz` | Real | Live trading with real funds |

**The API URL is automatically determined by `ENVIRONMENT` - it cannot be set manually.**

### Strategy Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `EMA_FAST_PERIOD` | `9` | Fast EMA period |
| `EMA_SLOW_PERIOD` | `21` | Slow EMA period |
| `RSI_PERIOD` | `14` | RSI calculation period |
| `RSI_OVERBOUGHT` | `70` | RSI overbought threshold |
| `RSI_OVERSOLD` | `30` | RSI oversold threshold |

### Risk Management

| Variable | Default | Description |
|----------|---------|-------------|
| `RISK_PER_TRADE` | `0.01` | Max risk per trade (1% = 0.01) |
| `REWARD_TO_RISK_RATIO` | `2.0` | Minimum R:R ratio for TP |
| `ATR_MULTIPLIER_SL` | `2.0` | ATR multiplier for stop loss |
| `TRAILING_STOP_ENABLED` | `true` | Enable trailing stops |
| `TRAILING_STOP_ATR_MULT` | `1.5` | ATR multiplier for trailing distance |

### API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_RETRIES` | `3` | API call retry attempts |
| `RETRY_BASE_DELAY` | `1.0` | Base delay for exponential backoff (seconds) |
| `API_TIMEOUT_THRESHOLD` | `300` | Pause bot if API down for X seconds |

---

## Project Structure

```
trading-bot/
├── main.py              # Main trading loop
├── strategy.py          # EMA/RSI indicators and signals
├── risk_manager.py      # Position sizing, SL/TP calculations
├── hyperliquid_client.py # API wrapper with retry logic
├── logger.py            # Trade logging with rotation
├── config.py            # Configuration management
├── .env.example         # Environment template
├── .env                 # Your configuration (not in git)
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container image
├── docker-compose.dev.yml  # Development (testnet)
├── docker-compose.prod.yml # Production (real money)
├── backtest/            # Backtest module (independent)
│   ├── run_backtest.py  # Main backtest script
│   ├── requirements.txt # Backtest dependencies
│   ├── Dockerfile       # Backtest container
│   ├── data/            # Cached OHLCV data (CSV)
│   └── results/         # HTML reports
├── logs/                # Log files (gitignored)
│   ├── bot.log          # General logs
│   ├── trades.jsonl     # Trade history (JSON Lines)
│   └── errors.log       # Error logs
└── README.md            # This file
```

---

## Logging

### Log Files

- `logs/bot.log` - General bot activity (rotating, 10MB max)
- `logs/trades.jsonl` - All trades in JSON Lines format
- `logs/errors.log` - Errors only

### Trade Log Format (trades.jsonl)

```json
{
  "timestamp": "2024-01-15T10:30:00.000Z",
  "action": "ENTRY",
  "pair": "BTC",
  "direction": "LONG",
  "price": 42000.50,
  "size": 0.025,
  "stop_loss": 41000.00,
  "take_profit": 44000.00
}
```

---

## Safety Features

1. **Leverage Limit**: Hardcoded maximum of 3x (bot refuses to start if higher)
2. **Testnet Default**: `ENVIRONMENT=testnet` by default - must explicitly set to `production` for real trading
3. **Production Warning**: When `ENVIRONMENT=production`, shows wallet address and capital, waits 5 seconds before starting
4. **Position Sizing**: Never risks more than 1% of capital per trade
5. **API Error Handling**: Retries with exponential backoff, pauses on extended outages
6. **Graceful Shutdown**: Handles SIGINT/SIGTERM properly

---

## ⚠️ Avertissement / Disclaimer

**TRADING AUTOMATIQUE = RISQUE ÉLEVÉ**

### Risques Importants

1. **Perte de capital**: Le trading de cryptomonnaies, surtout avec effet de levier, peut entraîner la perte totale de votre capital.

2. **Bugs et erreurs**: Ce bot peut contenir des bugs. Même sans bugs, les marchés peuvent évoluer de manière imprévisible.

3. **Pas de garantie de profit**: Les performances passées ne prédisent pas les résultats futurs. Ce bot peut perdre de l'argent.

4. **Risques spécifiques aux DEX**: 
   - Slippage sur les ordres market
   - Liquidations rapides sur les perps
   - Downtime API potentiel

### Avant d'utiliser ce bot en production

- [ ] Testez sur le testnet (`ENVIRONMENT=testnet`) pendant plusieurs jours
- [ ] Commencez avec un capital minimal que vous pouvez perdre
- [ ] Utilisez un levier de 1x au début
- [ ] Surveillez les logs régulièrement
- [ ] Comprenez la stratégie et ses limitations

### Vous êtes responsable

Ce logiciel est fourni "tel quel", sans aucune garantie. L'auteur n'est pas responsable des pertes financières. Utilisez-le à vos propres risques.

---

## License

MIT License - See LICENSE file for details.

---

## Resources

- [Hyperliquid Documentation](https://hyperliquid.gitbook.io/hyperliquid-docs)
- [Hyperliquid Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
- [Pandas-TA Documentation](https://github.com/twopirllc/pandas-ta)
