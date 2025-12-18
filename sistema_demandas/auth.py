# auth.py

import hashlib

def hash_password(password: str) -> str:
    """
    Gera o hash SHA256 da senha.
    
    NOTA DE MELHORIA: O SHA256 é rápido e vulnerável a ataques de força bruta.
    Para produção, considere usar uma biblioteca como `passlib` com algoritmos
    lentos e modernos como `bcrypt` ou `argon2`.
    """
    return hashlib.sha256(password.encode()).hexdigest()


def verificar_senha(senha_digitada: str, senha_hash: str) -> bool:
    """Verifica se a senha digitada corresponde ao hash armazenado."""
    return hash_password(senha_digitada) == senha_hash
