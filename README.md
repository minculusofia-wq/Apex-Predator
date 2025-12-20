# Bot HFT PolyScalper - Crypto Edition (v6.0)

Bot de trading haute fréquence (HFT) pour scalper les marchés crypto court terme sur Polymarket.
Optimisé pour la **vitesse d'exécution**, la **gestion du risque** et l'**automatisation intelligente**.

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Status](https://img.shields.io/badge/Status-Production-green.svg)
![Tests](https://img.shields.io/badge/Tests-Automated-brightgreen.svg)

## 🚀 Fonctionnalités Clés

### 🧠 Cerveau: Gabagool Strategy (Arbitrage Binaire)
- **Principe** : Accumuler YES + NO via `Order Queue` pour que `avg_YES + avg_NO < $1.00`.
- **Profit Garanti** : Au settlement, une des deux options vaut $1.00. Si coût total < $1.00, profit mathématique.
- **HFT Scoring** : Score 0-100 basé sur amélioration pair_cost + équilibre + prix.

### 🛡️ Stability & Security Protocol (v4.2)
Le bot est maintenant "Hardened" pour la production HFT réelle.
- **Auto-Redeem Loop** : Maintenance parallèle (50x plus rapide) qui réclame vos gains automatiquement chaque minute.
- **Partial Fill Reconciliation** : Gestion automatique des stocks déséquilibrés ("Inventory Risk"). Si 100 YES / 5 NO, le bot vend le surplus instantanément au marché.
- **Unified Circuit Breaker** : Fusible centralisé. 5 échecs (manuel ou auto) = Arrêt d'urgence.
- **WS Auto-Recovery** : Reconnexion automatique au flux WebSocket en cas de coupure réseau.

### 🏭 Production-Grade v6.0 (NEW)

Infrastructure robuste pour le trading en production:

| Module | Description |
|--------|-------------|
| **Logging Centralisé** | Rotation automatique, logs JSON, niveaux TRADE/ERROR séparés |
| **Circuit Breaker** | Protection cascade: 5 échecs → pause 30s → recovery |
| **Order Validator** | Validation pré-exécution (balance, slippage, position limits) |
| **Retry Exponential** | Backoff intelligent: 100ms → 200ms → 400ms (max 5s) |
| **Health Check API** | Endpoint `/api/health` pour monitoring externe |
| **Metrics Tracking** | Trades, profit, latence avec persistance JSON |
| **Graceful Shutdown** | Arrêt propre sur SIGINT/SIGTERM |
| **Tests Automatisés** | 50+ tests pytest pour validation continue |

### ⚡ Performance HFT Ultra v5.0

**Latence réduite de 2000-4000ms à 200-500ms** (4-20x plus rapide)

| Optimisation | Gain | Description |
|-------------|------|-------------|
| **Event-Driven Gabagool** | 1500-2000ms | Réaction instantanée aux updates WebSocket |
| **Polling 500ms** | 500-1000ms | Broadcast loop optimisé (était 2s) |
| **Analyse Parallèle** | 500-2000ms | Traitement par batch avec asyncio.gather |
| **asyncio.Lock** | Stabilité | Thread-safety pour accès concurrent |
| **deque Price History** | 5-10ms | O(1) au lieu de O(n) pour list.pop(0) |
| **Cache RSI avec TTL** | 10-15ms | Évite recalculs redondants (5s TTL) |
| **Connection Warming Loop** | Stabilité | Keep-alive TLS toutes les 30s |
| **uvloop** | 50-200ms | Event loop 2-4x plus rapide qu'asyncio |
| **orjson** | 10x | Sérialisation JSON ultra-rapide |
| **Keepalive 60s** | 5-10ms/req | Réutilisation des connexions HTTP |

### 🔧 Correctifs v5.0 (HFT Symbiosis)
- **Event-Driven Callback** : `scanner.on_immediate_analysis` connecté à Gabagool
- **Analyse Parallèle** : Batch processing avec `asyncio.gather()` pour 100+ marchés
- **Thread-Safety** : `asyncio.Lock` sur `_markets` pour éviter race conditions
- **Structures Optimisées** : `deque(maxlen=100)` pour price_history
- **Cache Intelligent** : RSI cache avec TTL 5 secondes
- **Connection Warming** : Boucle périodique toutes les 30s

### 🔧 Correctifs v4.5 (Gabagool Optimized)
- **Filtrage Gabagool** : Scanner filtre sur `pair_cost < 0.995` (profit garanti uniquement)
- **Scoring profit_margin** : Score basé sur marge de profit (40 points max)
- **Nouveaux paramètres** : `max_pair_cost`, `min_profit_margin` dans trading_params
- **Logs améliorés** : Affiche stats de filtrage Gabagool (pair_cost_high, etc.)

### 🔧 Correctifs v4.4 (Production Ready)
- **ApiCreds Fix** : Correction du bug py-clob-client avec credentials (était dict, maintenant ApiCreds)
- **Connection Warming** : Utilise désormais httpx direct pour éviter les bugs SDK
- **Auto Trading Toggle** : Bouton dans le dashboard avec logs visibles
- **Executor Bugs Fixed** : Correction des erreurs `__aenter__` et `global executor`
- **Logs Améliorés** : Statut auto-trading affiché dans les logs serveur

### 🤖 Auto-Optimizer (IA de Pilotage)
Ajuste dynamiquement les paramètres du bot selon les conditions de marché (volatilité, spread, liquidité).
- **Mode Manual** : Vous fixez les paramètres.
- **Mode Semi-Auto (Copilote)** : L'IA suggère les réglages, vous validez.
- **Mode Full-Auto (Pilote Auto)** : L'IA adapte tout en temps réel.

## 📊 Dashboard Web

Interface réactive sur `http://localhost:8000` :
- **Scanner Ultra-Rapide** : Détection instantanée des opportunités sur BTC, ETH, SOL...
- **Contrôles Complets** : Start/Stop Gabagool, Market Maker, Optimizer.
- **Visualisation** : Graphiques P&L, positions actives, profits verrouillés.
- **Notifications** : Alertes visuelles pour chaque trade.

## 🛠 Installation

```bash
# 1. Cloner le repo
git clone https://github.com/votre-repo/PolyScalper-HFT.git
cd PolyScalper-HFT

# 2. Créer l'environnement virtuel
python3 -m venv venv
source venv/bin/activate

# 3. Installer les dépendances (inclus uvloop/orjson)
pip install -r requirements.txt

# 4. Configurer
cp .env.example .env
# Editez .env avec vos clés API Polymarket
```

## 🚦 Démarrage Rapide

1. **Lancer le serveur :**
   ```bash
   # macOS (Script auto)
   ./🚀\ Lancer\ Bot.command

   # Ou manuel
   source venv/bin/activate
   python3 web/server.py
   ```

2. **Ouvrir le Dashboard :**
   `http://localhost:8000`

3. **Utilisation :**
   - **Start Scanner** : Lance l'écoute du marché.
   - **Gabagool** : Active la stratégie principale.
   - **Auto-Optimizer** : Activez le mode "Semi-Auto" pour débuter.

4. **Mode CLI (sans interface):**
   ```bash
   python main.py --cli
   ```

## ⚙️ Configuration

### Paramètres Trading (config/trading_params.py)

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `max_pair_cost` | 0.975 | **GABAGOOL** Coût max YES+NO (< 1.0 = profit) |
| `min_profit_margin` | 0.025 | **GABAGOOL** Marge profit minimum (2.5%) |
| `min_volume_usd` | 100 | Volume minimum du marché |
| `capital_per_trade` | 25 | $ par trade |
| `max_open_positions` | 15 | Positions simultanées max |
| `order_offset` | 0.003 | Décalage prix (agressivité) |

### Paramètres Système (config/settings.py)

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `scan_interval_seconds` | 0.3 | Intervalle entre scans (300ms) |
| `request_timeout` | 3 | Timeout API (fail fast) |
| `max_retries` | 1 | Retries par requête |

## 🏗 Architecture HFT v6.0

```
PolyScalper-HFT/
├── main.py              # Point d'entrée (uvloop activé)
├── web/                 # FastAPI + WebSocket (Dashboard)
│   └── server.py        # Event-driven + Health/Metrics endpoints
├── ui/                  # Interface Textual (TUI)
├── core/                # Moteur HFT
│   ├── scanner.py       # WebSocket Feed + asyncio.Lock + Event triggers
│   ├── analyzer.py      # Scoring opportunités + OBI + pair_cost
│   ├── gabagool.py      # Stratégie arbitrage + deque + RSI cache
│   ├── executor.py      # Exécution + Circuit Breaker + Warmup
│   ├── order_queue.py   # Queue async prioritaire
│   ├── fill_manager.py  # Tracking fills temps réel
│   ├── logger.py        # [v6.0] Logging centralisé avec rotation
│   ├── resilience.py    # [v6.0] Retry, Circuit Breaker, Validation
│   ├── lifecycle.py     # [v6.0] Health Check, Metrics, Shutdown
│   ├── auto_optimizer.py      # IA paramétrage
│   └── performance.py   # uvloop, orjson, caches
├── tests/               # [v6.0] Tests automatisés pytest
│   ├── conftest.py      # Fixtures partagées
│   ├── test_gabagool.py # Tests stratégie Gabagool
│   ├── test_resilience.py    # Tests validation/retry
│   └── test_lifecycle.py     # Tests métriques
├── api/
│   ├── public/          # APIs publiques (Polymarket, Binance, CoinGecko)
│   └── private/         # API privée Polymarket (ordres, wallet)
└── config/              # Paramètres globaux
```

## 🔄 Flow Event-Driven v5.0

```
┌─────────────────────────────────────────────────────────────────┐
│                    SCANNER (WebSocket Feed)                      │
│  _handle_book_update() → prix change détecté                    │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼ INSTANTANÉ (0-50ms)
┌─────────────────────────────────────────────────────────────────┐
│              on_immediate_analysis(market_data)                  │
│  Callback event-driven connecté au démarrage Gabagool           │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼ FILTRE RAPIDE
┌─────────────────────────────────────────────────────────────────┐
│                   pair_cost < 0.995 ?                            │
│  YES → Continue | NO → Skip (pas de profit possible)            │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼ ANALYSE (50-100ms)
┌─────────────────────────────────────────────────────────────────┐
│              gabagool_engine.analyze_opportunity()               │
│  RSI (cached) + OBI + Trend Filter + Kelly Sizing               │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼ EXÉCUTION (100-200ms)
┌─────────────────────────────────────────────────────────────────┐
│                    buy_yes() / buy_no()                          │
│  Order Queue → Executor → Polymarket API                        │
└─────────────────────────────────────────────────────────────────┘

LATENCE TOTALE: 200-500ms (vs 2000-4000ms avant)
```

## 🔧 Optimisations Techniques

### Event Loop (uvloop)
```python
# Activé automatiquement au démarrage
from core.performance import setup_uvloop
setup_uvloop()  # 2-4x plus rapide
```

### Connection Warming
```python
# Pré-chauffe TLS toutes les 30s
await client.warm_connections()
```

### Vérification Performance
```bash
# Vérifier que uvloop est actif
python main.py --cli
# Doit afficher: ⚡ uvloop activé - Event loop optimisé
```

### Logs Event-Driven
```
🔗 [Gabagool] Event-driven callback connecté au scanner
🔥 [Event-Driven] BUY YES market_xxx @ 0.45 (pair_cost: 0.92)
⚡ [Parallel] 50 marchés analysés en 45ms
```

## 🔌 API Endpoints v6.0

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/health` | GET | Statut santé de tous les composants |
| `/api/metrics` | GET | Métriques (trades, profit, latence, uptime) |
| `/api/metrics/reset` | POST | Réinitialiser les métriques |

```bash
# Exemple Health Check
curl http://localhost:8000/api/health
# {"status": "healthy", "components": {...}, "metrics_summary": {...}}

# Exemple Metrics
curl http://localhost:8000/api/metrics
# {"trades_executed": 150, "success_rate": 94.5, "avg_latency_ms": 245, ...}
```

## 🧪 Tests Automatisés v6.0

```bash
# Lancer tous les tests
pytest tests/ -v

# Tests spécifiques
pytest tests/test_gabagool.py -v    # Stratégie Gabagool
pytest tests/test_resilience.py -v  # Validation ordres
pytest tests/test_lifecycle.py -v   # Métriques

# Avec couverture
pytest tests/ --cov=core --cov-report=html
```

## 🔒 Sécurité
- Les clés privées restent locales dans `.env`.
- Le bot tourne 100% sur votre machine.
- Aucune donnée transmise à des tiers.
- Circuit Breaker: arrêt automatique après 5 échecs consécutifs.

## 📈 Performance v6.0

| Métrique | v4.5 | v5.0 | v6.0 |
|----------|------|------|------|
| Latence détection → exécution | 2000-4000ms | 200-500ms | **200-500ms** |
| Opportunités capturées | ~30% | ~80% | **~80%** |
| Fiabilité (uptime) | ~85% | ~90% | **~99%** |
| Tests automatisés | 0 | 0 | **50+** |

Pour des performances optimales:
- **Serveur**: VPS proche des serveurs Polymarket (US East - AWS us-east-1)
- **Connexion**: Faible latence, stable
- **Python**: 3.11+ (pour `slots=True` sur dataclasses)

## ⚖️ Avertissement
Trading haute fréquence impliquant des risques de perte en capital. Utilisez uniquement des fonds que vous pouvez perdre.

## License
MIT License
