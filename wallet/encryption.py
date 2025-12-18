"""
Wallet Encryption - Chiffrement AES-256-GCM pour la clé privée

Sécurité:
- Chiffrement AES-256-GCM (authentifié)
- Dérivation de clé avec PBKDF2 (600,000 itérations)
- Salt aléatoire par fichier
- Nonce unique par chiffrement
- La clé privée n'est JAMAIS stockée en clair
"""

import os
import base64
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend


# Paramètres de sécurité
PBKDF2_ITERATIONS = 600_000  # Recommandation OWASP 2023
SALT_LENGTH = 32
NONCE_LENGTH = 12
KEY_LENGTH = 32  # 256 bits


@dataclass
class EncryptedData:
    """Données chiffrées avec métadonnées."""
    salt: bytes
    nonce: bytes
    ciphertext: bytes
    version: int = 1
    
    def to_dict(self) -> dict:
        """Convertit en dictionnaire pour stockage JSON."""
        return {
            "version": self.version,
            "salt": base64.b64encode(self.salt).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "EncryptedData":
        """Crée depuis un dictionnaire."""
        return cls(
            version=data.get("version", 1),
            salt=base64.b64decode(data["salt"]),
            nonce=base64.b64decode(data["nonce"]),
            ciphertext=base64.b64decode(data["ciphertext"]),
        )
    
    def save(self, filepath: str) -> None:
        """Sauvegarde dans un fichier."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)
    
    @classmethod
    def load(cls, filepath: str) -> Optional["EncryptedData"]:
        """Charge depuis un fichier."""
        path = Path(filepath)
        if not path.exists():
            return None
        with open(path, "r") as f:
            data = json.load(f)
            return cls.from_dict(data)


class WalletEncryption:
    """
    Chiffrement sécurisé pour les clés privées.
    
    Utilise AES-256-GCM avec dérivation de clé PBKDF2.
    
    Usage:
        # Chiffrement
        encryption = WalletEncryption()
        encrypted = encryption.encrypt("ma_cle_privee", "mon_mot_de_passe")
        encrypted.save("wallet.enc")
        
        # Déchiffrement
        encrypted = EncryptedData.load("wallet.enc")
        private_key = encryption.decrypt(encrypted, "mon_mot_de_passe")
    """
    
    def __init__(self):
        self._backend = default_backend()
    
    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """
        Dérive une clé de chiffrement depuis le mot de passe.
        
        Utilise PBKDF2 avec SHA-256 et 600,000 itérations.
        
        Args:
            password: Mot de passe utilisateur
            salt: Salt aléatoire
            
        Returns:
            Clé de 256 bits
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LENGTH,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
            backend=self._backend,
        )
        return kdf.derive(password.encode())
    
    def encrypt(self, plaintext: str, password: str) -> EncryptedData:
        """
        Chiffre une chaîne avec un mot de passe.
        
        Args:
            plaintext: Texte à chiffrer (clé privée)
            password: Mot de passe de chiffrement
            
        Returns:
            EncryptedData contenant les données chiffrées
        """
        # Générer salt et nonce aléatoires
        salt = os.urandom(SALT_LENGTH)
        nonce = os.urandom(NONCE_LENGTH)
        
        # Dériver la clé
        key = self._derive_key(password, salt)
        
        # Chiffrer avec AES-GCM
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
        
        return EncryptedData(
            salt=salt,
            nonce=nonce,
            ciphertext=ciphertext,
            version=1,
        )
    
    def decrypt(self, encrypted: EncryptedData, password: str) -> str:
        """
        Déchiffre des données avec un mot de passe.
        
        Args:
            encrypted: Données chiffrées
            password: Mot de passe de déchiffrement
            
        Returns:
            Texte en clair (clé privée)
            
        Raises:
            ValueError: Si le mot de passe est incorrect
        """
        try:
            # Dériver la clé
            key = self._derive_key(password, encrypted.salt)
            
            # Déchiffrer
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(encrypted.nonce, encrypted.ciphertext, None)
            
            return plaintext.decode()
            
        except Exception as e:
            raise ValueError("Mot de passe incorrect ou données corrompues") from e
    
    def encrypt_and_save(
        self,
        plaintext: str,
        password: str,
        filepath: str
    ) -> None:
        """
        Chiffre et sauvegarde dans un fichier.
        
        Args:
            plaintext: Texte à chiffrer
            password: Mot de passe
            filepath: Chemin du fichier de sortie
        """
        encrypted = self.encrypt(plaintext, password)
        encrypted.save(filepath)
    
    def load_and_decrypt(
        self,
        filepath: str,
        password: str
    ) -> Optional[str]:
        """
        Charge et déchiffre depuis un fichier.
        
        Args:
            filepath: Chemin du fichier chiffré
            password: Mot de passe
            
        Returns:
            Texte en clair ou None si fichier absent
            
        Raises:
            ValueError: Si le mot de passe est incorrect
        """
        encrypted = EncryptedData.load(filepath)
        if encrypted is None:
            return None
        
        return self.decrypt(encrypted, password)
    
    @staticmethod
    def file_exists(filepath: str) -> bool:
        """Vérifie si un fichier wallet chiffré existe."""
        return Path(filepath).exists()
    
    @staticmethod
    def validate_password_strength(password: str) -> tuple[bool, list[str]]:
        """
        Valide la force d'un mot de passe.
        
        Args:
            password: Mot de passe à valider
            
        Returns:
            Tuple (valide, liste d'erreurs)
        """
        errors = []
        
        if len(password) < 8:
            errors.append("Au moins 8 caractères requis")
        if not any(c.isupper() for c in password):
            errors.append("Au moins une majuscule requise")
        if not any(c.islower() for c in password):
            errors.append("Au moins une minuscule requise")
        if not any(c.isdigit() for c in password):
            errors.append("Au moins un chiffre requis")
        
        return len(errors) == 0, errors
