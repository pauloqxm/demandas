import streamlit as st
import pandas as pd
import json
from datetime import datetime, date, timedelta
from decimal import Decimal
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import os
from urllib.parse import urlparse
import hashlib
import pytz
import time

import socket
import ssl
import smtplib
from email.message import EmailMessage

# =============================
# Configuração da página
# =============================
st.set_page_config(
    page_title="Sistema de Demandas - GRBANABUIU",
    page_icon="🖥️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# =============================
# Fuso horário Fortaleza
# =============================
FORTALEZA_TZ = pytz.timezone("America/Fortaleza")

def agora_fortaleza() -> datetime:
    return datetime.now(FORTALEZA_TZ)

def converter_para_fortaleza(dt: datetime) -> datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(FORTALEZA_TZ)

def formatar_data_hora_fortaleza(dt: datetime, formato: str = "%d/%m/%Y %H:%M") -> str:
    if not dt:
        return ""
    return converter_para_fortaleza(dt).strftime(formato)

def _to_tz_aware_start(d: date) -> datetime:
    """00:00 do dia no fuso Fortaleza."""
    if not d:
        return None
    return FORTALEZA_TZ.localize(datetime(d.year, d.month, d.day, 0, 0, 0))

def _to_tz_aware_end_exclusive(d: date) -> datetime:
    """Dia seguinte 00:00 no fuso Fortaleza. Útil para filtro < end."""
    if not d:
        return None
    dd = d + timedelta(days=1)
    return FORTALEZA_TZ.localize(datetime(dd.year, dd.month, dd.day, 0, 0, 0))

# =============================
# Cores para status
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
# Email (variáveis de ambiente)
# =============================
# Configure no Railway:
# SMTP_HOST=smtp-relay.brevo.com
# SMTP_PORT=587
# SMTP_USER=xxxx@smtp-brevo.com
# SMTP_PASSWORD=sua_smtp_key   (fallback: SMTP_PASS)
# SMTP_STARTTLS=true
# MAIL_FROM=remetente@dominio.com
# MAIL_TO=email1@dominio.com,email2@dominio.com
# MAIL_CC=
# MAIL_BCC=
# MAIL_ON_NEW_DEMANDA=true
# MAIL_SUBJECT_PREFIX=Sistema de Demandas GRBANABUIU
# MAIL_SEND_TIMEOUT=60
# SMTP_FORCE_IPV4=true
# MAIL_RETRIES=2
# MAIL_RETRY_SLEEP=2
# SMTP_DEBUG=false

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "sim", "on")

def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return default

def _env_list(name: str) -> list:
    raw = os.environ.get(name, "") or ""
    itens = [x.strip() for x in raw.replace(";", ",").split(",")]
    return [x for x in itens if x]

def _resolve_ipv4(host: str) -> str:
    infos = socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    if not infos:
        return host
    return infos[0][4][0]

def get_email_config() -> dict:
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
        "timeout": _env_int("MAIL_SEND_TIMEOUT", 60),

        "force_ipv4": _env_bool("SMTP_FORCE_IPV4", True),
        "retries": _env_int("MAIL_RETRIES", 2),
        "retry_sleep": _env_int("MAIL_RETRY_SLEEP", 2),
        "debug": _env_bool("SMTP_DEBUG", False),
    }

def enviar_email_nova_demanda(dados_email: dict) -> tuple:
    """
    dados_email esperado:
    codigo, solicitante, departamento, local, prioridade, item, quantidade, unidade, urgencia, categoria, observacoes
    """
    cfg = get_email_config()

    if not cfg["enabled_new"]:
        return True, "Envio de email desativado por variável"
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return False, "SMTP não configurado nas variáveis"
    if not cfg["to"]:
        return False, "MAIL_TO vazio"

    codigo = dados_email.get("codigo", "SEM-COD")
    assunto = f"{cfg['subject_prefix']} | Nova demanda {codigo}"

    urg = "Sim" if bool(dados_email.get("urgencia", False)) else "Não"
    obs = dados_email.get("observacoes") or "Sem observações."
    corpo = (
        "Nova demanda registrada.\n\n"
        f"Código. {codigo}\n"
        f"Solicitante. {dados_email.get('solicitante','')}\n"
        f"Departamento. {dados_email.get('departamento','')}\n"
        f"Local. {dados_email.get('local','')}\n"
        f"Categoria. {dados_email.get('categoria','Geral')}\n"
        f"Prioridade. {dados_email.get('prioridade','')}\n"
        f"Urgente. {urg}\n"
        f"Quantidade. {dados_email.get('quantidade','')} {dados_email.get('unidade','')}\n\n"
        "Descrição.\n"
        f"{dados_email.get('item','')}\n\n"
        "Observações.\n"
        f"{obs}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = cfg["from"] or cfg["user"]
    msg["To"] = ", ".join(cfg["to"])
    if cfg["cc"]:
        msg["Cc"] = ", ".join(cfg["cc"])
    msg.set_content(corpo)

    destinos = cfg["to"] + cfg["cc"] + cfg["bcc"]

    host_to_connect = cfg["host"]
    if cfg["force_ipv4"]:
        try:
            host_to_connect = _resolve_ipv4(cfg["host"])
        except Exception:
            host_to_connect = cfg["host"]

    last_err = None

    for tentativa in range(cfg["retries"] + 1):
        try:
            socket.setdefaulttimeout(cfg["timeout"])

            with smtplib.SMTP(host_to_connect, cfg["port"], timeout=cfg["timeout"]) as server:
                if cfg["debug"]:
                    server.set_debuglevel(1)

                server.ehlo()
                if cfg["starttls"]:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()

                server.login(cfg["user"], cfg["password"])
                server.send_message(msg, from_addr=msg["From"], to_addrs=destinos)

            return True, "Email enviado"

        except Exception as e:
            last_err = e
            if tentativa < cfg["retries"]:
                time.sleep(cfg["retry_sleep"])
                continue

    return False, f"Falha ao enviar email. {str(last_err)}"

# =============================
# Conexão Railway Postgres
# =============================
DATABASE_URL = (
    os.environ.get("DATABASE_PUBLIC_URL")
    or os.environ.get("DATABASE_URL_PUBLIC")
    or os.environ.get("DATABASE_URL")
)

def _safe_st_secrets_get(key: str, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

def get_db_config():
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

@contextmanager
def get_db_connection():
    config = get_db_config()
    conn = None
    try:
        conn = psycopg2.connect(
            host=config["host"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            port=config["port"],
            sslmode=config.get("sslmode", "require"),
            connect_timeout=10,
        )
        conn.autocommit = False
        yield conn
    finally:
        if conn:
            conn.close()

def test_db_connection():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                v = cur.fetchone()
                return True, f"✅ Conectado ao PostgreSQL: {v[0]}"
    except Exception as e:
        return False, f"❌ Falha na conexão: {str(e)}"

# =============================
# Segurança e auth
# =============================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verificar_senha(senha_digitada: str, senha_hash: str) -> bool:
    return hash_password(senha_digitada) == senha_hash

# =============================
# JSON seguro
# =============================
def json_safe(obj):
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

def dumps_safe(payload) -> str:
    return json.dumps(json_safe(payload), ensure_ascii=False, default=str)

# =============================
# Migrações / init DB
# =============================
def verificar_e_atualizar_tabela_usuarios():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'usuarios'
                    );
                """)
                existe = cur.fetchone()[0]

                if not existe:
                    cur.execute("""
                        CREATE TABLE usuarios (
                            id SERIAL PRIMARY KEY,
                            nome VARCHAR(200) NOT NULL,
                            email VARCHAR(200) UNIQUE NOT NULL,
                            username VARCHAR(100) UNIQUE NOT NULL,
                            senha_hash VARCHAR(255) NOT NULL,
                            departamento VARCHAR(100),
                            nivel_acesso VARCHAR(50) DEFAULT 'usuario',
                            is_admin BOOLEAN DEFAULT FALSE,
                            ativo BOOLEAN DEFAULT TRUE,
                            data_cadastro TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            ultimo_login TIMESTAMP WITH TIME ZONE,
                            UNIQUE(username, email)
                        )
                    """)
                    conn.commit()
                    return True, "Tabela usuarios criada."

                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'usuarios'
                """)
                existentes = {r[0] for r in cur.fetchall()}

                alteracoes = []
                if "username" not in existentes:
                    alteracoes.append("ADD COLUMN username VARCHAR(100) UNIQUE")
                if "senha_hash" not in existentes:
                    alteracoes.append("ADD COLUMN senha_hash VARCHAR(255) NOT NULL DEFAULT ''")
                if "nivel_acesso" not in existentes:
                    alteracoes.append("ADD COLUMN nivel_acesso VARCHAR(50) DEFAULT 'usuario'")
                if "ativo" not in existentes:
                    alteracoes.append("ADD COLUMN ativo BOOLEAN DEFAULT TRUE")
                if "ultimo_login" not in existentes:
                    alteracoes.append("ADD COLUMN ultimo_login TIMESTAMP WITH TIME ZONE")

                for alt in alteracoes:
                    try:
                        cur.execute(f"ALTER TABLE usuarios {alt}")
                    except Exception as e:
                        st.warning(f"Aviso alterando usuarios: {str(e)}")

                if "username" not in existentes:
                    cur.execute("""
                        UPDATE usuarios
                        SET username = LOWER(REPLACE(nome, ' ', '_')) || '_' || id::text
                        WHERE username IS NULL OR username = ''
                    """)

                conn.commit()
                return True, "Tabela usuarios OK."
    except Exception as e:
        return False, f"Erro usuarios: {str(e)}"

def verificar_e_atualizar_tabela_demandas():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'demandas'
                    );
                """)
                existe = cur.fetchone()[0]
                if not existe:
                    return True, "Tabela demandas será criada."

                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'demandas'
                """)
                existentes = {r[0] for r in cur.fetchall()}

                alters = []
                if "local" not in existentes:
                    alters.append("ADD COLUMN local VARCHAR(100) DEFAULT 'Gerência'")
                if "unidade" not in existentes:
                    alters.append("ADD COLUMN unidade VARCHAR(50) DEFAULT 'Unid.'")
                if "codigo" not in existentes:
                    alters.append("ADD COLUMN codigo VARCHAR(20)")

                if "almoxarifado" not in existentes:
                    alters.append("ADD COLUMN almoxarifado BOOLEAN DEFAULT FALSE")
                if "valor" not in existentes:
                    alters.append("ADD COLUMN valor DECIMAL(12,2)")

                for alt in alters:
                    try:
                        cur.execute(f"ALTER TABLE demandas {alt}")
                    except Exception as e:
                        st.warning(f"Aviso alterando demandas: {str(e)}")

                try:
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_demandas_codigo ON demandas(codigo)")
                except Exception:
                    pass

                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_solicitante ON demandas(solicitante)")
                except Exception:
                    pass

                conn.commit()
                return True, "Tabela demandas OK."
    except Exception as e:
        return False, f"Erro demandas: {str(e)}"

