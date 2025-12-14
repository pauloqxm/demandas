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

# =============================
# Configuração da página
# =============================
st.set_page_config(
    page_title="Sistema de Demandas - Railway",
    page_icon="🚂",
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

                # novas colunas que seu app usa
                alters = []
                if "local" not in existentes:
                    alters.append("ADD COLUMN local VARCHAR(100) DEFAULT 'Gerência'")
                if "unidade" not in existentes:
                    alters.append("ADD COLUMN unidade VARCHAR(50) DEFAULT 'Unid.'")
                if "codigo" not in existentes:
                    alters.append("ADD COLUMN codigo VARCHAR(20)")

                for alt in alters:
                    try:
                        cur.execute(f"ALTER TABLE demandas {alt}")
                    except Exception as e:
                        st.warning(f"Aviso alterando demandas: {str(e)}")

                # índice e unique para codigo
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

                # cria demandas caso não exista (já com codigo)
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
                        estimativa_horas DECIMAL(5,2)
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

                # migrações
                ok_d, msg_d = verificar_e_atualizar_tabela_demandas()
                if not ok_d:
                    conn.rollback()
                    return False, msg_d

                ok_u, msg_u = verificar_e_atualizar_tabela_usuarios()
                if not ok_u:
                    conn.rollback()
                    return False, msg_u

                # índices úteis
                try:
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_demandas_codigo ON demandas(codigo)")
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_status ON demandas(status)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_prioridade ON demandas(prioridade)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_data_criacao ON demandas(data_criacao DESC)")
                except Exception:
                    pass

                # usuário admin padrão
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
    """
    Aceita '141225-01', '14122501', '14/12/25-01' e tenta deixar '141225-01'
    """
    if not texto:
        return ""
    s = str(texto).strip()
    s = s.replace("/", "").replace(" ", "").replace(".", "").replace("_", "")
    # se veio 14122501 vira 141225-01
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
                           estimativa_horas
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

                query += " ORDER BY "

                if filtros and filtros.get("sort_by") == "prioridade":
                    query += """
                        CASE prioridade
                            WHEN 'Urgente' THEN 1
                            WHEN 'Alta' THEN 2
                            WHEN 'Média' THEN 3
                            ELSE 4
                        END,
                    """

                query += " data_criacao DESC"

                cur.execute(query, params)
                demandas = cur.fetchall()

                for d in demandas:
                    d["data_criacao_formatada"] = formatar_data_hora_fortaleza(d.get("data_criacao"))
                    d["data_atualizacao_formatada"] = formatar_data_hora_fortaleza(d.get("data_atualizacao"))

                return demandas
    except Exception as e:
        st.error(f"Erro ao carregar demandas: {str(e)}")
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
                             observacoes, categoria, unidade, urgencia, estimativa_horas)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                            dados.get("estimativa_horas")
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
                        COALESCE(SUM(estimativa_horas), 0) as total_horas
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
# Páginas
# =============================
def pagina_inicial():
    agora = agora_fortaleza()
    st.sidebar.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")
    st.title("🚂 Sistema de Demandas - Railway")
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📝 Solicitação e Consulta")
        st.markdown(
            "Envie uma nova demanda e também consulte depois usando seu nome ou o código da demanda."
        )
        if st.button("📄 Acessar Solicitação", type="primary", use_container_width=True):
            st.session_state.pagina_atual = "solicitacao"
            st.rerun()

    with col2:
        st.subheader("🔧 Área Administrativa")
        st.markdown("Acesso para supervisores e administradores.")
        if st.button("🔐 Entrar como Admin", use_container_width=True):
            st.session_state.pagina_atual = "login_admin"
            st.rerun()

    st.markdown("---")
    st.caption(f"🕒 Agora: {agora.strftime('%d/%m/%Y %H:%M:%S')} (Fortaleza)")

