"""
Secure Wallet - Gestionnaire sécurisé du wallet Polygon

Fonctionnalités:
- Chiffrement de la clé privée avec AES-256
- Prompt sécurisé (pas d'affichage dans le terminal)
- Déchiffrement en mémoire uniquement
- Signature des transactions
- Vérification du solde
"""

import getpass
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3
from eth_account import Account

from wallet.encryption import WalletEncryption, EncryptedData
from config import get_settings


@dataclass
class WalletInfo:
    """Informations du wallet."""
    address: str
    balance_usdc: float
    balance_matic: float
    is_connected: bool


class SecureWallet:
    """
    Gestionnaire sécurisé du wallet Polygon.
    
    La clé privée est:
    - Demandée via prompt sécurisé (première fois)
    - Chiffrée avec AES-256 et sauvegardée
    - Déchiffrée en mémoire au runtime
    - Jamais stockée en clair
    
    Usage:
        wallet = SecureWallet()
        
        # Première connexion
        await wallet.connect()  # Demande la clé privée
        
        # Connexions suivantes
        await wallet.unlock("mot_de_passe")  # Déchiffre seulement
        
        # Utilisation
        info = await wallet.get_info()
        signed_tx = wallet.sign_transaction(tx_data)
    """
    
    # Adresse du contrat USDC sur Polygon
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    
    # ABI minimal pour balanceOf
    USDC_ABI = [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        }
    ]
    
    def __init__(self, wallet_file: str = "wallet.enc"):
        self.settings = get_settings()
        self._wallet_file = wallet_file
        self._encryption = WalletEncryption()
        
        # État
        self._account: Optional[Account] = None
        self._address: Optional[str] = None
        self._web3: Optional[Web3] = None
        self._is_connected = False
    
    @property
    def is_connected(self) -> bool:
        """Vérifie si le wallet est connecté."""
        return self._is_connected and self._account is not None
    
    @property
    def address(self) -> Optional[str]:
        """Retourne l'adresse du wallet."""
        return self._address
    
    @property
    def has_saved_wallet(self) -> bool:
        """Vérifie si un wallet chiffré existe."""
        return WalletEncryption.file_exists(self._wallet_file)
    
    async def connect(self) -> bool:
        """
        Connecte le wallet.
        
        Si un wallet chiffré existe, demande le mot de passe.
        Sinon, demande la clé privée et la chiffre.
        
        Returns:
            True si connecté, False sinon
        """
        try:
            # Initialiser Web3
            self._web3 = Web3(Web3.HTTPProvider(self.settings.polygon_rpc_url))
            
            if self.has_saved_wallet:
                # Wallet existant - demander le mot de passe
                return await self._unlock_existing()
            else:
                # Nouveau wallet - demander la clé privée
                return await self._setup_new_wallet()
                
        except Exception as e:
            print(f"❌ Erreur connexion wallet: {e}")
            return False
    
    async def _unlock_existing(self) -> bool:
        """Déverrouille un wallet existant."""
        print("\n" + "=" * 50)
        print("🔐 Déverrouillage du wallet")
        print("=" * 50)
        
        max_attempts = 3
        for attempt in range(max_attempts):
            password = getpass.getpass("Mot de passe: ")
            
            try:
                private_key = self._encryption.load_and_decrypt(
                    self._wallet_file,
                    password
                )
                
                if private_key:
                    return self._load_account(private_key)
                    
            except ValueError:
                remaining = max_attempts - attempt - 1
                if remaining > 0:
                    print(f"❌ Mot de passe incorrect. {remaining} tentative(s) restante(s).")
                else:
                    print("❌ Trop de tentatives échouées.")
                    return False
        
        return False
    
    async def _setup_new_wallet(self) -> bool:
        """Configure un nouveau wallet."""
        print("\n" + "=" * 50)
        print("🔐 Configuration du wallet")
        print("=" * 50)
        print("⚠️  Votre clé privée sera chiffrée et jamais stockée en clair.")
        print()
        
        # Demander la clé privée
        private_key = getpass.getpass("Clé privée (0x... ou hex): ")
        
        # Nettoyer
        private_key = private_key.strip()
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        
        # Valider
        if len(private_key) != 66:
            print("❌ Clé privée invalide (doit faire 64 caractères hex)")
            return False
        
        try:
            int(private_key, 16)
        except ValueError:
            print("❌ Clé privée invalide (caractères non-hex)")
            return False
        
        # Demander le mot de passe de chiffrement
        print("\nCréez un mot de passe pour chiffrer votre wallet.")
        
        while True:
            password = getpass.getpass("Nouveau mot de passe: ")
            
            is_valid, errors = WalletEncryption.validate_password_strength(password)
            if not is_valid:
                print("❌ Mot de passe trop faible:")
                for error in errors:
                    print(f"   - {error}")
                continue
            
            confirm = getpass.getpass("Confirmer le mot de passe: ")
            if password != confirm:
                print("❌ Les mots de passe ne correspondent pas")
                continue
            
            break
        
        # Chiffrer et sauvegarder
        try:
            self._encryption.encrypt_and_save(private_key, password, self._wallet_file)
            print(f"✅ Wallet chiffré et sauvegardé dans {self._wallet_file}")
            
            return self._load_account(private_key)
            
        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")
            return False
    
    def _load_account(self, private_key: str) -> bool:
        """Charge le compte depuis la clé privée."""
        try:
            self._account = Account.from_key(private_key)
            self._address = self._account.address
            self._is_connected = True
            
            print(f"✅ Wallet connecté: {self._address[:10]}...{self._address[-6:]}")
            return True
            
        except Exception as e:
            print(f"❌ Erreur chargement compte: {e}")
            return False
    
    async def get_info(self) -> Optional[WalletInfo]:
        """
        Récupère les informations du wallet.
        
        Returns:
            WalletInfo ou None si non connecté
        """
        if not self.is_connected or not self._web3:
            return None
        
        try:
            # Balance MATIC
            balance_wei = self._web3.eth.get_balance(self._address)
            balance_matic = float(Web3.from_wei(balance_wei, "ether"))
            
            # Balance USDC
            usdc_contract = self._web3.eth.contract(
                address=Web3.to_checksum_address(self.USDC_ADDRESS),
                abi=self.USDC_ABI
            )
            usdc_balance = usdc_contract.functions.balanceOf(self._address).call()
            balance_usdc = usdc_balance / 10**6  # USDC a 6 décimales
            
            return WalletInfo(
                address=self._address,
                balance_usdc=balance_usdc,
                balance_matic=balance_matic,
                is_connected=True
            )
            
        except Exception as e:
            return WalletInfo(
                address=self._address or "",
                balance_usdc=0,
                balance_matic=0,
                is_connected=False
            )
    
    def sign_transaction(self, transaction: dict) -> str:
        """
        Signe une transaction.
        
        Args:
            transaction: Dictionnaire de la transaction
            
        Returns:
            Transaction signée (hex)
            
        Raises:
            ValueError: Si wallet non connecté
        """
        if not self._account:
            raise ValueError("Wallet non connecté")
        
        signed = self._account.sign_transaction(transaction)
        return signed.rawTransaction.hex()
    
    def sign_message(self, message: str) -> str:
        """
        Signe un message.
        
        Args:
            message: Message à signer
            
        Returns:
            Signature (hex)
        """
        if not self._account:
            raise ValueError("Wallet non connecté")
        
        signed = self._account.sign_message(message.encode())
        return signed.signature.hex()
    
    def disconnect(self) -> None:
        """Déconnecte le wallet et efface la clé de la mémoire."""
        self._account = None
        self._address = None
        self._is_connected = False
    
    def delete_saved_wallet(self) -> bool:
        """
        Supprime le wallet chiffré sauvegardé.
        
        Returns:
            True si supprimé, False sinon
        """
        try:
            path = Path(self._wallet_file)
            if path.exists():
                path.unlink()
                return True
            return False
        except Exception:
            return False
