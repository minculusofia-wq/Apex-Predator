# Configuration des APIs Privées

## 🔐 Ce dont vous avez besoin

### 1. API Key Polymarket

Pour obtenir vos credentials API Polymarket:

1. Connectez-vous sur [Polymarket](https://polymarket.com)
2. Allez dans **Settings** > **API**
3. Créez une nouvelle API Key
4. Notez votre `API_KEY` et `API_SECRET`

> ⚠️ **Important**: Ne partagez JAMAIS votre API Secret

### 2. Wallet Polygon

Vous aurez besoin de:
- **Adresse publique** (0x...)
- **Clé privée** (sera chiffrée par le bot)
- **Fonds USDC** sur le réseau Polygon

## ⚙️ Configuration

### Option 1: Variables d'environnement

Créez un fichier `.env` à la racine du projet:

```env
POLYMARKET_API_KEY=your_api_key_here
POLYMARKET_API_SECRET=your_api_secret_here
WALLET_ADDRESS=0x...

# NE PAS mettre la clé privée dans .env !
# Elle sera demandée au démarrage et chiffrée.
```

### Option 2: Prompt sécurisé

Au premier lancement, le bot vous demandera:
1. Vos credentials API (si non présentes dans .env)
2. Votre clé privée wallet (toujours via prompt sécurisé)

La clé privée sera chiffrée avec AES-256 et sauvegardée dans `wallet.enc`.

## 🔒 Sécurité

### Ce que fait le bot:

✅ Chiffre la clé privée avec AES-256-GCM
✅ Utilise PBKDF2 pour dériver la clé de chiffrement
✅ Ne stocke JAMAIS la clé privée en clair
✅ Demande un mot de passe au démarrage pour déchiffrer
✅ Efface les données sensibles de la mémoire après utilisation

### Ce que vous devez faire:

1. Ne JAMAIS commiter le fichier `.env` dans Git
2. Ne JAMAIS partager votre `API_SECRET`
3. Utiliser un mot de passe fort pour le chiffrement du wallet
4. Garder une sauvegarde sécurisée de votre clé privée

## 📁 Fichiers sensibles (exclus de Git)

```
.env              # Variables d'environnement
wallet.enc        # Wallet chiffré
credentials.enc   # Credentials chiffrées
```

Ces fichiers sont automatiquement ignorés par `.gitignore`.
