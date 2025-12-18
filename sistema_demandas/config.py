# config.py

import os
import pytz
from urllib.parse import urlparse
import streamlit as st

# =============================
# Fuso horário Fortaleza
# =============================
FORTALEZA_TZ = pytz.timezone("America/Fortaleza")

# =============================
# Cores para status e prioridade
# =============================
CORES_STATUS = {
    "Pendente": "#FF6B6B",
    "Em andamento": "#4ECDC4",
    "Concluída": "#06D6A0",
    "Cancelada": "#B0B0B0"
}

CORES_PRIORIDADE = {
    "Urgente": "#FF6B6B",
    "Alta": "#FF9E6D",
    "Média": "#FFD166",
    "Baixa": "#118AB2"
}

# =============================
# Funções de variáveis de ambiente
# =============================

def _env_bool(name: str, default: bool = False) -> bool:
    """Lê variável de ambiente como booleano."""
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "sim", "on")


def _env_int(name: str, default: int) -> int:
    """Lê variável de ambiente como inteiro."""
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return default


def _env_list(name: str) -> list:
    """Lê variável de ambiente como lista de strings separadas por vírgula ou ponto e vírgula."""
    raw = os.environ.get(name, "") or ""
    itens = [x.strip() for x in raw.replace(";", ",").split(",")]
    return [x for x in itens if x]

# =============================
# Configuração de Banco de Dados
# =============================

DATABASE_URL = (
    os.environ.get("DATABASE_PUBLIC_URL")
    or os.environ.get("DATABASE_URL_PUBLIC")
    or os.environ.get("DATABASE_URL")
)

def _safe_st_secrets_get(key: str, default=None):
    """Lê segredos do Streamlit de forma segura."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

def get_db_config():
    """Retorna as configurações de conexão com o PostgreSQL."""
    if DATABASE_URL:
        url = urlparse(DATABASE_URL)
        return {
            "host": url.hostname,
            "database": url.path[1:],
            "user": url.username,
            "password": url.password,
            "port": url.port or 5432,
            "sslmode": "require",
        }

    return {
        "host": os.environ.get("DB_HOST") or _safe_st_secrets_get("DB_HOST", "localhost"),
        "database": os.environ.get("DB_NAME") or _safe_st_secrets_get("DB_NAME", "railway"),
        "user": os.environ.get("DB_USER") or _safe_st_secrets_get("DB_USER", "postgres"),
        "password": os.environ.get("DB_PASSWORD") or _safe_st_secrets_get("DB_PASSWORD", ""),
        "port": int(os.environ.get("DB_PORT") or _safe_st_secrets_get("DB_PORT", 5432)),
        "sslmode": os.environ.get("DB_SSLMODE") or _safe_st_secrets_get("DB_SSLMODE", "prefer"),
    }

# =============================
# Configuração de Email
# =============================

def get_email_config() -> dict:
    """Retorna as configurações de envio de e-mail via SMTP."""
    smtp_password = (os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS") or "").strip()
    return {
        "enabled_new": _env_bool("MAIL_ON_NEW_DEMANDA", True),
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": _env_int("SMTP_PORT", 587),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": smtp_password,
        "starttls": _env_bool("SMTP_STARTTLS", True),
        "from": (os.environ.get("MAIL_FROM") or "").strip(),
        "to": _env_list("MAIL_TO"),
        "cc": _env_list("MAIL_CC"),
        "bcc": _env_list("MAIL_BCC"),
        "subject_prefix": os.environ.get("MAIL_SUBJECT_PREFIX", "Sistema de Demandas").strip(),
        "timeout": _env_int("MAIL_SEND_TIMEOUT", 20),
    }


def get_brevo_config() -> dict:
    """Retorna as configurações de envio de e-mail via Brevo API."""
    return {
        "api_key": (os.environ.get("BREVO_API_KEY") or "").strip(),
        "sender_email": (os.environ.get("BREVO_SENDER") or "").strip(),
        "sender_name": (os.environ.get("BREVO_SENDER_NAME") or "Sistema de Demandas").strip(),
        "to": _env_list("BREVO_TO") or _env_list("MAIL_TO"),
        "timeout": _env_int("BREVO_TIMEOUT", 20),
        "subject_prefix": os.environ.get("MAIL_SUBJECT_PREFIX", "Sistema de Demandas").strip(),
    }
