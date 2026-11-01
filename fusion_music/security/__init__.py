"""安全存储子系统。"""

from .vault import CredentialVault, VaultError, vault

__all__ = ["CredentialVault", "VaultError", "vault"]