def pagina_solicitacao():
    st.header("📝 Solicitação e Consulta")
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")

    if "solicitacao_enviada" not in st.session_state:
        st.session_state.solicitacao_enviada = False
    if "ultima_demanda_codigo" not in st.session_state:
        st.session_state.ultima_demanda_codigo = None

    # CONSULTA DO USUÁRIO
    with st.expander("🔎 Consultar demanda por Nome ou Código", expanded=True):
        colc1, colc2 = st.columns(2)
        with colc1:
            filtro_nome = st.text_input("Nome do solicitante", placeholder="Ex: Maria")
        with colc2:
            filtro_codigo = st.text_input("Código da demanda", placeholder="Ex: 141225-01")

        btn_consultar = st.button("Buscar", type="secondary", use_container_width=True)

        if btn_consultar:
            filtros = {}
            if filtro_nome.strip():
                filtros["solicitante"] = filtro_nome.strip()
            if filtro_codigo.strip():
                filtros["codigo"] = filtro_codigo.strip()

            if not filtros:
                st.warning("Digite o nome do solicitante ou o código.")
            else:
                resultados = carregar_demandas(filtros)
                if resultados:
                    df = pd.DataFrame(resultados)
                    # mostra primeiro o código, que é o público
                    cols = ["codigo", "status", "prioridade", "departamento", "local", "categoria", "unidade",
                            "quantidade", "item", "data_criacao_formatada", "data_atualizacao_formatada"]
                    cols = [c for c in cols if c in df.columns]
                    st.dataframe(df[cols], use_container_width=True, hide_index=True)
                else:
                    st.info("Nada encontrado com esses dados.")

    st.markdown("---")

    # CONFIRMAÇÃO DE ENVIO
    if st.session_state.solicitacao_enviada:
        st.success(f"✅ Solicitação enviada. Código: {st.session_state.ultima_demanda_codigo}")
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
        return

    # FORMULÁRIO DE ENVIO
    with st.form("form_nova_demanda", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            solicitante = st.text_input("👤 Nome do Solicitante*")
            departamento = st.selectbox("🏢 Departamento*", ["Administrativo", "Gestão", "Operação", "Açudes", "EB", "Outro"])
            local = st.selectbox("📍 Local*", ["Gerência", "Fogareiro", "Quixeramobim", "Outro"])
            categoria = st.selectbox("📂 Categoria", ["Combustível", "Materiais", "Equipamentos", "Ferramentas", "Alimentos", "Lubrificantes", "Outro"])
            unidade = st.selectbox("📏 Unidade*", ["Kg", "Litro", "Unid.", "Metros", "m²", "m³", "Outro"])

        with col2:
            item = st.text_area("📝 Descrição da Demanda*", height=100)
            quantidade = st.number_input("🔢 Quantidade*", min_value=1, value=1, step=1)
            estimativa_horas = st.number_input("⏱️ Estimativa (horas)", min_value=0.0, value=0.0, step=0.5)

        col3, col4 = st.columns(2)
        with col3:
            prioridade = st.selectbox("🚨 Prioridade", ["Baixa", "Média", "Alta", "Urgente"], index=1)
            urgencia = st.checkbox("🚨 É urgente?")

        with col4:
            observacoes = st.text_area("💬 Observações Adicionais", height=100)

        submitted = st.form_submit_button("✅ Enviar Solicitação", type="primary")

        if submitted:
            if solicitante and item and departamento and local and unidade:
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
                    "estimativa_horas": float(estimativa_horas) if estimativa_horas and estimativa_horas > 0 else None
                }

                res = adicionar_demanda(nova_demanda)
                if res and res.get("codigo"):
                    st.session_state.solicitacao_enviada = True
                    st.session_state.ultima_demanda_codigo = res["codigo"]
                    st.rerun()
                else:
                    st.error("❌ Erro ao salvar a solicitação.")
            else:
                st.error("⚠️ Preencha todos os campos obrigatórios (*)")

    if st.button("← Voltar ao Início"):
        st.session_state.pagina_atual = "inicio"
        st.rerun()

def pagina_login_admin():
    st.title("🔧 Área Administrativa")
    st.markdown("---")
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")
    st.warning("🔒 Acesso restrito")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_admin_login"):
            username = st.text_input("👤 Username")
            senha = st.text_input("🔑 Senha", type="password")
            login_submit = st.form_submit_button("🔓 Entrar", type="primary")

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
                    st.error("⚠️ Preencha tudo.")

    if st.button("← Voltar ao Início"):
        st.session_state.pagina_atual = "inicio"
        st.rerun()

