import streamlit as st
import pandas as pd
import json
from datetime import datetime, date
from decimal import Decimal
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import os
from urllib.parse import urlparse
import hashlib
import pytz
import time

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

                # NOVOS CAMPOS (admin)
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

                        -- NOVOS CAMPOS (admin)
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
                            dados.get("urgencia", False),
                            dados.get("estimativa_horas"),
                            bool(dados.get("almoxarifado", False)),
                            dados.get("valor")
                        ))
                        nova_id, codigo_ok = cur.fetchone()

                        cur.execute("""
                            INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                            VALUES (%s, %s, %s, %s)
                        """, (nova_id, dados["solicitante"], "CRIAÇÃO", dumps_safe(dados)))

                        conn.commit()
                        return {"id": nova_id, "codigo": codigo_ok}
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        continue

        return None
    except Exception as e:
        st.error(f"Erro ao adicionar demanda: {str(e)}")
        return None

def atualizar_demanda(demanda_id, dados):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                antigo = cur.fetchone()

                cur.execute("""
                    UPDATE demandas
                    SET item = %s, quantidade = %s, solicitante = %s,
                        departamento = %s, local = %s, prioridade = %s,
                        observacoes = %s, status = %s, categoria = %s,
                        unidade = %s, urgencia = %s, estimativa_horas = %s,
                        almoxarifado = %s, valor = %s,
                        data_atualizacao = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    dados["item"],
                    dados["quantidade"],
                    dados["solicitante"],
                    dados["departamento"],
                    dados.get("local", "Gerência"),
                    dados["prioridade"],
                    dados.get("observacoes", ""),
                    dados["status"],
                    dados.get("categoria", "Geral"),
                    dados.get("unidade", "Unid."),
                    dados.get("urgencia", False),
                    dados.get("estimativa_horas"),
                    bool(dados.get("almoxarifado", False)),
                    dados.get("valor"),
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

def obter_estatisticas():
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                estat = {}
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(CASE WHEN status = 'Pendente' THEN 1 END) as pendentes,
                        COUNT(CASE WHEN status = 'Em andamento' THEN 1 END) as em_andamento,
                        COUNT(CASE WHEN status = 'Concluída' THEN 1 END) as concluidas,
                        COUNT(CASE WHEN urgencia = TRUE THEN 1 END) as urgentes,
                        COALESCE(SUM(quantidade), 0) as total_itens,
                        COALESCE(SUM(estimativa_horas), 0) as total_horas,
                        COALESCE(SUM(valor), 0) as total_valor
                    FROM demandas
                """)
                estat["totais"] = cur.fetchone() or {}

                cur.execute("""
                    SELECT departamento, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY departamento
                    ORDER BY quantidade DESC
                """)
                estat["por_departamento"] = {r["departamento"]: r["quantidade"] for r in cur.fetchall()}

                cur.execute("""
                    SELECT prioridade, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY prioridade
                    ORDER BY
                        CASE prioridade
                            WHEN 'Urgente' THEN 1
                            WHEN 'Alta' THEN 2
                            WHEN 'Média' THEN 3
                            ELSE 4
                        END
                """)
                estat["por_prioridade"] = {r["prioridade"]: r["quantidade"] for r in cur.fetchall()}

                cur.execute("""
                    SELECT status, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY status
                """)
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