def init_database():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS demandas (
                        id SERIAL PRIMARY KEY,
                        codigo VARCHAR(20),
                        item VARCHAR(500) NOT NULL,
                        quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                        solicitante VARCHAR(200) NOT NULL,
                        departamento VARCHAR(100) NOT NULL,
                        local VARCHAR(100) DEFAULT 'Gerência',
                        prioridade VARCHAR(50) NOT NULL,
                        observacoes TEXT,
                        status VARCHAR(50) DEFAULT 'Pendente',
                        data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        data_atualizacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        categoria VARCHAR(100),
                        unidade VARCHAR(50) DEFAULT 'Unid.',
                        urgencia BOOLEAN DEFAULT FALSE,
                        estimativa_horas DECIMAL(5,2),
                        almoxarifado BOOLEAN DEFAULT FALSE,
                        valor DECIMAL(12,2)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS historico_demandas (
                        id SERIAL PRIMARY KEY,
                        demanda_id INTEGER REFERENCES demandas(id) ON DELETE CASCADE,
                        usuario VARCHAR(200),
                        acao VARCHAR(100),
                        detalhes JSONB,
                        data_acao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                ok_d, msg_d = verificar_e_atualizar_tabela_demandas()
                if not ok_d:
                    conn.rollback()
                    return False, msg_d

                ok_u, msg_u = verificar_e_atualizar_tabela_usuarios()
                if not ok_u:
                    conn.rollback()
                    return False, msg_u

                try:
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_demandas_codigo ON demandas(codigo)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_status ON demandas(status)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_prioridade ON demandas(prioridade)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_data_criacao ON demandas(data_criacao DESC)")
                except Exception:
                    pass

                cur.execute("SELECT COUNT(*) FROM usuarios WHERE username = 'admin'")
                if cur.fetchone()[0] == 0:
                    admin_hash = hash_password("admin123")
                    cur.execute("""
                        INSERT INTO usuarios (nome, email, username, senha_hash, nivel_acesso, is_admin, ativo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, ("Administrador Principal", "admin@sistema.com", "admin", admin_hash, "administrador", True, True))

                conn.commit()
        return True, "✅ Banco inicializado."
    except Exception as e:
        return False, f"❌ Erro init: {str(e)}"

# =============================
# Auth usuários
# =============================
def autenticar_usuario(username, senha):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("""
                    SELECT id, nome, email, username, senha_hash,
                           nivel_acesso, is_admin, departamento, ativo
                    FROM usuarios
                    WHERE username = %s AND ativo = TRUE
                """, (username,))
                u = cur.fetchone()
                if u and verificar_senha(senha, u["senha_hash"]):
                    cur.execute("UPDATE usuarios SET ultimo_login = CURRENT_TIMESTAMP WHERE id = %s", (u["id"],))
                    conn.commit()
                    return u
                return None
    except Exception as e:
        st.error(f"Erro autenticação: {str(e)}")
        return None

def criar_usuario(dados_usuario):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("""
                    SELECT COUNT(*) FROM usuarios
                    WHERE username = %s OR email = %s
                """, (dados_usuario["username"], dados_usuario["email"]))
                if cur.fetchone()[0] > 0:
                    return False, "Username ou email já cadastrado."

                senha_hash = hash_password(dados_usuario["senha"])
                cur.execute("""
                    INSERT INTO usuarios
                    (nome, email, username, senha_hash, departamento, nivel_acesso, is_admin, ativo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    dados_usuario["nome"],
                    dados_usuario["email"],
                    dados_usuario["username"],
                    senha_hash,
                    dados_usuario.get("departamento", ""),
                    dados_usuario.get("nivel_acesso", "usuario"),
                    dados_usuario.get("is_admin", False),
                    True
                ))
                conn.commit()
                return True, "Usuário criado com sucesso."
    except Exception as e:
        return False, f"Erro criar usuário: {str(e)}"

def listar_usuarios():
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("""
                    SELECT id, nome, email, username, departamento,
                           nivel_acesso, is_admin, ativo,
                           TO_CHAR(data_cadastro AT TIME ZONE 'America/Fortaleza', 'DD/MM/YYYY') as data_cadastro,
                           TO_CHAR(ultimo_login AT TIME ZONE 'America/Fortaleza', 'DD/MM/YYYY HH24:MI') as ultimo_login
                    FROM usuarios
                    ORDER BY nome
                """)
                return cur.fetchall()
    except Exception as e:
        st.error(f"Erro listar usuários: {str(e)}")
        return []

def atualizar_usuario(usuario_id, dados_atualizados):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                campos = []
                valores = []
                for campo, valor in dados_atualizados.items():
                    if campo == "senha" and valor:
                        campos.append("senha_hash = %s")
                        valores.append(hash_password(valor))
                    elif campo != "senha" and valor is not None:
                        campos.append(f"{campo} = %s")
                        valores.append(valor)

                if not campos:
                    return False, "Nada pra atualizar."

                valores.append(usuario_id)
                cur.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = %s", valores)
                conn.commit()
                return True, "Usuário atualizado."
    except Exception as e:
        return False, f"Erro atualizar usuário: {str(e)}"

def desativar_usuario(usuario_id):
    try:
        if usuario_id == 1:
            return False, "Não dá pra desativar o admin principal."
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("UPDATE usuarios SET ativo = FALSE WHERE id = %s", (usuario_id,))
                conn.commit()
                return True, "Usuário desativado."
    except Exception as e:
        return False, f"Erro desativar usuário: {str(e)}"

# =============================
# Código ddmmaa-xx
# =============================
def gerar_codigo_demanda(cur) -> str:
    prefixo = agora_fortaleza().strftime("%d%m%y")
    cur.execute("""
        SELECT COALESCE(MAX(NULLIF(SPLIT_PART(codigo, '-', 2), '')::int), 0)
        FROM demandas
        WHERE codigo LIKE %s
    """, (f"{prefixo}-%",))
    max_seq = cur.fetchone()[0] or 0
    return f"{prefixo}-{(max_seq + 1):02d}"

def normalizar_busca_codigo(texto: str) -> str:
    if not texto:
        return ""
    s = str(texto).strip()
    s = s.replace("/", "").replace(" ", "").replace(".", "").replace("_", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:6]}-{s[6:]}"
    return s

# =============================
# Demandas
# =============================
def carregar_demandas(filtros=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                query = """
                    SELECT id, codigo, item, quantidade, solicitante, departamento,
                           local, prioridade, observacoes, status, categoria,
                           unidade, urgencia, data_criacao, data_atualizacao,
                           estimativa_horas, almoxarifado, valor
                    FROM demandas
                    WHERE 1=1
                """
                params = []

                if filtros:
                    if filtros.get("status"):
                        query += " AND status = ANY(%s)"
                        params.append(filtros["status"])

                    if filtros.get("departamento"):
                        query += " AND departamento = ANY(%s)"
                        params.append(filtros["departamento"])

                    if filtros.get("prioridade"):
                        query += " AND prioridade = ANY(%s)"
                        params.append(filtros["prioridade"])

                    if filtros.get("search"):
                        termo = filtros["search"].strip()
                        termo_codigo = normalizar_busca_codigo(termo)
                        query += """
                            AND (
                                item ILIKE %s
                                OR solicitante ILIKE %s
                                OR codigo ILIKE %s
                            )
                        """
                        params.append(f"%{termo}%")
                        params.append(f"%{termo}%")
                        params.append(f"%{termo_codigo}%")

                    if filtros.get("solicitante"):
                        query += " AND solicitante ILIKE %s"
                        params.append(f"%{filtros['solicitante']}%")

                    if filtros.get("codigo"):
                        codigo = normalizar_busca_codigo(filtros["codigo"])
                        query += " AND codigo = %s"
                        params.append(codigo)

                    dt_ini = filtros.get("data_ini")
                    dt_fim = filtros.get("data_fim")
                    if dt_ini:
                        query += " AND data_criacao >= %s"
                        params.append(dt_ini)
                    if dt_fim:
                        query += " AND data_criacao < %s"
                        params.append(dt_fim)

                query += " ORDER BY data_criacao DESC"

                cur.execute(query, params)
                demandas = cur.fetchall()

                for d in demandas:
                    d["data_criacao_formatada"] = formatar_data_hora_fortaleza(d.get("data_criacao"))
                    d["data_atualizacao_formatada"] = formatar_data_hora_fortaleza(d.get("data_atualizacao"))

                return demandas
    except Exception as e:
        st.error(f"Erro ao carregar demandas: {str(e)}")
        return []

def carregar_historico_demanda(demanda_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("""
                    SELECT id, usuario, acao, detalhes, data_acao
                    FROM historico_demandas
                    WHERE demanda_id = %s
                    ORDER BY data_acao DESC
                """, (demanda_id,))
                rows = cur.fetchall()
                for r in rows:
                    r["data_acao_formatada"] = formatar_data_hora_fortaleza(r.get("data_acao"))
                return rows
    except Exception as e:
        st.warning(f"Não foi possível carregar histórico: {str(e)}")
        return []

def adicionar_demanda(dados):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                for _ in range(8):
                    codigo = gerar_codigo_demanda(cur)
                    try:
                        cur.execute("""
                            INSERT INTO demandas
                            (codigo, item, quantidade, solicitante, departamento, local, prioridade,
                             observacoes, categoria, unidade, urgencia, estimativa_horas, almoxarifado, valor)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id, codigo
                        """, (
                            codigo,
                            dados["item"],
                            dados["quantidade"],
                            dados["solicitante"],
                            dados["departamento"],
                            dados.get("local", "Gerência"),
                            dados["prioridade"],
                            dados.get("observacoes", ""),
                            dados.get("categoria", "Geral"),
                            dados.get("unidade", "Unid."),
                            bool(dados.get("urgencia", False)),
                            None,
                            bool(dados.get("almoxarifado", False)),
                            dados.get("valor")
                        ))
                        nova_id, codigo_ok = cur.fetchone()

                        cur.execute("""
                            INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                            VALUES (%s, %s, %s, %s)
                        """, (nova_id, dados["solicitante"], "CRIAÇÃO", dumps_safe(dados)))

                        conn.commit()

                        ok_mail, msg_mail = enviar_email_nova_demanda({
                            "codigo": codigo_ok,
                            "solicitante": dados.get("solicitante", ""),
                            "departamento": dados.get("departamento", ""),
                            "local": dados.get("local", "Gerência"),
                            "prioridade": dados.get("prioridade", ""),
                            "item": dados.get("item", ""),
                            "quantidade": dados.get("quantidade", ""),
                            "unidade": dados.get("unidade", ""),
                            "urgencia": bool(dados.get("urgencia", False)),
                            "categoria": dados.get("categoria", "Geral"),
                            "observacoes": dados.get("observacoes", ""),
                        })

                        return {
                            "id": nova_id,
                            "codigo": codigo_ok,
                            "email_ok": ok_mail,
                            "email_msg": msg_mail
                        }
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        continue

        return None
    except Exception as e:
        st.error(f"Erro ao adicionar demanda: {str(e)}")
        return None

def atualizar_demanda(demanda_id: int, dados: dict):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                antigo = cur.fetchone()

                cur.execute("""
                    UPDATE demandas
                    SET status = %s,
                        almoxarifado = %s,
                        valor = %s,
                        observacoes = %s,
                        data_atualizacao = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    dados.get("status"),
                    bool(dados.get("almoxarifado", False)),
                    dados.get("valor"),
                    dados.get("observacoes", ""),
                    demanda_id
                ))

                usuario_atual = st.session_state.get("usuario_nome", "Administrador")
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    demanda_id,
                    usuario_atual,
                    "ATUALIZAÇÃO",
                    dumps_safe({"antigo": antigo or {}, "novo": dados})
                ))

                conn.commit()
                return True
    except Exception as e:
        st.error(f"Erro ao atualizar demanda: {str(e)}")
        return False

def excluir_demanda(demanda_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados = cur.fetchone()

                usuario_atual = st.session_state.get("usuario_nome", "Administrador")
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    demanda_id,
                    usuario_atual,
                    "EXCLUSÃO",
                    dumps_safe(dados or {})
                ))

                cur.execute("DELETE FROM demandas WHERE id = %s", (demanda_id,))
                conn.commit()
                return True
    except Exception as e:
        st.error(f"Erro ao excluir demanda: {str(e)}")
        return False

def obter_estatisticas(filtros=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                where = "WHERE 1=1"
                params = []

                if filtros:
                    dt_ini = filtros.get("data_ini")
                    dt_fim = filtros.get("data_fim")
                    if dt_ini:
                        where += " AND data_criacao >= %s"
                        params.append(dt_ini)
                    if dt_fim:
                        where += " AND data_criacao < %s"
                        params.append(dt_fim)

                    if filtros.get("status"):
                        where += " AND status = ANY(%s)"
                        params.append(filtros["status"])
                    if filtros.get("prioridade"):
                        where += " AND prioridade = ANY(%s)"
                        params.append(filtros["prioridade"])

                estat = {}

                cur.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        COUNT(CASE WHEN status = 'Pendente' THEN 1 END) as pendentes,
                        COUNT(CASE WHEN status = 'Em andamento' THEN 1 END) as em_andamento,
                        COUNT(CASE WHEN status = 'Concluída' THEN 1 END) as concluidas,
                        COUNT(CASE WHEN status = 'Cancelada' THEN 1 END) as canceladas,
                        COUNT(CASE WHEN urgencia = TRUE THEN 1 END) as urgentes,
                        COALESCE(SUM(quantidade), 0) as total_itens,
                        COALESCE(SUM(valor), 0) as total_valor
                    FROM demandas
                    {where}
                """, params)
                estat["totais"] = cur.fetchone() or {}

                cur.execute(f"""
                    SELECT departamento, COUNT(*) as quantidade
                    FROM demandas
                    {where}
                    GROUP BY departamento
                    ORDER BY quantidade DESC
                """, params)
                estat["por_departamento"] = {r["departamento"]: r["quantidade"] for r in cur.fetchall()}

                cur.execute(f"""
                    SELECT prioridade, COUNT(*) as quantidade
                    FROM demandas
                    {where}
                    GROUP BY prioridade
                    ORDER BY
                        CASE prioridade
                            WHEN 'Urgente' THEN 1
                            WHEN 'Alta' THEN 2
                            WHEN 'Média' THEN 3
                            ELSE 4
                        END
                """, params)
                estat["por_prioridade"] = {r["prioridade"]: r["quantidade"] for r in cur.fetchall()}

                cur.execute(f"""
                    SELECT status, COUNT(*) as quantidade
                    FROM demandas
                    {where}
                    GROUP BY status
                """, params)
                estat["por_status"] = {r["status"]: r["quantidade"] for r in cur.fetchall()}

                return estat
    except Exception as e:
        st.error(f"Erro ao obter estatísticas: {str(e)}")
        return {}

# =============================
# UI helper: formatação BR simples
# =============================
def formatar_brl(valor) -> str:
    try:
        v = float(valor)
    except Exception:
        return "R$ 0,00"
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def dataframe_to_csv_br(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig")

# =============================
# UI helper: Comprovante
# =============================
def render_comprovante_demanda(d: dict, mostrar_campos_admin: bool = False):
    cor_status = CORES_STATUS.get(d.get("status", "Pendente"), "#FF6B6B")
    cor_prioridade = CORES_PRIORIDADE.get(d.get("prioridade", "Média"), "#FFD166")

    with st.container():
        st.markdown(f"""
        <div style="
            border-left: 8px solid {cor_status};
            background: linear-gradient(90deg, #f8f9fa, #ffffff);
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 15px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        ">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0; color: #2c3e50; font-size: 1.4rem;">
                        📋 Comprovante de Demanda
                    </h3>
                    <p style="margin: 5px 0 0 0; color: #7f8c8d; font-size: 0.9rem;">
                        Código: <strong>{d.get('codigo', 'SEM-COD')}</strong> |
                        Criado em: {d.get('data_criacao_formatada', '')}
                    </p>
                </div>
                <div style="display: flex; gap: 10px;">
                    <div style="
                        background-color: {cor_status};
                        color: white;
                        padding: 5px 15px;
                        border-radius: 20px;
                        font-weight: bold;
                        font-size: 0.9rem;
                    ">
                        {d.get('status', 'Pendente')}
                    </div>
                    <div style="
                        background-color: {cor_prioridade};
                        color: #333;
                        padding: 5px 15px;
                        border-radius: 20px;
                        font-weight: bold;
                        font-size: 0.9rem;
                    ">
                        {d.get('prioridade', 'Média')}
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with st.container():
        col1, col2 = st.columns([1, 1])

        with col1:
            st.markdown("### 📄 Extrato da Demanda")

            info_grid = [
                ("Solicitante:", d.get("solicitante", "")),
                ("Departamento:", d.get("departamento", "")),
                ("Local:", d.get("local", "Gerência")),
                ("Categoria:", d.get("categoria", "Geral")),
                ("Quantidade:", f"{d.get('quantidade', 0)} {d.get('unidade', 'Unid.')}"),
                ("Urgente:", "✅ Sim" if d.get("urgencia") else "❌ Não"),
            ]

            if mostrar_campos_admin:
                info_grid.extend([
                    ("Almoxarifado:", "✅ Sim" if bool(d.get("almoxarifado")) else "❌ Não"),
                    ("Valor:", formatar_brl(d.get("valor") or 0)),
                ])

            for label, value in info_grid:
                st.markdown(f"""
                <div style="
                    display: flex;
                    justify-content: space-between;
                    padding: 8px 0;
                    border-bottom: 1px solid #eee;
                ">
                    <span style="color: #555;">{label}</span>
                    <span style="font-weight: bold; color: #2c3e50;">{value}</span>
                </div>
                """, unsafe_allow_html=True)

        with col2:
            st.markdown("### 🔗 Ações")
            codigo = d.get("codigo", "")
            if st.button("📋 Copiar Código", key=f"copy_{codigo}", use_container_width=True):
                st.session_state.copied_code = codigo
                st.toast(f"Código {codigo} copiado!", icon="📋")
                time.sleep(0.2)
                st.rerun()

            st.markdown("### 💬 Observações")
            obs = d.get("observacoes", "Sem observações.")
            st.markdown(f"""
            <div style="
                background-color: #f8f9fa;
                padding: 15px;
                border-radius: 8px;
                border-left: 4px solid #3498db;
                font-size: 0.95rem;
                line-height: 1.5;
                color: #555;
            ">
                {obs if obs else "Sem observações registradas."}
            </div>
            """, unsafe_allow_html=True)

            st.markdown("### 📝 Descrição Completa")
            item_desc = d.get("item", "")
            st.markdown(f"""
            <div style="
                background-color: #fff;
                padding: 15px;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
                font-size: 0.95rem;
                line-height: 1.5;
                color: #333;
            ">
                {item_desc}
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 📅 Histórico da Demanda")
    hist = carregar_historico_demanda(int(d["id"]))

    if not hist:
        st.info("📭 Sem histórico registrado ainda.")
    else:
        for h in hist:
            data_formatada = h.get("data_acao_formatada", "")
            usuario = h.get("usuario", "")
            acao = h.get("acao", "")

            if "CRIAÇÃO" in acao:
                cor_acao = "#2ecc71"
                icone = "🆕"
            elif "ATUALIZAÇÃO" in acao:
                cor_acao = "#3498db"
                icone = "✏️"
            elif "EXCLUSÃO" in acao:
                cor_acao = "#e74c3c"
                icone = "🗑️"
            else:
                cor_acao = "#95a5a6"
                icone = "📝"

            st.markdown(f"""
            <div style="
                display: flex;
                align-items: flex-start;
                margin-bottom: 15px;
                padding: 10px;
                background: white;
                border-radius: 8px;
                border-left: 4px solid {cor_acao};
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            ">
                <div style="margin-right: 15px; font-size: 1.2rem;">
                    {icone}
                </div>
                <div style="flex: 1;">
                    <div style="font-weight: bold; color: {cor_acao};">
                        {acao}
                    </div>
                    <div style="color: #7f8c8d; font-size: 0.9rem;">
                        Por: {usuario} | {data_formatada}
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            detalhes = h.get("detalhes")
            if detalhes:
                with st.expander("🔍 Ver detalhes", expanded=False):
                    st.json(detalhes)

    st.markdown("---")

def render_resultados_com_detalhes(demandas: list, titulo: str = "Resultados", mostrar_campos_admin: bool = False):
    st.subheader(titulo)

    if not demandas:
        st.info("📭 Nenhuma demanda encontrada.")
        return

    total_itens = sum(d.get("quantidade", 0) for d in demandas)
    total_urgentes = sum(1 for d in demandas if d.get("urgencia"))

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📋 Total de Demandas", len(demandas))
    with col2:
        st.metric("📦 Total de Itens", total_itens)
    with col3:
        st.metric("⚠️ Urgentes", total_urgentes)

    st.caption("🔍 Clique nos comprovantes abaixo para expandir e ver todos os detalhes")

    for d in demandas:
        with st.expander(
            f"📋 {d.get('codigo', 'SEM-COD')} | 👤 {d.get('solicitante', '')} | 📍 {d.get('local', '')} | 🏷️ {d.get('status', '')}",
            expanded=False
        ):
            render_comprovante_demanda(d, mostrar_campos_admin=mostrar_campos_admin)

# =============================
# Relatório Mensal (Admin)
# =============================
def render_relatorio_mensal_automatico():
    st.subheader("📅 Relatório Mensal Automático")

    hoje = agora_fortaleza().date()
    ano_padrao = hoje.year
    mes_padrao = hoje.month

    colm1, colm2, colm3 = st.columns([1, 1, 2])
    with colm1:
        ano = st.number_input("Ano", min_value=2020, max_value=2100, value=ano_padrao, step=1)
    with colm2:
        mes = st.selectbox("Mês", list(range(1, 13)), index=mes_padrao - 1)
    with colm3:
        st.caption("Esse relatório se gera sozinho conforme mês selecionado.")

    data_ini = date(int(ano), int(mes), 1)
    if mes == 12:
        data_fim = date(int(ano) + 1, 1, 1) - timedelta(days=1)
    else:
        data_fim = date(int(ano), int(mes) + 1, 1) - timedelta(days=1)

    filtros_mes = {
        "data_ini": _to_tz_aware_start(data_ini),
        "data_fim": _to_tz_aware_end_exclusive(data_fim),
    }

    est = obter_estatisticas(filtros_mes)
    totais = est.get("totais", {}) if est else {}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📋 Total", totais.get("total", 0))
    col2.metric("📦 Itens", totais.get("total_itens", 0))
    col3.metric("⚠️ Urgentes", totais.get("urgentes", 0))
    col4.metric("💰 Valor total", formatar_brl(totais.get("total_valor", 0) or 0))

    st.markdown("---")

    df_status = pd.DataFrame(list((est.get("por_status") or {}).items()), columns=["Status", "Quantidade"])
    df_prior = pd.DataFrame(list((est.get("por_prioridade") or {}).items()), columns=["Prioridade", "Quantidade"])
    df_depto = pd.DataFrame(list((est.get("por_departamento") or {}).items()), columns=["Departamento", "Quantidade"])

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🏷️ Por Status")
        if not df_status.empty:
            st.dataframe(df_status.sort_values("Quantidade", ascending=False), hide_index=True, use_container_width=True)
        else:
            st.info("Sem dados.")
    with c2:
        st.markdown("### 🚨 Por Prioridade")
        if not df_prior.empty:
            st.dataframe(df_prior.sort_values("Quantidade", ascending=False), hide_index=True, use_container_width=True)
        else:
            st.info("Sem dados.")

    st.markdown("### 🏢 Por Departamento")
    if not df_depto.empty:
        st.dataframe(df_depto.sort_values("Quantidade", ascending=False), hide_index=True, use_container_width=True)
    else:
        st.info("Sem dados.")

    df_rel = pd.DataFrame([
        {"Tipo": "Totais", "Chave": "total", "Valor": totais.get("total", 0)},
        {"Tipo": "Totais", "Chave": "total_itens", "Valor": totais.get("total_itens", 0)},
        {"Tipo": "Totais", "Chave": "urgentes", "Valor": totais.get("urgentes", 0)},
        {"Tipo": "Totais", "Chave": "total_valor", "Valor": float(totais.get("total_valor", 0) or 0)},
    ])

    for k, v in (est.get("por_status") or {}).items():
        df_rel = pd.concat([df_rel, pd.DataFrame([{"Tipo": "Status", "Chave": k, "Valor": v}])], ignore_index=True)
    for k, v in (est.get("por_prioridade") or {}).items():
        df_rel = pd.concat([df_rel, pd.DataFrame([{"Tipo": "Prioridade", "Chave": k, "Valor": v}])], ignore_index=True)
    for k, v in (est.get("por_departamento") or {}).items():
        df_rel = pd.concat([df_rel, pd.DataFrame([{"Tipo": "Departamento", "Chave": k, "Valor": v}])], ignore_index=True)

    st.markdown("---")
    st.subheader("⬇️ Exportações do mês")

    nome_arq_rel = f"relatorio_mensal_{ano}_{mes:02d}.csv"
    st.download_button(
        "⬇️ Baixar relatório mensal (CSV)",
        data=dataframe_to_csv_br(df_rel),
        file_name=nome_arq_rel,
        mime="text/csv",
        use_container_width=True
    )

    demandas_mes = carregar_demandas(filtros_mes)
    if demandas_mes:
        df_dm = pd.DataFrame(demandas_mes)
        df_dm["data_criacao"] = df_dm.get("data_criacao_formatada")
        df_dm["almoxarifado"] = df_dm["almoxarifado"].apply(lambda x: "Sim" if x else "Não")
        df_dm["valor"] = df_dm["valor"].fillna(0).astype(float)

        cols = [
            "codigo", "solicitante", "departamento", "local",
            "status", "prioridade", "quantidade", "unidade",
            "almoxarifado", "valor", "data_criacao"
        ]
        df_dm_out = df_dm[cols].copy()
        df_dm_out.rename(columns={
            "codigo": "Código",
            "solicitante": "Solicitante",
            "departamento": "Departamento",
            "local": "Local",
            "status": "Status",
            "prioridade": "Prioridade",
            "quantidade": "Quantidade",
            "unidade": "Unidade",
            "almoxarifado": "Almoxarifado",
            "valor": "Valor",
            "data_criacao": "Data Criação",
        }, inplace=True)

        nome_arq_dm = f"demandas_{ano}_{mes:02d}.csv"
        st.download_button(
            "⬇️ Baixar demandas do mês (CSV)",
            data=dataframe_to_csv_br(df_dm_out),
            file_name=nome_arq_dm,
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.info("Sem demandas no mês selecionado.")

# =============================
# Páginas
# =============================
def pagina_inicial():
    agora = agora_fortaleza()
    st.sidebar.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")

    st.markdown("""
    <div style="
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 40px 30px;
        border-radius: 15px;
        color: white;
        margin-bottom: 30px;
    ">
        <h1 style="margin: 0; font-size: 2.5rem;">🖥️ Sistema de Demandas - GRBANABUIU</h1>
        <p style="margin: 10px 0 0 0; font-size: 1.1rem; opacity: 0.9;">
            Gestão completa de solicitações e comprovantes
        </p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        <div style="
            background: white;
            padding: 25px;
            border-radius: 12px;
            border-left: 6px solid #3498db;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            height: 100%;
        ">
            <h3 style="color: #2c3e50; margin-top: 0;">📝 Solicitação e Consulta</h3>
            <p style="color: #555; line-height: 1.6;">
                Envie uma nova demanda e consulte depois usando nome ou código.
            </p>
        </div>
        """, unsafe_allow_html=True)

        if st.button("📄 Acessar Solicitação", type="primary", use_container_width=True, key="btn_solicitacao"):
            st.session_state.pagina_atual = "solicitacao"
            st.rerun()

    with col2:
        st.markdown("""
        <div style="
            background: white;
            padding: 25px;
            border-radius: 12px;
            border-left: 6px solid #9b59b6;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            height: 100%;
        ">
            <h3 style="color: #2c3e50; margin-top: 0;">🔧 Área Administrativa</h3>
            <p style="color: #555; line-height: 1.6;">
                Acesso para supervisores e administradores autorizados.
                Gestão completa de demandas, usuários e relatórios.
            </p>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🔐 Entrar como Admin", use_container_width=True, key="btn_admin"):
            st.session_state.pagina_atual = "login_admin"
            st.rerun()

    st.markdown("---")
    st.caption(f"🕒 Horário atual do sistema: {agora.strftime('%d/%m/%Y %H:%M:%S')} (Fortaleza)")

def pagina_solicitacao():
    st.header("📝 Solicitação e Consulta")
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")

    if "solicitacao_enviada" not in st.session_state:
        st.session_state.solicitacao_enviada = False
    if "ultima_demanda_codigo" not in st.session_state:
        st.session_state.ultima_demanda_codigo = None
    if "ultima_demanda_email_ok" not in st.session_state:
        st.session_state.ultima_demanda_email_ok = None
    if "ultima_demanda_email_msg" not in st.session_state:
        st.session_state.ultima_demanda_email_msg = None

    if st.session_state.solicitacao_enviada:
        st.success(f"""
        ✅ **Solicitação enviada com sucesso!**

        **Código da demanda:** `{st.session_state.ultima_demanda_codigo}`

        Guarde este código para consultar o status posteriormente.
        """)

        if st.session_state.ultima_demanda_email_ok is True:
            st.info("📧 Notificação por email enviada.")
        elif st.session_state.ultima_demanda_email_ok is False:
            st.warning(f"📧 A demanda foi registrada, mas o email falhou. {st.session_state.ultima_demanda_email_msg}")

        st.balloons()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📝 Enviar nova solicitação", use_container_width=True):
                st.session_state.solicitacao_enviada = False
                st.session_state.ultima_demanda_codigo = None
                st.session_state.ultima_demanda_email_ok = None
                st.session_state.ultima_demanda_email_msg = None
                st.rerun()
        with col2:
            if st.button("🏠 Voltar ao início", use_container_width=True):
                st.session_state.pagina_atual = "inicio"
                st.session_state.solicitacao_enviada = False
                st.session_state.ultima_demanda_codigo = None
                st.session_state.ultima_demanda_email_ok = None
                st.session_state.ultima_demanda_email_msg = None
                st.rerun()

        st.markdown("---")
        st.subheader("📋 Comprovante da Demanda Enviada")

        filtros = {"codigo": st.session_state.ultima_demanda_codigo}
        resultado = carregar_demandas(filtros)
        if resultado:
            render_comprovante_demanda(resultado[0], mostrar_campos_admin=False)

        return

    with st.container():
        st.markdown("### 📝 Nova Solicitação")
        with st.form("form_nova_demanda", clear_on_submit=True):
            col1, col2 = st.columns(2)

            with col1:
                solicitante = st.text_input("👤 Nome do Solicitante*", placeholder="Seu nome completo")
                departamento = st.selectbox(
                    "🏢 Departamento*",
                    ["Selecione", "Administrativo", "Açudes", "EB", "Gestão", "Operação", "Outro"]
                )
                local = st.selectbox(
                    "📍 Local*",
                    ["Selecione", "Banabuiú", "Capitão Mor", "Cipoada", "Fogareiro", "Gerência", "Outro", "Patu", "Pirabibu",
                     "Poço do Barro", "Quixeramobim", "São Jose I", "São Jose II", "Serafim Dias", "Trapiá II", "Umari", "Vieirão"]
                )
                categoria = st.selectbox(
                    "📂 Categoria",
                    ["Selecione", "Alimentos", "Combustível", "Equipamentos", "Ferramentas", "Lubrificantes", "Materiais", "Outro"]
                )

            with col2:
                item = st.text_area("📝 Descrição da Demanda*", placeholder="Descreva detalhadamente o que está solicitando...", height=120)
                quantidade = st.number_input("🔢 Quantidade*", min_value=1, value=1, step=1)
                unidade = st.selectbox(
                    "📏 Unidade*",
                    ["Selecione", "Kg", "Litro", "Unid.", "Metros", "m²", "m³", "Outro"]
                )

            col3, col4 = st.columns(2)
            with col3:
                prioridade = st.selectbox("🚨 Prioridade", ["Baixa", "Média", "Alta", "Urgente"], index=1)
                urgencia = st.checkbox("🚨 Marcar como URGENTE?")

            with col4:
                observacoes = st.text_area("💬 Observações Adicionais", placeholder="Informações adicionais...", height=100)

            submitted = st.form_submit_button("✅ Enviar Solicitação", type="primary", use_container_width=True)

            if submitted:
                if solicitante and item and departamento and local and unidade:
                    if departamento == "Selecione":
                        st.error("⚠️ Selecione um departamento válido.")
                    elif local == "Selecione":
                        st.error("⚠️ Selecione um local válido.")
                    elif unidade == "Selecione":
                        st.error("⚠️ Selecione uma unidade válida.")
                    else:
                        nova_demanda = {
                            "item": item,
                            "quantidade": int(quantidade),
                            "solicitante": solicitante.strip(),
                            "departamento": departamento,
                            "local": local,
                            "prioridade": prioridade,
                            "observacoes": observacoes,
                            "categoria": categoria if categoria != "Selecione" else "Geral",
                            "unidade": unidade,
                            "urgencia": bool(urgencia),
                        }

                        res = adicionar_demanda(nova_demanda)
                        if res and res.get("codigo"):
                            st.session_state.solicitacao_enviada = True
                            st.session_state.ultima_demanda_codigo = res["codigo"]
                            st.session_state.ultima_demanda_email_ok = res.get("email_ok")
                            st.session_state.ultima_demanda_email_msg = res.get("email_msg")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao salvar a solicitação. Tente novamente.")
                else:
                    st.error("⚠️ Preencha todos os campos obrigatórios (*)")

    st.markdown("---")
    st.markdown("### 🔎 Consultar Demandas")
    st.caption("Busque por nome do solicitante ou código da demanda")

    with st.expander("🔍 Abrir painel de consulta", expanded=True):
        colc1, colc2 = st.columns(2)
        with colc1:
            filtro_nome = st.text_input("Nome do solicitante", placeholder="Ex: João Silva", key="filtro_nome")
        with colc2:
            filtro_codigo = st.text_input("Código da demanda", placeholder="Ex: 141225-01", key="filtro_codigo")

        btn_consultar = st.button("🔍 Buscar Demandas", type="secondary", use_container_width=True)

        if btn_consultar:
            filtros = {}
            if filtro_nome.strip():
                filtros["solicitante"] = filtro_nome.strip()
            if filtro_codigo.strip():
                filtros["codigo"] = filtro_codigo.strip()

            if not filtros:
                st.warning("⚠️ Digite o nome do solicitante ou o código para buscar.")
            else:
                resultados = carregar_demandas(filtros)
                render_resultados_com_detalhes(resultados, "📋 Demandas Encontradas", mostrar_campos_admin=False)
        else:
            st.info("ℹ️ As últimas demandas aparecerão aqui após a busca.")

    st.markdown("---")
    if st.button("← Voltar ao Início", use_container_width=True):
        st.session_state.pagina_atual = "inicio"
        st.rerun()

def pagina_login_admin():
    st.title("🔧 Área Administrativa")
    st.markdown("---")
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")

    st.markdown("""
    <div style="
        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        padding: 25px;
        border-radius: 12px;
        color: white;
        margin-bottom: 25px;
    ">
        <h3 style="margin: 0; color: white;">🔒 Acesso Restrito</h3>
        <p style="margin: 10px 0 0 0; opacity: 0.9;">
            Esta área é exclusiva para administradores e supervisores autorizados.
        </p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_admin_login"):
            username = st.text_input("👤 Username", placeholder="Seu username")
            senha = st.text_input("🔑 Senha", type="password", placeholder="Sua senha")
            login_submit = st.form_submit_button("🔓 Entrar na Área Admin", type="primary", use_container_width=True)

            if login_submit:
                if username and senha:
                    usuario = autenticar_usuario(username, senha)
                    if usuario:
                        st.session_state.usuario_logado = True
                        st.session_state.usuario_id = usuario["id"]
                        st.session_state.usuario_nome = usuario["nome"]
                        st.session_state.usuario_username = usuario["username"]
                        st.session_state.usuario_nivel = usuario["nivel_acesso"]
                        st.session_state.usuario_admin = usuario["is_admin"]
                        st.session_state.pagina_atual = "admin"
                        st.rerun()
                    else:
                        st.error("❌ Credenciais inválidas ou usuário inativo.")
                else:
                    st.error("⚠️ Preencha todos os campos.")

    if st.button("← Voltar ao Início", use_container_width=True):
        st.session_state.pagina_atual = "inicio"
        st.rerun()

def pagina_gerenciar_usuarios():
    st.header("👥 Gerenciamento de Usuários")
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")

    if not st.session_state.get("usuario_admin", False):
        st.error("⛔ Apenas administradores.")
        return

    tab1, tab2 = st.tabs(["📋 Lista de Usuários", "➕ Novo Usuário"])

    with tab1:
        usuarios = listar_usuarios()
        if not usuarios:
            st.info("Nenhum usuário cadastrado.")
            return

        df = pd.DataFrame(usuarios)
        df["is_admin"] = df["is_admin"].apply(lambda x: "✅" if x else "❌")
        df["ativo"] = df["ativo"].apply(lambda x: "✅" if x else "❌")

        st.dataframe(
            df[["id", "nome", "username", "departamento", "nivel_acesso", "is_admin", "ativo", "ultimo_login"]],
            use_container_width=True,
            hide_index=True
        )

        st.subheader("⚙️ Ações sobre Usuários")
        op = st.selectbox(
            "Selecione um usuário para gerenciar",
            [f"{u['id']} - {u['nome']} ({u['username']})" for u in usuarios]
        )
        usuario_id = int(op.split(" - ")[0])
        info = next((u for u in usuarios if u["id"] == usuario_id), None)

        if not info:
            return

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**Nível de Acesso**")
            novo_nivel = st.selectbox(
                "Nível",
                ["usuario", "supervisor", "administrador"],
                index=["usuario", "supervisor", "administrador"].index(info["nivel_acesso"]),
                key=f"nivel_{usuario_id}"
            )
            if st.button("💾 Salvar nível", key=f"save_nivel_{usuario_id}"):
                ok, msg = atualizar_usuario(usuario_id, {"nivel_acesso": novo_nivel, "is_admin": (novo_nivel == "administrador")})
                st.success(msg) if ok else st.error(msg)
                st.rerun()

        with col2:
            st.markdown("**Alterar Senha**")
            nova_senha = st.text_input("Nova senha", type="password", key=f"senha_{usuario_id}")
            if st.button("🔐 Trocar senha", key=f"trocar_{usuario_id}"):
                if not nova_senha:
                    st.warning("Digite a nova senha.")
                else:
                    ok, msg = atualizar_usuario(usuario_id, {"senha": nova_senha})
                    st.success(msg) if ok else st.error(msg)
                    st.rerun()

        with col3:
            st.markdown("**Status do Usuário**")
            if st.button("⛔ Desativar usuário", key=f"desativar_{usuario_id}"):
                ok, msg = desativar_usuario(usuario_id)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

    with tab2:
        st.markdown("### 👤 Cadastrar Novo Usuário")
        with st.form("form_novo_usuario"):
            col1, col2 = st.columns(2)
            with col1:
                nome = st.text_input("Nome Completo*")
                email = st.text_input("Email*", placeholder="usuario@email.com")
                username = st.text_input("Username*", placeholder="nome.usuario")
            with col2:
                departamento = st.selectbox("Departamento",
                    ["Administrativo", "Gestão", "Operação", "Açudes", "EB", "TI", "RH", "Financeiro", "Outro"]
                )
                nivel_acesso = st.selectbox("Nível de Acesso", ["usuario", "supervisor", "administrador"])
                senha = st.text_input("Senha*", type="password")
                confirmar = st.text_input("Confirmar Senha*", type="password")

            criar = st.form_submit_button("✅ Criar Usuário", type="primary")

            if criar:
                if not all([nome, email, username, senha, confirmar]):
                    st.error("⚠️ Preencha todos os campos obrigatórios (*).")
                elif senha != confirmar:
                    st.error("❌ As senhas não coincidem.")
                elif "@" not in email:
                    st.error("❌ Email inválido.")
                else:
                    ok, msg = criar_usuario({
                        "nome": nome,
                        "email": email,
                        "username": username,
                        "senha": senha,
                        "departamento": departamento,
                        "nivel_acesso": nivel_acesso,
                        "is_admin": (nivel_acesso == "administrador")
                    })
                    if ok:
                        st.success(f"✅ {msg}")
                        st.balloons()
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

def pagina_admin():
    if not st.session_state.get("usuario_logado", False):
        st.session_state.pagina_atual = "login_admin"
        st.rerun()
        return

    agora = agora_fortaleza()

    st.sidebar.markdown(f"""
    <div style="
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px;
        border-radius: 10px;
        color: white;
        margin-bottom: 20px;
    ">
        <h3 style="margin: 0; font-size: 1.3rem;">🔧 Administração</h3>
        <p style="margin: 5px 0 0 0; font-size: 0.9rem; opacity: 0.9;">
        👤 {st.session_state.get('usuario_nome', 'Usuário')}<br>
        🏷️ {st.session_state.get('usuario_nivel', 'usuario').title()}
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.caption(f"🕒 {agora.strftime('%d/%m/%Y %H:%M')} (Fortaleza)")
    st.sidebar.markdown("---")

    usuario_nivel = st.session_state.get("usuario_nivel", "usuario")
    usuario_admin = st.session_state.get("usuario_admin", False)

    menu = ["🏠 Dashboard", "📋 Todas as Demandas", "✏️ Editar Demanda", "📅 Relatório Mensal", "📊 Estatísticas", "⚙️ Configurações"]
    if usuario_admin:
        menu.insert(5, "👥 Gerenciar Usuários")

    menu_sel = st.sidebar.radio("Navegação", menu)

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 Filtros Rápidos")

    status_filtro = st.sidebar.multiselect(
        "Status",
        ["Pendente", "Em andamento", "Concluída", "Cancelada"],
        default=["Pendente", "Em andamento"]
    )
    prioridade_filtro = st.sidebar.multiselect(
        "Prioridade",
        ["Urgente", "Alta", "Média", "Baixa"],
        default=["Urgente", "Alta", "Média", "Baixa"]
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📅 Filtro por período")

    hoje = agora_fortaleza().date()
    padrao_ini = hoje - timedelta(days=30)
    padrao_fim = hoje

    data_ini = st.sidebar.date_input("Data inicial", value=st.session_state.get("filtro_data_ini", padrao_ini))
    data_fim = st.sidebar.date_input("Data final", value=st.session_state.get("filtro_data_fim", padrao_fim))

    st.session_state.filtro_data_ini = data_ini
    st.session_state.filtro_data_fim = data_fim

    st.sidebar.caption("O filtro usa data de criação da demanda.")

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Logout", use_container_width=True):
        for k in ["usuario_logado", "usuario_id", "usuario_nome", "usuario_username", "usuario_nivel", "usuario_admin"]:
            st.session_state.pop(k, None)
        st.session_state.pagina_atual = "inicio"
        st.rerun()

    filtros = {}
    if status_filtro:
        filtros["status"] = status_filtro
    if prioridade_filtro:
        filtros["prioridade"] = prioridade_filtro

    if data_ini:
        filtros["data_ini"] = _to_tz_aware_start(data_ini)
    if data_fim:
        filtros["data_fim"] = _to_tz_aware_end_exclusive(data_fim)

    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M:%S')}")

    if menu_sel == "🏠 Dashboard":
        st.header("📊 Dashboard Administrativo")
        est = obter_estatisticas(filtros)
        if not est:
            st.info("📭 Sem dados disponíveis.")
            return

        totais = est.get("totais", {})

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📋 Total", totais.get("total", 0))
        col2.metric("⏳ Pendentes", totais.get("pendentes", 0), delta=f"+{totais.get('em_andamento', 0)} em andamento")
        col3.metric("⚠️ Urgentes", totais.get("urgentes", 0))
        col4.metric("💰 Valor", formatar_brl(totais.get("total_valor", 0) or 0))

        st.markdown("---")
        st.subheader("📋 Últimas Demandas (com filtro aplicado)")
        rec = carregar_demandas(filtros)[:15]
        render_resultados_com_detalhes(rec, "Últimas 15 demandas", mostrar_campos_admin=True)

    elif menu_sel == "📋 Todas as Demandas":
        st.header("📋 Todas as Demandas")

        col1, col2 = st.columns([3, 1])
        with col1:
            busca = st.text_input("🔎 Buscar por item, solicitante ou código", placeholder="Ex: material ou 141225-01 ou Maria")
        with col2:
            st.write("")
            st.write("")
            if st.button("🔍 Buscar", use_container_width=True):
                if busca.strip():
                    filtros["search"] = busca.strip()

        if busca.strip() and "search" not in filtros:
            filtros["search"] = busca.strip()

        dados = carregar_demandas(filtros)
        render_resultados_com_detalhes(dados, "Resultados da Busca", mostrar_campos_admin=True)

        st.markdown("---")
        st.subheader("📊 Tabela Geral (com filtro aplicado)")

        if not dados:
            st.info("📭 Nenhuma demanda para exibir na tabela.")
        else:
            df = pd.DataFrame(dados)

            df["Data Criação"] = df["data_criacao_formatada"]
            df["Código"] = df["codigo"]
            df["Solicitante"] = df["solicitante"]
            df["Departamento"] = df["departamento"]
            df["Local"] = df["local"]
            df["Status"] = df["status"]
            df["Prioridade"] = df["prioridade"]
            df["Quantidade"] = df["quantidade"]
            df["Unidade"] = df["unidade"]
            df["Almoxarifado"] = df["almoxarifado"].apply(lambda x: "Sim" if x else "Não")
            df["Valor (R$)"] = df["valor"].fillna(0).apply(formatar_brl)

            colunas_tabela = [
                "Código",
                "Solicitante",
                "Departamento",
                "Local",
                "Status",
                "Prioridade",
                "Quantidade",
                "Unidade",
                "Almoxarifado",
                "Valor (R$)",
                "Data Criação",
            ]

            st.dataframe(df[colunas_tabela], use_container_width=True, hide_index=True)

            csv_df = df[colunas_tabela].copy()
            csv_bytes = dataframe_to_csv_br(csv_df)
            nome_arquivo = f"demandas_filtradas_{agora_fortaleza().strftime('%Y%m%d_%H%M')}.csv"

            st.download_button(
                label="⬇️ Exportar CSV das demandas filtradas",
                data=csv_bytes,
                file_name=nome_arquivo,
                mime="text/csv",
                use_container_width=True
            )

    elif menu_sel == "✏️ Editar Demanda":
        if usuario_nivel not in ["supervisor", "administrador"]:
            st.error("⛔ Apenas supervisores e administradores podem editar demandas.")
            return

        st.header("✏️ Editar Demanda")
        st.caption("Editável somente: Status, Almoxarifado, Valor e Observações.")

        todas = carregar_demandas(filtros)
        if not todas:
            st.info("📭 Nenhuma demanda cadastrada nesse período/filtro.")
            return

        opcoes = [f"{d.get('codigo','SEM-COD')} | {d['solicitante']} | {d['item'][:50]}..." for d in todas]
        escolha = st.selectbox("Selecione uma demanda para editar", opcoes, index=0)

        if escolha:
            codigo_selecionado = escolha.split("|")[0].strip()
            demanda = next((d for d in todas if d.get("codigo") == codigo_selecionado), None)

            if not demanda:
                st.error("Demanda não encontrada.")
                return

            demanda_id = int(demanda["id"])
            st.markdown(f"**Editando demanda:** `{demanda.get('codigo', '')}`")

            with st.expander("👀 Visualizar campos bloqueados", expanded=False):
                st.text_input("Descrição", value=demanda.get("item", ""), disabled=True)
                c1, c2, c3 = st.columns(3)
                c1.text_input("Solicitante", value=demanda.get("solicitante", ""), disabled=True)
                c2.text_input("Departamento", value=demanda.get("departamento", ""), disabled=True)
                c3.text_input("Local", value=demanda.get("local", ""), disabled=True)
                c4, c5, c6 = st.columns(3)
                c4.text_input("Prioridade", value=demanda.get("prioridade", ""), disabled=True)
                c5.text_input("Quantidade", value=str(demanda.get("quantidade", "")), disabled=True)
                c6.text_input("Unidade", value=demanda.get("unidade", ""), disabled=True)

            with st.form(f"form_editar_{demanda_id}"):
                status_lista = ["Pendente", "Em andamento", "Concluída", "Cancelada"]
                st_index = status_lista.index(demanda["status"]) if demanda["status"] in status_lista else 0

                status_edit = st.selectbox("📊 Status", status_lista, index=st_index)

                almoxarifado_edit = st.selectbox(
                    "📦 Almoxarifado",
                    ["Não", "Sim"],
                    index=1 if bool(demanda.get("almoxarifado", False)) else 0
                )

                valor_edit = st.number_input(
                    "💰 Valor (R$)",
                    min_value=0.0,
                    value=float(demanda.get("valor") or 0.0),
                    step=10.0,
                    format="%.2f"
                )

                observacoes_edit = st.text_area("💬 Observações", value=demanda.get("observacoes") or "", height=120)

                col_b1, col_b2, col_b3 = st.columns(3)
                salvar = col_b1.form_submit_button("💾 Salvar Alterações", type="primary")
                excluir = col_b2.form_submit_button("🗑️ Excluir Demanda") if usuario_admin else False
                cancelar = col_b3.form_submit_button("↻ Cancelar")

                if salvar:
                    ok = atualizar_demanda(demanda_id, {
                        "status": status_edit,
                        "almoxarifado": (almoxarifado_edit == "Sim"),
                        "valor": float(valor_edit) if valor_edit and valor_edit > 0 else None,
                        "observacoes": observacoes_edit,
                    })
                    if ok:
                        st.success("✅ Demanda atualizada com sucesso!")
                        st.rerun()

                if excluir and usuario_admin:
                    if excluir_demanda(demanda_id):
                        st.warning("🗑️ Demanda excluída.")
                        st.rerun()

                if cancelar:
                    st.rerun()

            st.markdown("---")
            st.subheader("📋 Prévia do Comprovante (Admin)")
            atualizado = carregar_demandas({"codigo": demanda.get("codigo")})
            if atualizado:
                render_comprovante_demanda(atualizado[0], mostrar_campos_admin=True)
            else:
                render_comprovante_demanda(demanda, mostrar_campos_admin=True)

    elif menu_sel == "📅 Relatório Mensal":
        render_relatorio_mensal_automatico()

    elif menu_sel == "👥 Gerenciar Usuários":
        pagina_gerenciar_usuarios()

    elif menu_sel == "📊 Estatísticas":
        st.header("📊 Estatísticas Avançadas (com filtro aplicado)")
        est = obter_estatisticas(filtros)

        if not est:
            st.info("📭 Sem dados disponíveis para análise.")
            return

        totais = est.get("totais", {})
        st.metric("💰 Total de valores", formatar_brl(totais.get("total_valor", 0) or 0))

        col1, col2 = st.columns(2)

        with col1:
            if est.get("por_status"):
                st.subheader("📈 Distribuição por Status")
                df_status = pd.DataFrame(list(est["por_status"].items()), columns=["Status", "Quantidade"])
                st.bar_chart(df_status.set_index("Status")["Quantidade"], use_container_width=True)
                st.dataframe(df_status, hide_index=True, use_container_width=True)

        with col2:
            if est.get("por_prioridade"):
                st.subheader("🚨 Distribuição por Prioridade")
                df_prioridade = pd.DataFrame(list(est["por_prioridade"].items()), columns=["Prioridade", "Quantidade"])
                ordem_prioridade = ["Urgente", "Alta", "Média", "Baixa"]
                df_prioridade["Ordem"] = df_prioridade["Prioridade"].apply(lambda x: ordem_prioridade.index(x) if x in ordem_prioridade else 99)
                df_prioridade = df_prioridade.sort_values("Ordem")
                st.bar_chart(df_prioridade.set_index("Prioridade")["Quantidade"], use_container_width=True)
                st.dataframe(df_prioridade[["Prioridade", "Quantidade"]], hide_index=True, use_container_width=True)

        if est.get("por_departamento"):
            st.markdown("---")
            st.subheader("🏢 Demandas por Departamento")
            df_depto = pd.DataFrame(list(est["por_departamento"].items()), columns=["Departamento", "Quantidade"])
            df_depto = df_depto.sort_values("Quantidade", ascending=False)

            col1, col2 = st.columns([2, 1])
            with col1:
                st.bar_chart(df_depto.set_index("Departamento")["Quantidade"], use_container_width=True)
            with col2:
                st.dataframe(df_depto, hide_index=True, use_container_width=True)

    elif menu_sel == "⚙️ Configurações":
        st.header("⚙️ Configurações do Sistema")

        st.subheader("🔌 Conexão com Banco de Dados")
        cfg = get_db_config()

        st.code(f"""
Host: {cfg.get('host')}
Database: {cfg.get('database')}
User: {cfg.get('user')}
Port: {cfg.get('port')}
SSL Mode: {cfg.get('sslmode')}
Timezone: America/Fortaleza
        """, language="bash")

        if st.button("🔄 Testar Conexão com Banco de Dados", use_container_width=True):
            with st.spinner("Testando conexão..."):
                ok, msg = test_db_connection()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

        st.markdown("---")
        st.subheader("📧 Configuração de email (por variáveis)")
        ecfg = get_email_config()
        st.caption("Tudo aqui vem das variables do Railway.")
        st.code(f"""
MAIL_ON_NEW_DEMANDA: {ecfg.get("enabled_new")}
SMTP_HOST: {ecfg.get("host")}
SMTP_PORT: {ecfg.get("port")}
SMTP_USER: {ecfg.get("user")}
SMTP_STARTTLS: {ecfg.get("starttls")}
MAIL_FROM: {ecfg.get("from")}
MAIL_TO: {", ".join(ecfg.get("to", []))}
MAIL_CC: {", ".join(ecfg.get("cc", []))}
MAIL_BCC: {", ".join(ecfg.get("bcc", []))}
MAIL_SUBJECT_PREFIX: {ecfg.get("subject_prefix")}
MAIL_SEND_TIMEOUT: {ecfg.get("timeout")}
SMTP_FORCE_IPV4: {ecfg.get("force_ipv4")}
MAIL_RETRIES: {ecfg.get("retries")}
MAIL_RETRY_SLEEP: {ecfg.get("retry_sleep")}
SMTP_DEBUG: {ecfg.get("debug")}
        """.strip(), language="bash")

        st.markdown("---")
        st.subheader("📊 Informações do Sistema")

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Versão do Sistema", "3.3")
            st.metric("Fuso Horário", "America/Fortaleza")
        with col2:
            st.metric("Design", "Comprovante Digital + Relatórios + Email")
            st.metric("Usuários Online", "1")

# =============================
# Boot do sistema
# =============================
if "init_complete" not in st.session_state:
    ok, msg = test_db_connection()
    if ok:
        init_ok, init_msg = init_database()
        if init_ok:
            st.session_state.init_complete = True
        else:
            st.warning(init_msg)
    else:
        st.error(msg)
        st.session_state.demo_mode = True

if "pagina_atual" not in st.session_state:
    st.session_state.pagina_atual = "inicio"

if "usuario_logado" not in st.session_state:
    st.session_state.usuario_logado = False

# =============================
# Rotas do sistema
# =============================
if st.session_state.pagina_atual == "inicio":
    pagina_inicial()
elif st.session_state.pagina_atual == "solicitacao":
    pagina_solicitacao()
elif st.session_state.pagina_atual == "login_admin":
    pagina_login_admin()
elif st.session_state.pagina_atual == "admin":
    pagina_admin()
else:
    st.session_state.pagina_atual = "inicio"
    st.rerun()

# =============================
# Rodapé e informações de debug
# =============================
if st.session_state.pagina_atual in ["admin", "solicitacao"]:
    st.sidebar.markdown("---")
    if DATABASE_URL:
        st.sidebar.success("✅ Conectado ao Railway Postgres")
        if st.sidebar.checkbox("Mostrar informações técnicas", key="debug_info"):
            cfg = get_db_config()
            st.sidebar.text(f"Host: {cfg.get('host')}")
            st.sidebar.text(f"Database: {cfg.get('database')}")
            st.sidebar.text(f"User: {cfg.get('user')}")
            st.sidebar.text(f"Port: {cfg.get('port')}")
            st.sidebar.text("Timezone: America/Fortaleza")
    else:
        st.sidebar.warning("⚠️ DATABASE_URL não encontrada")

    st.sidebar.markdown("---")
    st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas - GRBANABUIU v3.3")