def pagina_gerenciar_usuarios():
    st.header("👥 Gerenciamento de Usuários")
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")

    if not st.session_state.get("usuario_admin", False):
        st.error("⛔ Apenas administradores.")
        return

    tab1, tab2 = st.tabs(["📋 Lista", "➕ Novo"])

    with tab1:
        usuarios = listar_usuarios()
        if not usuarios:
            st.info("Nenhum usuário.")
            return

        df = pd.DataFrame(usuarios)
        df["is_admin"] = df["is_admin"].apply(lambda x: "✅" if x else "❌")
        df["ativo"] = df["ativo"].apply(lambda x: "✅" if x else "❌")
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("⚙️ Ações")
        op = st.selectbox("Selecione", [f"{u['id']} - {u['nome']} ({u['username']})" for u in usuarios])
        usuario_id = int(op.split(" - ")[0])
        info = next((u for u in usuarios if u["id"] == usuario_id), None)
        if not info:
            return

        col1, col2, col3 = st.columns(3)
        with col1:
            novo_nivel = st.selectbox("Nível", ["usuario", "supervisor", "administrador"],
                                      index=["usuario", "supervisor", "administrador"].index(info["nivel_acesso"]))
            if st.button("💾 Salvar nível"):
                ok, msg = atualizar_usuario(usuario_id, {"nivel_acesso": novo_nivel, "is_admin": (novo_nivel == "administrador")})
                st.success(msg) if ok else st.error(msg)

        with col2:
            nova_senha = st.text_input("Nova senha", type="password")
            if st.button("🔐 Trocar senha"):
                if not nova_senha:
                    st.warning("Digite a senha.")
                else:
                    ok, msg = atualizar_usuario(usuario_id, {"senha": nova_senha})
                    st.success(msg) if ok else st.error(msg)

        with col3:
            if st.button("⛔ Desativar usuário"):
                ok, msg = desativar_usuario(usuario_id)
                st.success(msg) if ok else st.error(msg)

    with tab2:
        with st.form("form_novo_usuario"):
            col1, col2 = st.columns(2)
            with col1:
                nome = st.text_input("Nome*")
                email = st.text_input("Email*")
                username = st.text_input("Username*")
            with col2:
                departamento = st.selectbox("Departamento", ["Administrativo", "Gestão", "Operação", "Açudes", "EB", "TI", "RH", "Financeiro", "Outro"])
                nivel_acesso = st.selectbox("Nível", ["usuario", "supervisor", "administrador"])
                senha = st.text_input("Senha*", type="password")
                confirmar = st.text_input("Confirmar*", type="password")

            criar = st.form_submit_button("✅ Criar", type="primary")
            if criar:
                if not all([nome, email, username, senha, confirmar]):
                    st.error("Preencha tudo.")
                elif senha != confirmar:
                    st.error("Senhas não batem.")
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
                    st.success(msg) if ok else st.error(msg)
                    if ok:
                        st.balloons()
                        st.rerun()

