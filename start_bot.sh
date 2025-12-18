#!/bin/bash
# =============================================================================
# 🚀 HFT Scalper Bot - Launcher
# =============================================================================
# Lance le bot avec l'interface graphique Textual
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
echo "║   🚀 HFT SCALPER BOT - POLYMARKET                            ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Vérifier Python
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}⚠️  Python3 non trouvé. Installation requise.${NC}"
    exit 1
fi

# Vérifier les dépendances
echo -e "${GREEN}✓ Vérification des dépendances...${NC}"
python3 -c "import textual" 2>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}⚠️  Installation des dépendances...${NC}"
    pip3 install -r requirements.txt
fi

# Lancer le bot
echo -e "${GREEN}✓ Lancement du bot...${NC}"
echo ""
python3 main.py
