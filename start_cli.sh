#!/bin/bash
# =============================================================================
# 🚀 HFT Scalper Bot - CLI Mode Launcher
# =============================================================================
# Lance le bot en mode ligne de commande (sans interface graphique)
# =============================================================================

# Couleurs
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Répertoire du script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                                                               ║"
echo "║   🚀 HFT SCALPER BOT - MODE CLI                              ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Vérifier Python
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}⚠️  Python3 non trouvé. Installation requise.${NC}"
    exit 1
fi

# Lancer le bot en mode CLI
echo -e "${GREEN}✓ Lancement en mode CLI...${NC}"
echo ""
python3 main.py --cli