def pagina_admin():
    if not st.session_state.get("usuario_logado", False):
        st.session_state.pagina_atual = "login_admin"
        st.rerun()
        return

    agora = agora_fortaleza()
    st.sidebar.caption(f"🕒 {agora.strftime('%d/%m/%Y %H:%M')} (Fortaleza)")

    st.sidebar.title("🔧 Administração")
    st.sidebar.markdown(f"**👤 {st.session_state.get('usuario_nome', 'Usuário')}**")
    st.sidebar.caption(f"Nível: {st.session_state.get('usuario_nivel', 'usuario').title()}")
    st.sidebar.markdown("---")

    usuario_nivel = st.session_state.get("usuario_nivel", "usuario")
    usuario_admin = st.session_state.get("usuario_admin", False)

    menu = ["🏠 Dashboard", "📋 Todas as Demandas", "✏️ Editar Demanda", "📊 Estatísticas", "⚙️ Configurações"]
    if usuario_admin:
        menu.insert(4, "👥 Gerenciar Usuários")

    menu_sel = st.sidebar.radio("Navegação", menu)

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 Filtros")
    status_filtro = st.sidebar.multiselect("Status", ["Pendente", "Em andamento", "Concluída", "Cancelada"],
                                          default=["Pendente", "Em andamento", "Concluída", "Cancelada"])
    prioridade_filtro = st.sidebar.multiselect("Prioridade", ["Urgente", "Alta", "Média", "Baixa"],
                                              default=["Urgente", "Alta", "Média", "Baixa"])

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Logout"):
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
        st.header("📊 Dashboard")
        est = obter_estatisticas()
        if not est:
            st.info("Sem dados.")
            return
        totais = est.get("totais", {})

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", totais.get("total", 0))
        c2.metric("Pendentes", totais.get("pendentes", 0))
        c3.metric("Urgentes", totais.get("urgentes", 0))
        c4.metric("Total itens", totais.get("total_itens", 0))

        col1, col2 = st.columns(2)
        with col1:
            if est.get("por_departamento"):
                df = pd.DataFrame(list(est["por_departamento"].items()), columns=["Departamento", "Qtd"])
                st.bar_chart(df.set_index("Departamento"))
        with col2:
            if est.get("por_prioridade"):
                df = pd.DataFrame(list(est["por_prioridade"].items()), columns=["Prioridade", "Qtd"])
                st.bar_chart(df.set_index("Prioridade"))

        st.subheader("📋 Últimas")
        rec = carregar_demandas(filtros)[:10]
        if rec:
            df = pd.DataFrame(rec)
            cols = ["codigo", "status", "prioridade", "departamento", "local", "solicitante", "item", "data_criacao_formatada"]
            cols = [c for c in cols if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("Nada com esses filtros.")

    elif menu_sel == "📋 Todas as Demandas":
        st.header("📋 Todas as Demandas")
        busca = st.text_input("🔎 Buscar por item, solicitante ou código", placeholder="Ex: Maria ou 141225-01")
        if busca.strip():
            filtros["search"] = busca.strip()

        dados = carregar_demandas(filtros)
        if not dados:
            st.info("Nada encontrado.")
            return

        df = pd.DataFrame(dados)
        st.info(f"Encontradas {len(df)} demandas")

        if usuario_nivel in ["supervisor", "administrador"]:
            if st.button("📥 Exportar CSV"):
                csv = df.to_csv(index=False)
                st.download_button("Baixar CSV", data=csv,
                                   file_name=f"demandas_{agora.strftime('%Y%m%d_%H%M%S')}.csv",
                                   mime="text/csv")

        st.dataframe(df, use_container_width=True, hide_index=True)

    elif menu_sel == "✏️ Editar Demanda":
        if usuario_nivel not in ["supervisor", "administrador"]:
            st.error("⛔ Apenas supervisor/admin.")
            return

        st.header("✏️ Editar Demanda")
        todas = carregar_demandas()
        if not todas:
            st.info("Sem demandas.")
            return

        opcoes = [f"{d.get('codigo','SEM-COD')} | id {d['id']} | {d['item'][:50]}" for d in todas]
        escolha = st.selectbox("Selecione", opcoes)

        demanda_id = int(escolha.split("|")[1].strip().replace("id", "").strip())
        atual = next((d for d in todas if d["id"] == demanda_id), None)
        if not atual:
            st.info("Demanda não encontrada.")
            return

        st.caption(f"Código: {atual.get('codigo','')}")
        st.caption(f"Criado: {atual.get('data_criacao_formatada','')}")
        st.caption(f"Atualizado: {atual.get('data_atualizacao_formatada','')}")

        departamentos_lista = ["TI", "RH", "Financeiro", "Comercial", "Operações", "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"]
        locais_lista = ["Gerência", "Fogareiro", "Quixeramobim", "Outro"]
        unidades_lista = ["Kg", "Litro", "Unid.", "Metros", "m²", "m³", "Outro"]
        status_lista = ["Pendente", "Em andamento", "Concluída", "Cancelada"]
        prioridade_lista = ["Baixa", "Média", "Alta", "Urgente"]

               # índices seguros
        dep_index = departamentos_lista.index(atual["departamento"]) if atual["departamento"] in departamentos_lista else len(departamentos_lista) - 1
        loc_index = locais_lista.index(atual.get("local", "Gerência")) if atual.get("local", "Gerência") in locais_lista else 0
        uni_index = unidades_lista.index(atual.get("unidade", "Unid.")) if atual.get("unidade", "Unid.") in unidades_lista else 2
        pri_index = prioridade_lista.index(atual["prioridade"]) if atual["prioridade"] in prioridade_lista else 1
        st_index = status_lista.index(atual["status"]) if atual["status"] in status_lista else 0

        with st.form(f"form_editar_{demanda_id}"):
            col1, col2 = st.columns(2)
            with col1:
                item_edit = st.text_area("Descrição", value=atual["item"], height=100)
                quantidade_edit = st.number_input("Quantidade", min_value=1, value=int(atual["quantidade"]))
                solicitante_edit = st.text_input("Solicitante", value=atual["solicitante"])
                departamento_edit = st.selectbox("Departamento", departamentos_lista, index=dep_index)
                local_edit = st.selectbox("Local", locais_lista, index=loc_index)

            with col2:
                prioridade_edit = st.selectbox("Prioridade", prioridade_lista, index=pri_index)
                status_edit = st.selectbox("Status", status_lista, index=st_index)
                categoria_edit = st.text_input("Categoria", value=atual.get("categoria") or "Geral")
                unidade_edit = st.selectbox("Unidade", unidades_lista, index=uni_index)
                urgencia_edit = st.checkbox("Urgente", value=bool(atual.get("urgencia", False)))
                observacoes_edit = st.text_area("Observações", value=atual.get("observacoes") or "", height=100)

            c1, c2, c3 = st.columns(3)
            salvar = c1.form_submit_button("💾 Salvar", type="primary")
            excluir = c2.form_submit_button("🗑️ Excluir") if usuario_admin else False
            cancelar = c3.form_submit_button("↻ Cancelar")

            if salvar:
                ok = atualizar_demanda(demanda_id, {
                    "item": item_edit,
                    "quantidade": int(quantidade_edit),
                    "solicitante": solicitante_edit,
                    "departamento": departamento_edit,
                    "local": local_edit,
                    "prioridade": prioridade_edit,
                    "status": status_edit,
                    "categoria": categoria_edit,
                    "unidade": unidade_edit,
                    "urgencia": bool(urgencia_edit),
                    "observacoes": observacoes_edit,
                    "estimativa_horas": atual.get("estimativa_horas"),
                })
                if ok:
                    st.success("Atualizado.")
                    st.rerun()

            if excluir and usuario_admin:
                if excluir_demanda(demanda_id):
                    st.warning("Excluída.")
                    st.rerun()

            if cancelar:
                st.rerun()

    elif menu_sel == "👥 Gerenciar Usuários":
        pagina_gerenciar_usuarios()

    elif menu_sel == "📊 Estatísticas":
        st.header("📊 Estatísticas")
        est = obter_estatisticas()
        if not est:
            st.info("Sem dados.")
            return
        totais = est.get("totais", {})
        st.metric("Total de horas estimadas", f"{float(totais.get('total_horas', 0) or 0):.1f}h")

        col1, col2 = st.columns(2)
        with col1:
            if est.get("por_status"):
                df = pd.DataFrame(list(est["por_status"].items()), columns=["Status", "Qtd"])
                st.bar_chart(df.set_index("Status"))
        with col2:
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SET TIME ZONE 'America/Fortaleza'")
                        cur.execute("""
                            SELECT DATE(data_criacao) as data, COUNT(*) as quantidade
                            FROM demandas
                            WHERE data_criacao >= CURRENT_DATE - INTERVAL '7 days'
                            GROUP BY DATE(data_criacao)
                            ORDER BY data
                        """)
                        dados = cur.fetchall()
                if dados:
                    df = pd.DataFrame(dados, columns=["Data", "Quantidade"])
                    st.line_chart(df.set_index("Data"))
                else:
                    st.info("Sem dados nos últimos 7 dias.")
            except Exception:
                st.info("Falha ao carregar série temporal.")

    elif menu_sel == "⚙️ Configurações":
        st.header("⚙️ Configurações")
        cfg = get_db_config()
        st.code(
            f"Host: {cfg.get('host')}\n"
            f"Database: {cfg.get('database')}\n"
            f"User: {cfg.get('user')}\n"
            f"Port: {cfg.get('port')}\n"
            f"SSL: {cfg.get('sslmode')}\n"
            f"Timezone: America/Fortaleza"
        )

        if st.button("🔄 Testar conexão"):
            ok, msg = test_db_connection()
            st.success(msg) if ok else st.error(msg)

# =============================
# Boot
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
# Rotas
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
# Rodapé e debug
# =============================
if st.session_state.pagina_atual in ["admin", "solicitacao"]:
    st.sidebar.markdown("---")
    if DATABASE_URL:
        st.sidebar.success("✅ Conectado ao Railway Postgres")
        if st.sidebar.checkbox("Mostrar debug"):
            cfg = get_db_config()
            st.sidebar.text(f"Host: {cfg.get('host')}")
            st.sidebar.text(f"Database: {cfg.get('database')}")
            st.sidebar.text(f"User: {cfg.get('user')}")
            st.sidebar.text(f"Port: {cfg.get('port')}")
            st.sidebar.text("Timezone: America/Fortaleza")
    else:
        st.sidebar.warning("⚠️ DATABASE_URL não encontrada")

    st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas v2.1")