# =============================
# UI helper: Comprovante (novo design)
# =============================
def render_comprovante_demanda(d: dict, mostrar_campos_admin: bool = False):
    """Renderiza uma demanda como comprovante estilizado.
    mostrar_campos_admin = True apenas na área administrativa.
    """

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
                ("Estimativa:", f"{float(d.get('estimativa_horas') or 0):.1f} horas" if d.get("estimativa_horas") else "Não informada"),
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
                time.sleep(0.3)
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
                Acesso para supervisores e administradores.
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

    if st.session_state.solicitacao_enviada:
        st.success(f"""
        ✅ **Solicitação enviada com sucesso!**

        **Código da demanda:** `{st.session_state.ultima_demanda_codigo}`

        Guarde este código para consultar o status posteriormente.
        """)

        st.balloons()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📝 Enviar nova solicitação", use_container_width=True):
                st.session_state.solicitacao_enviada = False
                st.session_state.ultima_demanda_codigo = None
                st.rerun()
        with col2:
            if st.button("🏠 Voltar ao início", use_container_width=True):
                st.session_state.pagina_atual = "inicio"
                st.session_state.solicitacao_enviada = False
                st.session_state.ultima_demanda_codigo = None
                st.rerun()

        st.markdown("---")
        st.subheader("📋 Comprovante da Demanda Enviada")

        filtros = {"codigo": st.session_state.ultima_demanda_codigo}
        resultado = carregar_demandas(filtros)
        if resultado:
            # NÃO mostrar campos admin aqui
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
                unidade = st.selectbox("📏 Unidade*",
                    ["Selecione", "Kg", "Litro", "Unid.", "Metros", "m²", "m³", "Outro"]
                )
                estimativa_horas = st.number_input("⏱️ Estimativa (horas)", min_value=0.0, value=0.0, step=0.5)

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
                            "categoria": categoria,
                            "unidade": unidade,
                            "urgencia": bool(urgencia),
                            "estimativa_horas": float(estimativa_horas) if estimativa_horas and estimativa_horas > 0 else None,
                            # NÃO enviar almoxarifado/valor aqui
                        }

                        res = adicionar_demanda(nova_demanda)
                        if res and res.get("codigo"):
                            st.session_state.solicitacao_enviada = True
                            st.session_state.ultima_demanda_codigo = res["codigo"]
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
                # NÃO mostrar campos admin aqui
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

    menu = ["🏠 Dashboard", "📋 Todas as Demandas", "✏️ Editar Demanda", "📊 Estatísticas", "⚙️ Configurações"]
    if usuario_admin:
        menu.insert(4, "👥 Gerenciar Usuários")

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

    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M:%S')}")

    if menu_sel == "🏠 Dashboard":
        st.header("📊 Dashboard Administrativo")
        est = obter_estatisticas()
        if not est:
            st.info("📭 Sem dados disponíveis.")
            return

        totais = est.get("totais", {})

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📋 Total", totais.get("total", 0))
        col2.metric("⏳ Pendentes", totais.get("pendentes", 0), delta=f"+{totais.get('em_andamento', 0)} em andamento")
        col3.metric("⚠️ Urgentes", totais.get("urgentes", 0))
        col4.metric("📦 Total Itens", totais.get("total_itens", 0))

        st.markdown("---")
        st.subheader("📋 Últimas Demandas")
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

    elif menu_sel == "✏️ Editar Demanda":
        if usuario_nivel not in ["supervisor", "administrador"]:
            st.error("⛔ Apenas supervisores e administradores podem editar demandas.")
            return

        st.header("✏️ Editar Demanda")

        todas = carregar_demandas()
        if not todas:
            st.info("📭 Nenhuma demanda cadastrada.")
            return

        opcoes = [f"{d.get('codigo','SEM-COD')} | {d['solicitante']} | {d['item'][:50]}..." for d in todas]
        escolha = st.selectbox("Selecione uma demanda para editar", opcoes, index=0)

        if escolha:
            codigo_selecionado = escolha.split("|")[0].strip()
            demanda_id = next((d["id"] for d in todas if d.get("codigo") == codigo_selecionado), None)

            if not demanda_id:
                st.error("Demanda não encontrada.")
                return

            demanda_atual = next((d for d in todas if d["id"] == demanda_id), None)
            if not demanda_atual:
                st.error("Erro ao carregar dados da demanda.")
                return

            st.markdown(f"**Editando demanda:** `{demanda_atual.get('codigo', '')}`")

            with st.form(f"form_editar_{demanda_id}"):
                col1, col2 = st.columns(2)

                with col1:
                    item_edit = st.text_area("📝 Descrição", value=demanda_atual["item"], height=100)
                    quantidade_edit = st.number_input("🔢 Quantidade", min_value=1, value=int(demanda_atual["quantidade"]))
                    solicitante_edit = st.text_input("👤 Solicitante", value=demanda_atual["solicitante"])

                    locais_lista = ["Banabuiú", "Capitão Mor", "Cipoada", "Fogareiro", "Gerência", "Outro", "Patu", "Pirabibu",
                                    "Poço do Barro", "Quixeramobim", "São Jose I", "São Jose II", "Serafim Dias", "Trapiá II", "Umari", "Vieirão"]

                    local_atual = demanda_atual.get("local", "Gerência")
                    local_index = locais_lista.index(local_atual) if local_atual in locais_lista else 0
                    local_edit = st.selectbox("📍 Local", locais_lista, index=local_index)

                with col2:
                    prioridade_lista = ["Baixa", "Média", "Alta", "Urgente"]
                    status_lista = ["Pendente", "Em andamento", "Concluída", "Cancelada"]

                    pri_index = prioridade_lista.index(demanda_atual["prioridade"]) if demanda_atual["prioridade"] in prioridade_lista else 1
                    st_index = status_lista.index(demanda_atual["status"]) if demanda_atual["status"] in status_lista else 0

                    prioridade_edit = st.selectbox("🚨 Prioridade", prioridade_lista, index=pri_index)
                    status_edit = st.selectbox("📊 Status", status_lista, index=st_index)

                    unidades_lista = ["Kg", "Litro", "Unid.", "Metros", "m²", "m³", "Outro"]
                    unidade_atual = demanda_atual.get("unidade", "Unid.")
                    uni_index = unidades_lista.index(unidade_atual) if unidade_atual in unidades_lista else 2

                    categoria_edit = st.text_input("📂 Categoria", value=demanda_atual.get("categoria") or "Geral")
                    unidade_edit = st.selectbox("📏 Unidade", unidades_lista, index=uni_index)
                    urgencia_edit = st.checkbox("🚨 Urgente", value=bool(demanda_atual.get("urgencia", False)))

                    # NOVOS CAMPOS (somente admin)
                    almoxarifado_edit = st.selectbox(
                        "📦 Almoxarifado",
                        ["Não", "Sim"],
                        index=1 if bool(demanda_atual.get("almoxarifado", False)) else 0
                    )
                    valor_edit = st.number_input(
                        "💰 Valor (R$)",
                        min_value=0.0,
                        value=float(demanda_atual.get("valor") or 0.0),
                        step=10.0,
                        format="%.2f"
                    )

                    observacoes_edit = st.text_area("💬 Observações", value=demanda_atual.get("observacoes") or "", height=100)

                col_b1, col_b2, col_b3 = st.columns(3)
                salvar = col_b1.form_submit_button("💾 Salvar Alterações", type="primary")
                excluir = col_b2.form_submit_button("🗑️ Excluir Demanda") if usuario_admin else False
                cancelar = col_b3.form_submit_button("↻ Cancelar")

                if salvar:
                    ok = atualizar_demanda(demanda_id, {
                        "item": item_edit,
                        "quantidade": int(quantidade_edit),
                        "solicitante": solicitante_edit,
                        "departamento": demanda_atual.get("departamento", ""),
                        "local": local_edit,
                        "prioridade": prioridade_edit,
                        "status": status_edit,
                        "categoria": categoria_edit,
                        "unidade": unidade_edit,
                        "urgencia": bool(urgencia_edit),
                        "observacoes": observacoes_edit,
                        "estimativa_horas": demanda_atual.get("estimativa_horas"),
                        "almoxarifado": (almoxarifado_edit == "Sim"),
                        "valor": float(valor_edit) if valor_edit and valor_edit > 0 else None,
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
            render_comprovante_demanda(demanda_atual, mostrar_campos_admin=True)

    elif menu_sel == "👥 Gerenciar Usuários":
        pagina_gerenciar_usuarios()

    elif menu_sel == "📊 Estatísticas":
        st.header("📊 Estatísticas Avançadas")
        est = obter_estatisticas()

        if not est:
            st.info("📭 Sem dados disponíveis para análise.")
            return

        totais = est.get("totais", {})
        st.metric("⏱️ Total de horas estimadas", f"{float(totais.get('total_horas', 0) or 0):.1f}h")
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
        st.subheader("📊 Informações do Sistema")

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Versão do Sistema", "3.0")
            st.metric("Fuso Horário", "America/Fortaleza")
        with col2:
            st.metric("Design", "Comprovante Digital")
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
    st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas - GRBANABUIU v3.0")
