# Bot HFT PolyScalper - Crypto Edition (v4.3)

Bot de trading haute fréquence (HFT) pour scalper les marchés crypto court terme sur Polymarket.
Optimisé pour la **vitesse d'exécution**, la **gestion du risque** et l'**automatisation intelligente**.

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Status](https://img.shields.io/badge/Status-Production-red.svg)

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

### ⚡ Performance HFT Ultra (v4.3 - NEW)
Optimisations de latence pour trading haute fréquence:

| Optimisation | Gain | Description |
|-------------|------|-------------|
| **uvloop** | 50-200ms | Event loop 2-4x plus rapide qu'asyncio |
| **orjson** | 10x | Sérialisation JSON ultra-rapide |
| **Connection Warming** | 50-150ms | Pré-chauffe TLS au démarrage |
| **Keepalive 60s** | 5-10ms/req | Réutilisation des connexions HTTP |
| **Pre-signing Orders** | 5-10ms | Signature crypto anticipée |
| **Event-driven Triggers** | 20-50ms | Réaction instantanée aux updates WebSocket |
| **Local Orderbook** | ~100ms | Miroir O(log n) avec SortedDict |
| **Speculative Engine** | 3-5ms | Pré-calcul des ordres pour top opportunités |

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
| `min_spread` | 0.04 | Spread minimum pour trader (4 cents) |
| `max_spread` | 0.20 | Spread maximum acceptable |
| `min_volume_usd` | 20000 | Volume minimum du marché |
| `capital_per_trade` | 25 | $ par trade |
| `max_open_positions` | 15 | Positions simultanées max |
| `order_offset` | 0.003 | Décalage prix (agressivité) |

### Paramètres Système (config/settings.py)

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `scan_interval_seconds` | 0.3 | Intervalle entre scans (300ms) |
| `request_timeout` | 3 | Timeout API (fail fast) |
| `max_retries` | 1 | Retries par requête |

## 🏗 Architecture HFT

```
PolyScalper-HFT/
├── main.py              # Point d'entrée (uvloop activé)
├── web/                 # FastAPI + WebSocket (Dashboard)
├── ui/                  # Interface Textual (TUI)
├── core/                # Moteur HFT
│   ├── scanner.py       # WebSocket Feed + Event-driven triggers
│   ├── analyzer.py      # Scoring opportunités + OBI
│   ├── gabagool.py      # Stratégie arbitrage binaire
│   ├── executor.py      # Exécution + Circuit Breaker + Warmup
│   ├── order_queue.py   # Queue async prioritaire
│   ├── fill_manager.py  # Tracking fills temps réel
│   ├── speculative_engine.py  # Pre-signing ordres (NEW)
│   ├── local_orderbook.py     # Miroir orderbook O(log n) (NEW)
│   ├── auto_optimizer.py      # IA paramétrage
│   └── performance.py   # uvloop, orjson, caches
├── api/
│   ├── public/          # APIs publiques (Polymarket, Binance, CoinGecko)
│   └── private/         # API privée Polymarket (ordres, wallet)
└── config/              # Paramètres globaux
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

## 🔒 Sécurité
- Les clés privées restent locales dans `.env`.
- Le bot tourne 100% sur votre machine.
- Aucune donnée transmise à des tiers.
- Circuit Breaker: arrêt automatique après 5 échecs consécutifs.

## 📈 Performance Recommandée

Pour des performances optimales:
- **Serveur**: VPS proche des serveurs Polymarket (US East - AWS us-east-1)
- **Connexion**: Faible latence, stable
- **Python**: 3.11+ (pour `slots=True` sur dataclasses)

## ⚖️ Avertissement
Trading haute fréquence impliquant des risques de perte en capital. Utilisez uniquement des fonds que vous pouvez perdre.

## License
MIT License
