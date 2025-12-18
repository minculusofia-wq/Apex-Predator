#!/bin/bash
# =============================================================================
# 🚀 HFT Scalper Bot - Debug Mode Launcher
# =============================================================================
# Lance le bot avec les logs de debug activés
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
echo "║   🚀 HFT SCALPER BOT - MODE DEBUG                            ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Créer le dossier logs si nécessaire
mkdir -p logs

# Lancer le bot en mode debug avec log file
echo -e "${GREEN}✓ Lancement en mode debug...${NC}"
echo -e "${YELLOW}📝 Logs: logs/debug.log${NC}"
echo ""
python3 main.py --debug --log-file logs/debug.log
