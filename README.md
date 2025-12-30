# APEX PREDATOR v7.2

High-frequency trading bot for Polymarket prediction markets with dual-strategy engine, Kelly Criterion sizing, and real-time auto-optimization.

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Tests](https://img.shields.io/badge/Tests-105%20passed-brightgreen.svg)
![Version](https://img.shields.io/badge/Version-7.2-purple.svg)

## Features

### Dual-Strategy Engine (v7.0)
| Strategy | Description | Edge |
|----------|-------------|------|
| **Gabagool** | YES/NO arbitrage when pair cost < $0.975 | Guaranteed profit at settlement |
| **Smart Ape** | Bitcoin Up/Down 15min momentum | Asymmetric payout exploitation |

### Kelly Criterion Sizing (v7.2)
Optimal position sizing based on statistical edge:
```
f* = (p × b - q) / b

where:
  f* = optimal fraction of capital
  p  = probability of winning
  q  = probability of losing (1 - p)
  b  = odds (avg_win / avg_loss)
```

- Per-strategy toggle (Gabagool, Smart Ape, or both)
- Configurable fractions: 1/8, 1/4, 1/2, Full Kelly
- Trade history persistence for win rate calculation

### Real-Time Auto-Optimizer (v7.2)
- **Dual-strategy optimization** - Separate logic for each strategy
- **Binance BTC integration** - Real-time price for Smart Ape decisions
- **Market condition adaptation** - Volatility-aware parameter tuning
- **3 modes**: Manual, Semi-Auto (suggestions), Full-Auto

### Production-Grade Infrastructure (v6.0)
| Module | Description |
|--------|-------------|
| **Circuit Breaker** | 5 failures → 30s pause → auto-recovery |
| **Order Validator** | Pre-execution checks (balance, slippage, limits) |
| **Retry with Backoff** | 100ms → 200ms → 400ms (max 5s) |
| **Health Check API** | `/api/health` for external monitoring |
| **Metrics Tracking** | Trades, profit, latency with JSON persistence |
| **Graceful Shutdown** | Clean exit on SIGINT/SIGTERM |

### HFT Performance (v5.0)
**Latency reduced from 2000-4000ms to 200-500ms** (4-20x faster)

| Optimization | Improvement |
|-------------|-------------|
| Event-driven Gabagool | 1500-2000ms saved |
| uvloop | 2-4x faster event loop |
| orjson | 10x faster JSON serialization |
| Connection warming | TLS keep-alive every 30s |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     APEX PREDATOR v7.2                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │   Scanner   │───▶│   Router    │───▶│  Executor   │     │
│  │ (WebSocket) │    │             │    │             │     │
│  └─────────────┘    └──────┬──────┘    └─────────────┘     │
│                            │                                │
│              ┌─────────────┼─────────────┐                  │
│              ▼             ▼             ▼                  │
│       ┌───────────┐ ┌───────────┐ ┌───────────┐            │
│       │ Gabagool  │ │ Smart Ape │ │   Kelly   │            │
│       │  Engine   │ │  Engine   │ │   Sizer   │            │
│       └───────────┘ └───────────┘ └───────────┘            │
│                            │                                │
│                     ┌──────┴──────┐                         │
│                     │    Auto     │                         │
│                     │  Optimizer  │                         │
│                     └─────────────┘                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites
- Python 3.11+
- Polymarket API credentials

### Installation

```bash
# Clone the repository
git clone https://github.com/minculusofia-wq/Apex-Predator.git
cd Apex-Predator

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API credentials
```

### Configuration

Edit `.env`:
```env
POLYMARKET_API_KEY=your_api_key
POLYMARKET_API_SECRET=your_api_secret
POLYMARKET_PASSPHRASE=your_passphrase
POLYMARKET_PRIVATE_KEY=your_private_key
WALLET_ADDRESS=your_wallet_address
```

### Run

```bash
# Start the bot
python main.py

# Or with web interface
python web/server.py

# Access dashboard
open http://localhost:8000
```

## Strategies

### Gabagool (Arbitrage)
Exploits pricing inefficiencies when YES + NO < $1.00

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_pair_cost` | 0.975 | Maximum YES+NO cost |
| `min_profit_margin` | 0.025 | Minimum profit margin (2.5%) |
| `max_duration_hours` | 4 | Focus on short-term markets |

### Smart Ape (Momentum)
Targets "Bitcoin Up or Down" 15-minute markets with asymmetric payouts

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window_minutes` | 2 | Analysis window (first minutes) |
| `dump_threshold` | 0.15 | Price dump detection (15%) |
| `min_payout_ratio` | 1.5 | Minimum payout multiplier |

## Capital Management (v7.1)

Separate capital allocation per strategy:

| Strategy | Default Capital | Default % per Trade |
|----------|----------------|---------------------|
| Gabagool | $500 | 5% ($25/trade) |
| Smart Ape | $300 | 8% ($24/trade) |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Bot status & metrics |
| `/api/health` | GET | Health check for all components |
| `/api/strategy/mode` | POST | Switch strategy (gabagool/smart_ape/both) |
| `/api/kelly/config` | GET/POST | Kelly sizing configuration |
| `/api/kelly/status` | GET | Kelly stats (win rate, edge) |
| `/api/smart-ape/start` | POST | Start Smart Ape engine |
| `/api/optimizer/start` | POST | Start auto-optimizer |
| `/api/capital/config` | GET/POST | Capital allocation settings |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=core --cov-report=html

# Specific test files
pytest tests/test_gabagool.py -v
pytest tests/test_smart_ape.py -v
pytest tests/test_resilience.py -v
```

## Project Structure

```
├── core/
│   ├── gabagool.py       # Gabagool arbitrage engine
│   ├── smart_ape.py      # Smart Ape momentum engine
│   ├── kelly.py          # Kelly Criterion sizing
│   ├── auto_optimizer.py # Dual-strategy optimization
│   ├── scanner.py        # WebSocket market scanner
│   ├── executor.py       # Order execution
│   ├── resilience.py     # Circuit breakers & retry
│   ├── lifecycle.py      # Health, metrics, shutdown
│   └── performance.py    # uvloop, orjson, caches
├── config/
│   ├── settings.py       # Environment config
│   └── trading_params.py # Trading parameters
├── web/
│   ├── server.py         # FastAPI backend
│   └── templates/        # Dashboard UI
├── api/
│   ├── public/           # Polymarket, Binance, CoinGecko
│   └── private/          # Polymarket trading API
├── tests/                # 105+ automated tests
└── main.py               # Entry point
```

## Performance Comparison

| Metric | v5.0 | v6.0 | v7.2 |
|--------|------|------|------|
| Detection → Execution | 200-500ms | 200-500ms | **200-500ms** |
| Opportunities captured | ~80% | ~80% | **~85%** |
| Uptime reliability | ~90% | ~99% | **~99%** |
| Automated tests | 0 | 50+ | **105+** |
| Strategies | 1 | 1 | **2** |
| Position sizing | Fixed | Fixed | **Kelly** |

## Disclaimer

This software is for educational purposes only. Trading prediction markets involves substantial risk of loss. Past performance does not guarantee future results. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) for details.
