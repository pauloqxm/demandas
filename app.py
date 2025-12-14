import streamlit as st
import pandas as pd
import json
from datetime import datetime, date, timezone, timedelta
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
    initial_sidebar_state="collapsed"  # Sidebar inicialmente fechada
)

# ============================================
# CONFIGURAÇÃO DO FUSO HORÁRIO (FORTALEZA - UTC-3)
# ============================================

FORTALEZA_TZ = pytz.timezone('America/Fortaleza')

def agora_fortaleza():
    """Retorna o horário atual em Fortaleza"""
    return datetime.now(FORTALEZA_TZ)

def converter_para_fortaleza(dt):
    """Converte um datetime para o fuso horário de Fortaleza"""
    if dt.tzinfo is None:
        # Se não tem timezone, assume UTC
        dt = pytz.utc.localize(dt)
    return dt.astimezone(FORTALEZA_TZ)

def formatar_data_hora_fortaleza(dt, formato='%d/%m/%Y %H:%M'):
    """Formata um datetime para o fuso horário de Fortaleza"""
    if dt:
        dt_fortaleza = converter_para_fortaleza(dt)
        return dt_fortaleza.strftime(formato)
    return ""

# ============================================
# CONFIGURAÇÃO DA CONEXÃO COM RAILWAY POSTGRES
# ============================================

DATABASE_URL = (
    os.environ.get("DATABASE_PUBLIC_URL")
    or os.environ.get("DATABASE_URL")
)

def _safe_st_secrets_get(key: str, default=None):
    """
    Tenta ler st.secrets sem quebrar quando não existir secrets.toml.
    No Railway, normalmente não existe.
    """
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

def get_db_config():
    if DATABASE_URL:
        url = urlparse(DATABASE_URL)
        return {
            "host": url.hostname,
            "database": url.path[1:],  # remove "/"
            "user": url.username,
            "password": url.password,
            "port": url.port or 5432,
            "sslmode": "require",
        }

    # Desenvolvimento local
    return {
        "host": os.environ.get("DB_HOST") or _safe_st_secrets_get("DB_HOST", "localhost"),
        "database": os.environ.get("DB_NAME") or _safe_st_secrets_get("DB_NAME", "railway"),
        "user": os.environ.get("DB_USER") or _safe_st_secrets_get("DB_USER", "postgres"),
        "password": os.environ.get("DB_PASSWORD") or _safe_st_secrets_get("DB_PASSWORD", ""),
        "port": int(os.environ.get("DB_PORT") or _safe_st_secrets_get("DB_PORT", 5432)),
        "sslmode": os.environ.get("DB_SSLMODE") or _safe_st_secrets_get("DB_SSLMODE", "prefer"),
    }

# ============================================
# FUNÇÕES DE SEGURANÇA E AUTENTICAÇÃO
# ============================================

def hash_password(password):
    """Cria hash da senha usando SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verificar_senha(senha_digitada, senha_hash):
    """Verifica se a senha digitada corresponde ao hash"""
    return hash_password(senha_digitada) == senha_hash

@contextmanager
def get_db_connection():
    """Context manager para conexões com o banco de dados"""
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
                version = cur.fetchone()
                return True, f"✅ Conectado ao PostgreSQL {version[0]}"
    except Exception as e:
        return False, f"❌ Falha na conexão: {str(e)}"

def verificar_e_atualizar_tabela_usuarios():
    """Verifica e atualiza a estrutura da tabela usuarios se necessário"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Verificar se a tabela usuarios existe
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'usuarios'
                    );
                """)
                tabela_existe = cur.fetchone()[0]
                
                if not tabela_existe:
                    # Criar tabela nova
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
                    return True, "Tabela usuarios criada com sucesso!"
                
                # Verificar colunas existentes
                cur.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'usuarios'
                """)
                colunas = cur.fetchall()
                colunas_existentes = [col[0] for col in colunas]
                
                # Lista de colunas necessárias
                colunas_necessarias = [
                    ('username', 'VARCHAR(100)', 'ADD COLUMN username VARCHAR(100) UNIQUE'),
                    ('senha_hash', 'VARCHAR(255)', 'ADD COLUMN senha_hash VARCHAR(255) NOT NULL DEFAULT \'\''),
                    ('nivel_acesso', 'VARCHAR(50)', 'ADD COLUMN nivel_acesso VARCHAR(50) DEFAULT \'usuario\''),
                    ('ativo', 'BOOLEAN', 'ADD COLUMN ativo BOOLEAN DEFAULT TRUE'),
                    ('ultimo_login', 'TIMESTAMP WITH TIME ZONE', 'ADD COLUMN ultimo_login TIMESTAMP WITH TIME ZONE')
                ]
                
                alteracoes = []
                for coluna, tipo, sql in colunas_necessarias:
                    if coluna not in colunas_existentes:
                        alteracoes.append(sql)
                
                # Executar alterações se necessário
                if alteracoes:
                    for alteracao in alteracoes:
                        try:
                            cur.execute(f"ALTER TABLE usuarios {alteracao}")
                        except Exception as e:
                            st.warning(f"Aviso ao adicionar coluna: {str(e)}")
                    
                    # Se username foi adicionado, precisamos preencher com valores padrão
                    if 'username' not in colunas_existentes:
                        cur.execute("""
                            UPDATE usuarios 
                            SET username = LOWER(REPLACE(nome, ' ', '_')) || '_' || id::text
                            WHERE username IS NULL OR username = ''
                        """)
                    
                    conn.commit()
                    return True, f"Tabela usuarios atualizada! Colunas adicionadas: {len(alteracoes)}"
                
                return True, "Tabela usuarios já está atualizada!"
                
    except Exception as e:
        return False, f"Erro ao verificar/atualizar tabela usuarios: {str(e)}"

def init_database():
    """Inicializa o banco de dados no Railway"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                # Tabela de demandas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS demandas (
                        id SERIAL PRIMARY KEY,
                        item VARCHAR(500) NOT NULL,
                        quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                        solicitante VARCHAR(200) NOT NULL,
                        departamento VARCHAR(100) NOT NULL,
                        prioridade VARCHAR(50) NOT NULL,
                        observacoes TEXT,
                        status VARCHAR(50) DEFAULT 'Pendente',
                        data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        data_atualizacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        categoria VARCHAR(100),
                        urgencia BOOLEAN DEFAULT FALSE,
                        estimativa_horas DECIMAL(5,2)
                    )
                """)

                # Tabela de histórico
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

                conn.commit()

                # Verificar e atualizar tabela de usuários
                sucesso, mensagem = verificar_e_atualizar_tabela_usuarios()
                if not sucesso:
                    return False, mensagem

                # Criar usuário admin padrão se não existir
                cur.execute("SELECT COUNT(*) FROM usuarios WHERE username = 'admin'")
                if cur.fetchone()[0] == 0:
                    admin_hash = hash_password("admin123")
                    cur.execute("""
                        INSERT INTO usuarios (nome, email, username, senha_hash, nivel_acesso, is_admin, ativo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        "Administrador Principal",
                        "admin@sistema.com",
                        "admin",
                        admin_hash,
                        "administrador",
                        True,
                        True
                    ))
                    st.success("✅ Usuário admin padrão criado (username: admin, senha: admin123)")

                conn.commit()

        return True, "✅ Banco de dados inicializado com sucesso!"
    except Exception as e:
        return False, f"❌ Erro ao inicializar banco: {str(e)}"

# ============================================
# FUNÇÕES DE USUÁRIOS E AUTENTICAÇÃO
# ============================================

def autenticar_usuario(username, senha):
    """Autentica um usuário pelo username e senha"""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                cur.execute("""
                    SELECT id, nome, email, username, senha_hash, 
                           nivel_acesso, is_admin, departamento, ativo
                    FROM usuarios 
                    WHERE username = %s AND ativo = TRUE
                """, (username,))
                
                usuario = cur.fetchone()
                
                if usuario and verificar_senha(senha, usuario["senha_hash"]):
                    # Atualizar último login
                    cur.execute("""
                        UPDATE usuarios 
                        SET ultimo_login = CURRENT_TIMESTAMP 
                        WHERE id = %s
                    """, (usuario["id"],))
                    conn.commit()
                    return usuario
                return None
    except Exception as e:
        st.error(f"Erro na autenticação: {str(e)}")
        return None

def criar_usuario(dados_usuario):
    """Cria um novo usuário no sistema"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                # Verificar se username ou email já existem
                cur.execute("""
                    SELECT COUNT(*) FROM usuarios 
                    WHERE username = %s OR email = %s
                """, (dados_usuario["username"], dados_usuario["email"]))
                
                if cur.fetchone()[0] > 0:
                    return False, "Username ou email já cadastrado"
                
                # Criar hash da senha
                senha_hash = hash_password(dados_usuario["senha"])
                
                cur.execute("""
                    INSERT INTO usuarios 
                    (nome, email, username, senha_hash, departamento, nivel_acesso, is_admin, ativo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
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
                return True, "Usuário criado com sucesso!"
    except Exception as e:
        return False, f"Erro ao criar usuário: {str(e)}"

def listar_usuarios():
    """Lista todos os usuários do sistema"""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Configurar timezone para Fortaleza
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
        st.error(f"Erro ao listar usuários: {str(e)}")
        return []

def atualizar_usuario(usuario_id, dados_atualizados):
    """Atualiza os dados de um usuário"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                # Construir query dinamicamente
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
                    return False, "Nenhum dado para atualizar"
                
                query = f"UPDATE usuarios SET {', '.join(campos)} WHERE id = %s"
                valores.append(usuario_id)
                
                cur.execute(query, valores)
                conn.commit()
                return True, "Usuário atualizado com sucesso!"
    except Exception as e:
        return False, f"Erro ao atualizar usuário: {str(e)}"

def excluir_usuario(usuario_id):
    """Exclui um usuário (desativa)"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                cur.execute("""
                    UPDATE usuarios 
                    SET ativo = FALSE 
                    WHERE id = %s AND id != 1  # Não permitir excluir o admin principal
                """, (usuario_id,))
                conn.commit()
                return True, "Usuário desativado com sucesso!"
    except Exception as e:
        return False, f"Erro ao excluir usuário: {str(e)}"

# ============================================
# UTILITÁRIO JSON SEGURO
# ============================================

def json_safe(obj):
    """Converte tipos não JSON (datetime, date, Decimal etc.) para serializáveis."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

def dumps_safe(payload):
    """json.dumps sem quebrar com datetime, Decimal e similares."""
    return json.dumps(json_safe(payload), ensure_ascii=False, default=str)

# ============================================
# FUNÇÕES DO SISTEMA DE DEMANDAS
# ============================================

def carregar_demandas(filtros=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                query = """
                    SELECT id, item, quantidade, solicitante, departamento,
                           prioridade, observacoes, status, categoria, urgencia,
                           data_criacao,
                           data_atualizacao,
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
                        query += " AND (item ILIKE %s OR solicitante ILIKE %s)"
                        params.append(f"%{filtros['search']}%")
                        params.append(f"%{filtros['search']}%")

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
                
                # Formatar datas para Fortaleza
                for demanda in demandas:
                    if demanda.get("data_criacao"):
                        demanda["data_criacao_formatada"] = formatar_data_hora_fortaleza(
                            demanda["data_criacao"], '%d/%m/%Y %H:%M'
                        )
                    if demanda.get("data_atualizacao"):
                        demanda["data_atualizacao_formatada"] = formatar_data_hora_fortaleza(
                            demanda["data_atualizacao"], '%d/%m/%Y %H:%M'
                        )
                
                return demandas
    except Exception as e:
        st.error(f"Erro ao carregar demandas: {str(e)}")
        return []

def adicionar_demanda(dados):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                cur.execute("""
                    INSERT INTO demandas
                    (item, quantidade, solicitante, departamento, prioridade, observacoes, categoria, urgencia, estimativa_horas)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, data_criacao
                """, (
                    dados["item"],
                    dados["quantidade"],
                    dados["solicitante"],
                    dados["departamento"],
                    dados["prioridade"],
                    dados.get("observacoes", ""),
                    dados.get("categoria", "Geral"),
                    dados.get("urgencia", False),
                    dados.get("estimativa_horas")
                ))

                resultado = cur.fetchone()
                nova_id = resultado[0]
                data_criacao = resultado[1]

                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (nova_id, dados["solicitante"], "CRIAÇÃO", dumps_safe(dados)))

                conn.commit()
                return nova_id
    except Exception as e:
        st.error(f"Erro ao adicionar demanda: {str(e)}")
        return None

def atualizar_demanda(demanda_id, dados):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados_antigos = cur.fetchone()

                cur.execute("""
                    UPDATE demandas
                    SET item = %s, quantidade = %s, solicitante = %s,
                        departamento = %s, prioridade = %s, observacoes = %s,
                        status = %s, categoria = %s, urgencia = %s,
                        estimativa_horas = %s, data_atualizacao = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    dados["item"],
                    dados["quantidade"],
                    dados["solicitante"],
                    dados["departamento"],
                    dados["prioridade"],
                    dados.get("observacoes", ""),
                    dados["status"],
                    dados.get("categoria", "Geral"),
                    dados.get("urgencia", False),
                    dados.get("estimativa_horas"),
                    demanda_id
                ))

                # Registrar no histórico usando o usuário logado
                usuario_atual = st.session_state.get("usuario_nome", "Administrador")
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    demanda_id,
                    usuario_atual,
                    "ATUALIZAÇÃO",
                    dumps_safe({
                        "antigo": dados_antigos if dados_antigos else {},
                        "novo": dados
                    })
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
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados = cur.fetchone()

                # Registrar no histórico usando o usuário logado
                usuario_atual = st.session_state.get("usuario_nome", "Administrador")
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    demanda_id,
                    usuario_atual,
                    "EXCLUSÃO",
                    dumps_safe(dados if dados else {})
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
                # Configurar timezone para Fortaleza
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                estatisticas = {}

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
                totais = cur.fetchone()
                estatisticas["totais"] = totais if totais else {}

                cur.execute("""
                    SELECT departamento, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY departamento
                    ORDER BY quantidade DESC
                """)
                rows = cur.fetchall()
                estatisticas["por_departamento"] = {r["departamento"]: r["quantidade"] for r in rows}

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
                rows = cur.fetchall()
                estatisticas["por_prioridade"] = {r["prioridade"]: r["quantidade"] for r in rows}

                cur.execute("""
                    SELECT status, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY status
                """)
                rows = cur.fetchall()
                estatisticas["por_status"] = {r["status"]: r["quantidade"] for r in rows}

                return estatisticas
    except Exception as e:
        st.error(f"Erro ao obter estatísticas: {str(e)}")
        return {}

# ============================================
# FUNÇÕES PARA PÁGINAS ESPECÍFICAS
# ============================================

def pagina_inicial():
    """Página inicial com opção de solicitação ou administrador"""
    # Mostrar horário atual de Fortaleza
    agora = agora_fortaleza()
    st.sidebar.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")
    
    st.title("🚂 Sistema de Demandas - Railway")
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📝 Nova Solicitação")
        st.markdown("""
        **Para usuários que desejam enviar uma nova demanda:**
        - Preencha o formulário de solicitação
        - Acompanhe o status da sua demanda
        - Receba confirmação imediata
        """)
        if st.button("📄 Enviar Solicitação", type="primary", use_container_width=True, key="btn_solicitacao"):
            st.session_state.pagina_atual = "solicitacao"
            st.rerun()
    
    with col2:
        st.subheader("🔧 Área Administrativa")
        st.markdown("""
        **Para administradores do sistema:**
        - Visualize todas as demandas
        - Gerencie status e prioridades
        - Acesse estatísticas e relatórios
        """)
        if st.button("🔐 Acessar como Administrador", use_container_width=True, key="btn_admin"):
            st.session_state.pagina_atual = "login_admin"
            st.rerun()
    
    st.markdown("---")
    st.caption(f"🕒 Horário atual: {agora.strftime('%d/%m/%Y %H:%M:%S')} (Fortaleza - UTC-3)")
    st.caption("Selecione uma opção para continuar")

def pagina_solicitacao():
    """Página de solicitação para usuários comuns"""
    st.header("📝 Nova Solicitação")
    
    # Mostrar horário atual
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")
    
    # Usar uma variável de sessão para controlar se o formulário foi enviado
    if "solicitacao_enviada" not in st.session_state:
        st.session_state.solicitacao_enviada = False
    if "ultima_demanda_id" not in st.session_state:
        st.session_state.ultima_demanda_id = None
    
    # Se uma solicitação foi enviada, mostrar confirmação
    if st.session_state.solicitacao_enviada:
        st.success(f"✅ Solicitação **#{st.session_state.ultima_demanda_id}** enviada com sucesso!")
        st.balloons()
        
        # Opções após envio
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📝 Enviar Nova Solicitação", use_container_width=True):
                st.session_state.solicitacao_enviada = False
                st.rerun()
        with col2:
            if st.button("🏠 Voltar ao Início", use_container_width=True):
                st.session_state.pagina_atual = "inicio"
                st.session_state.solicitacao_enviada = False
                st.rerun()
        
        return
    
    # Formulário de solicitação
    with st.form("form_nova_demanda", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            solicitante = st.text_input("👤 Nome do Solicitante*")
            departamento = st.selectbox(
                "🏢 Departamento*",
                ["Administrativo", "Gestão", "Operação", "Açudes", "EB",
                 "Outro"]
            )
            categoria = st.selectbox(
                "📂 Categoria",
                ["Combustível", "Materiais", "Equipamentos", "Ferramentas", "Alimentos",
                 "Lubrificantes", "Outro"]
            )

        with col2:
            item = st.text_area("📝 Descrição da Demanda*", height=100)
            quantidade = st.number_input("🔢 Quantidade*", min_value=1, value=1, step=1)
            unidade = st.selectbox("📏 Unidade", ["Kg", "Litros", "Und.", "Metros"])

        col3, col4 = st.columns(2)
        with col3:
            prioridade = st.selectbox("🚨 Prioridade", ["Baixa", "Média", "Alta", "Urgente"], index=1)
            urgencia = st.checkbox("🚨 É urgente?")

        with col4:
            observacoes = st.text_area("💬 Observações Adicionais", height=100)

        submitted = st.form_submit_button("✅ Enviar Solicitação", type="primary")

        if submitted:
            if solicitante and item and departamento:
                nova_demanda = {
                    "item": item,
                    "quantidade": int(quantidade),
                    "solicitante": solicitante,
                    "departamento": departamento,
                    "prioridade": prioridade,
                    "observacoes": observacoes,
                    "categoria": categoria,
                    "urgencia": bool(urgencia),
                    "estimativa_horas": float(estimativa_horas) if estimativa_horas and estimativa_horas > 0 else None
                }

                demanda_id = adicionar_demanda(nova_demanda)

                if demanda_id:
                    # Salvar estado da sessão
                    st.session_state.solicitacao_enviada = True
                    st.session_state.ultima_demanda_id = demanda_id
                    st.rerun()
                else:
                    st.error("❌ Erro ao salvar a solicitação.")
            else:
                st.error("⚠️ Por favor, preencha todos os campos obrigatórios (*)")
    
    # Botão para voltar ao início
    if st.button("← Voltar ao Início", key="voltar_solicitacao"):
        st.session_state.pagina_atual = "inicio"
        st.session_state.solicitacao_enviada = False
        st.rerun()

def pagina_login_admin():
    """Página de login para administradores"""
    st.title("🔧 Área Administrativa")
    st.markdown("---")
    
    # Mostrar horário atual
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")
    
    st.warning("🔒 Acesso Restrito - Autenticação Necessária")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_admin_login"):
            username = st.text_input("👤 Username")
            senha = st.text_input("🔑 Senha:", type="password")
            login_submit = st.form_submit_button("🔓 Entrar", type="primary")
            
            if login_submit:
                if username and senha:
                    usuario = autenticar_usuario(username, senha)
                    if usuario:
                        # Salvar informações do usuário na sessão
                        st.session_state.usuario_logado = True
                        st.session_state.usuario_id = usuario["id"]
                        st.session_state.usuario_nome = usuario["nome"]
                        st.session_state.usuario_username = usuario["username"]
                        st.session_state.usuario_nivel = usuario["nivel_acesso"]
                        st.session_state.usuario_admin = usuario["is_admin"]
                        
                        st.session_state.pagina_atual = "admin"
                        st.success(f"✅ Bem-vindo, {usuario['nome']}!")
                        st.rerun()
                    else:
                        st.error("❌ Credenciais inválidas ou usuário inativo!")
                else:
                    st.error("⚠️ Preencha todos os campos!")
    
    # Botão para voltar ao início
    if st.button("← Voltar ao Início", key="voltar_login"):
        st.session_state.pagina_atual = "inicio"
        st.rerun()

def pagina_gerenciar_usuarios():
    """Página para gerenciar usuários do sistema"""
    st.header("👥 Gerenciamento de Usuários")
    
    # Mostrar horário atual
    agora = agora_fortaleza()
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M')}")
    
    # Verificar permissões
    if not st.session_state.get("usuario_admin", False):
        st.error("⛔ Acesso negado! Apenas administradores podem gerenciar usuários.")
        return
    
    tab1, tab2 = st.tabs(["📋 Lista de Usuários", "➕ Novo Usuário"])
    
    with tab1:
        st.subheader("📋 Usuários do Sistema")
        usuarios = listar_usuarios()
        
        if usuarios:
            df_usuarios = pd.DataFrame(usuarios)
            
            # Converter valores booleanos para texto
            df_usuarios["is_admin"] = df_usuarios["is_admin"].apply(lambda x: "✅" if x else "❌")
            df_usuarios["ativo"] = df_usuarios["ativo"].apply(lambda x: "✅" if x else "❌")
            
            st.dataframe(
                df_usuarios,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "id": "ID",
                    "nome": "Nome",
                    "email": "Email",
                    "username": "Username",
                    "departamento": "Departamento",
                    "nivel_acesso": "Nível",
                    "is_admin": "Admin",
                    "ativo": "Ativo",
                    "data_cadastro": "Cadastro",
                    "ultimo_login": "Último Login"
                }
            )
            
            # Opções de gerenciamento por usuário
            st.subheader("⚙️ Gerenciar Usuário")
            usuarios_opcoes = [f"{u['id']} - {u['nome']} ({u['username']})" for u in usuarios]
            usuario_selecionado = st.selectbox("Selecione um usuário:", usuarios_opcoes)
            
            if usuario_selecionado:
                usuario_id = int(usuario_selecionado.split(" - ")[0])
                usuario_info = next((u for u in usuarios if u["id"] == usuario_id), None)
                
                if usuario_info:
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        if st.button("🔄 Alterar Nível", key=f"nivel_{usuario_id}"):
                            st.session_state.editar_usuario_id = usuario_id
                            st.session_state.editar_campo = "nivel"
                            st.rerun()
                    
                    with col2:
                        if usuario_info["ativo"]:
                            if st.button("❌ Desativar", key=f"desativar_{usuario_id}"):
                                sucesso, mensagem = excluir_usuario(usuario_id)
                                if sucesso:
                                    st.success(mensagem)
                                    st.rerun()
                                else:
                                    st.error(mensagem)
                        else:
                            if st.button("✅ Reativar", key=f"reativar_{usuario_id}"):
                                sucesso, mensagem = atualizar_usuario(usuario_id, {"ativo": True})
                                if sucesso:
                                    st.success(mensagem)
                                    st.rerun()
                                else:
                                    st.error(mensagem)
                    
                    with col3:
                        if st.button("🔑 Redefinir Senha", key=f"senha_{usuario_id}"):
                            st.session_state.editar_usuario_id = usuario_id
                            st.session_state.editar_campo = "senha"
                            st.rerun()
                    
                    # Modal para edição
                    if "editar_usuario_id" in st.session_state and st.session_state.editar_usuario_id == usuario_id:
                        with st.expander("✏️ Editar Usuário", expanded=True):
                            if st.session_state.editar_campo == "nivel":
                                novo_nivel = st.selectbox(
                                    "Novo Nível de Acesso:",
                                    ["usuario", "supervisor", "administrador"],
                                    index=["usuario", "supervisor", "administrador"].index(usuario_info["nivel_acesso"])
                                )
                                
                                if st.button("💾 Salvar Alteração"):
                                    sucesso, mensagem = atualizar_usuario(usuario_id, {
                                        "nivel_acesso": novo_nivel,
                                        "is_admin": (novo_nivel == "administrador")
                                    })
                                    if sucesso:
                                        st.success(mensagem)
                                        del st.session_state.editar_usuario_id
                                        del st.session_state.editar_campo
                                        st.rerun()
                                    else:
                                        st.error(mensagem)
                                
                                if st.button("❌ Cancelar"):
                                    del st.session_state.editar_usuario_id
                                    del st.session_state.editar_campo
                                    st.rerun()
                            
                            elif st.session_state.editar_campo == "senha":
                                nova_senha = st.text_input("Nova Senha:", type="password")
                                confirmar_senha = st.text_input("Confirmar Senha:", type="password")
                                
                                if st.button("🔐 Alterar Senha"):
                                    if nova_senha and nova_senha == confirmar_senha:
                                        sucesso, mensagem = atualizar_usuario(usuario_id, {"senha": nova_senha})
                                        if sucesso:
                                            st.success("✅ Senha alterada com sucesso!")
                                            del st.session_state.editar_usuario_id
                                            del st.session_state.editar_campo
                                            st.rerun()
                                        else:
                                            st.error(mensagem)
                                    else:
                                        st.error("⚠️ As senhas não coincidem!")
                                
                                if st.button("❌ Cancelar"):
                                    del st.session_state.editar_usuario_id
                                    del st.session_state.editar_campo
                                    st.rerun()
        else:
            st.info("Nenhum usuário cadastrado no sistema.")
    
    with tab2:
        st.subheader("➕ Cadastrar Novo Usuário")
        
        with st.form("form_novo_usuario"):
            col1, col2 = st.columns(2)
            
            with col1:
                nome = st.text_input("Nome Completo*")
                email = st.text_input("Email*")
                username = st.text_input("Username*")
            
            with col2:
                departamento = st.selectbox(
                    "Departamento",
                    ["Administrativo", "Gestão", "Operação", "Açudes", "EB", "TI", "RH", "Financeiro", "Outro"]
                )
                nivel_acesso = st.selectbox(
                    "Nível de Acesso",
                    ["usuario", "supervisor", "administrador"],
                    help="usuário: apenas visualização, supervisor: edição limitada, administrador: acesso total"
                )
                senha = st.text_input("Senha*", type="password")
                confirmar_senha = st.text_input("Confirmar Senha*", type="password")
            
            criar = st.form_submit_button("✅ Criar Usuário", type="primary")
            
            if criar:
                if not all([nome, email, username, senha, confirmar_senha]):
                    st.error("⚠️ Preencha todos os campos obrigatórios!")
                elif senha != confirmar_senha:
                    st.error("❌ As senhas não coincidem!")
                else:
                    dados_usuario = {
                        "nome": nome,
                        "email": email,
                        "username": username,
                        "senha": senha,
                        "departamento": departamento,
                        "nivel_acesso": nivel_acesso,
                        "is_admin": (nivel_acesso == "administrador")
                    }
                    
                    sucesso, mensagem = criar_usuario(dados_usuario)
                    if sucesso:
                        st.success(f"✅ {mensagem}")
                        st.balloons()
                        st.rerun()
                    else:
                        st.error(f"❌ {mensagem}")

def pagina_admin():
    """Página principal do administrador com sidebar"""
    # Verificar se usuário está logado
    if not st.session_state.get("usuario_logado", False):
        st.session_state.pagina_atual = "login_admin"
        st.rerun()
        return
    
    # Mostrar horário atual no sidebar
    agora = agora_fortaleza()
    st.sidebar.caption(f"🕒 {agora.strftime('%d/%m/%Y %H:%M')} (Fortaleza)")
    
    # Configurar sidebar para admin
    st.sidebar.title("🔧 Administração")
    
    # Informações do usuário
    st.sidebar.markdown(f"**👤 {st.session_state.get('usuario_nome', 'Usuário')}**")
    st.sidebar.caption(f"Nível: {st.session_state.get('usuario_nivel', 'usuário').title()}")
    st.sidebar.markdown("---")
    
    # Menu de navegação para admin (baseado no nível)
    usuario_nivel = st.session_state.get("usuario_nivel", "usuario")
    usuario_admin = st.session_state.get("usuario_admin", False)
    
    menu_opcoes = ["🏠 Dashboard", "📋 Todas as Demandas", "✏️ Editar Demanda", "📊 Estatísticas"]
    
    # Apenas administradores podem gerenciar usuários
    if usuario_admin:
        menu_opcoes.append("👥 Gerenciar Usuários")
    
    menu_opcoes.append("⚙️ Configurações")
    
    menu_selecionado = st.sidebar.radio("Navegação", menu_opcoes)
    
    # Filtros na sidebar
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 Filtros")
    
    status_filtro = st.sidebar.multiselect(
        "Status",
        ["Pendente", "Em andamento", "Concluída", "Cancelada"],
        default=["Pendente", "Em andamento"]
    )

    prioridade_filtro = st.sidebar.multiselect(
        "Prioridade",
        ["Urgente", "Alta", "Média", "Baixa"],
        default=["Urgente", "Alta", "Média"]
    )
    
    # Botão de logout
    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Logout", type="secondary"):
        # Limpar sessão
        for key in ['usuario_logado', 'usuario_id', 'usuario_nome', 'usuario_username', 'usuario_nivel', 'usuario_admin']:
            st.session_state.pop(key, None)
        st.session_state.pagina_atual = "inicio"
        st.rerun()
    
    # Aplicar filtros
    filtros = {}
    if status_filtro:
        filtros["status"] = status_filtro
    if prioridade_filtro:
        filtros["prioridade"] = prioridade_filtro
    
    # Mostrar horário atual no conteúdo principal
    st.caption(f"🕒 Horário Fortaleza: {agora.strftime('%d/%m/%Y %H:%M:%S')}")
    
    # Conteúdo principal baseado na seleção do menu
    if menu_selecionado == "🏠 Dashboard":
        st.header("📊 Dashboard de Demandas")
        
        estatisticas = obter_estatisticas()
        
        if estatisticas:
            totais = estatisticas.get("totais", {})
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Demandas", totais.get("total", 0))
            with col2:
                st.metric("Pendentes", totais.get("pendentes", 0))
            with col3:
                st.metric("Urgentes", totais.get("urgentes", 0), delta_color="inverse")
            with col4:
                st.metric("Total Itens", totais.get("total_itens", 0))
            
            col1, col2 = st.columns(2)
            with col1:
                if estatisticas.get("por_departamento"):
                    st.subheader("Por Departamento")
                    df_dept = pd.DataFrame(
                        list(estatisticas["por_departamento"].items()),
                        columns=["Departamento", "Quantidade"]
                    )
                    st.bar_chart(df_dept.set_index("Departamento"))
            
            with col2:
                if estatisticas.get("por_prioridade"):
                    st.subheader("Por Prioridade")
                    df_pri = pd.DataFrame(
                        list(estatisticas["por_prioridade"].items()),
                        columns=["Prioridade", "Quantidade"]
                    )
                    st.bar_chart(df_pri.set_index("Prioridade"))
            
            st.subheader("📋 Últimas Solicitações")
            demandas_recentes = carregar_demandas(filtros)[:10]
            
            if demandas_recentes:
                df_recentes = pd.DataFrame(demandas_recentes)
                df_display = df_recentes.rename(columns={
                    "id": "ID",
                    "item": "Item",
                    "quantidade": "Qtd",
                    "solicitante": "Solicitante",
                    "departamento": "Depto",
                    "prioridade": "Prioridade",
                    "status": "Status",
                    "data_criacao_formatada": "Data Criação",
                    "data_atualizacao_formatada": "Última Atualização"
                })
                st.dataframe(
                    df_display[["ID", "Item", "Qtd", "Solicitante", "Depto", "Prioridade", "Status", "Data Criação"]],
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("Nenhuma demanda encontrada com os filtros atuais.")
        else:
            st.info("Sem estatísticas ainda. Verifique a conexão com o banco.")
    
    elif menu_selecionado == "📋 Todas as Demandas":
        st.header("📋 Todas as Demandas")
        
        todas_demandas = carregar_demandas(filtros)
        
        if todas_demandas:
            df_admin = pd.DataFrame(todas_demandas)
            
            # Campo de busca
            busca = st.text_input("🔎 Buscar por texto (item ou solicitante):")
            if busca:
                filtros["search"] = busca
                todas_demandas = carregar_demandas(filtros)
                df_admin = pd.DataFrame(todas_demandas)
            
            st.info(f"📊 Encontradas **{len(todas_demandas)}** demandas")
            
            # Ações rápidas (apenas para administradores e supervisores)
            if usuario_nivel in ["supervisor", "administrador"]:
                col_acao1, col_acao2, col_acao3 = st.columns(3)
                with col_acao1:
                    if st.button("📥 Exportar para CSV"):
                        csv = df_admin.to_csv(index=False)
                        st.download_button(
                            label="Baixar CSV",
                            data=csv,
                            file_name=f"demandas_export_{agora.strftime('%Y%m%d_%H%M%S')}.csv",
                            mime="text/csv"
                        )
            
            st.dataframe(
                df_admin,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "id": "ID",
                    "item": "Item",
                    "quantidade": "Qtd",
                    "solicitante": "Solicitante",
                    "departamento": "Depto",
                    "prioridade": "Prioridade",
                    "status": "Status",
                    "data_criacao_formatada": "Criação",
                    "data_atualizacao_formatada": "Última Atualização",
                },
            )
        else:
            st.info("Ainda não existem demandas cadastradas.")
    
    elif menu_selecionado == "✏️ Editar Demanda":
        # Verificar permissão
        if usuario_nivel not in ["supervisor", "administrador"]:
            st.error("⛔ Acesso negado! Apenas supervisores e administradores podem editar demandas.")
            return
            
        st.header("✏️ Editar Demanda")
        todas_demandas = carregar_demandas()
        
        if todas_demandas:
            opcoes_demanda = [f"#{d['id']} - {d['item'][:50]}... (Criado: {d.get('data_criacao_formatada', 'N/A')})" for d in todas_demandas]
            selecao = st.selectbox("Selecione uma demanda:", opcoes_demanda)
            
            if selecao:
                demanda_id = int(selecao.split("#")[1].split(" - ")[0])
                demanda_atual = next((d for d in todas_demandas if d["id"] == demanda_id), None)
                
                if demanda_atual:
                    st.caption(f"📅 Criado em: {demanda_atual.get('data_criacao_formatada', 'N/A')}")
                    st.caption(f"🔄 Última atualização: {demanda_atual.get('data_atualizacao_formatada', 'N/A')}")
                    
                    departamentos_lista = [
                        "TI", "RH", "Financeiro", "Comercial", "Operações",
                        "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"
                    ]
                    status_lista = ["Pendente", "Em andamento", "Concluída", "Cancelada"]
                    prioridade_lista = ["Baixa", "Média", "Alta", "Urgente"]
                    
                    # Encontrar índices corretos
                    try:
                        dep_index = departamentos_lista.index(demanda_atual["departamento"])
                    except ValueError:
                        dep_index = len(departamentos_lista) - 1  # Último item (Outro)
                    
                    try:
                        pri_index = prioridade_lista.index(demanda_atual["prioridade"])
                    except ValueError:
                        pri_index = 1  # Média como padrão
                    
                    try:
                        st_index = status_lista.index(demanda_atual["status"])
                    except ValueError:
                        st_index = 0  # Pendente como padrão
                    
                    with st.form(f"form_editar_{demanda_id}"):
                        col_e1, col_e2 = st.columns(2)
                        
                        with col_e1:
                            item_edit = st.text_area("Descrição", value=demanda_atual["item"], height=100)
                            quantidade_edit = st.number_input("Quantidade", min_value=1, value=int(demanda_atual["quantidade"]))
                            solicitante_edit = st.text_input("Solicitante", value=demanda_atual["solicitante"])
                            departamento_edit = st.selectbox("Departamento", departamentos_lista, index=dep_index)
                        
                        with col_e2:
                            prioridade_edit = st.selectbox("Prioridade", prioridade_lista, index=pri_index)
                            status_edit = st.selectbox("Status", status_lista, index=st_index)
                            categoria_edit = st.text_input("Categoria", value=demanda_atual.get("categoria") or "Geral")
                            urgencia_edit = st.checkbox("Urgente", value=bool(demanda_atual.get("urgencia", False)))
                            observacoes_edit = st.text_area("Observações", value=demanda_atual.get("observacoes") or "", height=100)
                        
                        col_botoes1, col_botoes2, col_botoes3 = st.columns(3)
                        with col_botoes1:
                            salvar = st.form_submit_button("💾 Salvar Alterações", type="primary")
                        with col_botoes2:
                            # Apenas administradores podem excluir
                            if usuario_admin:
                                excluir = st.form_submit_button("🗑️ Excluir Demanda", type="secondary")
                            else:
                                excluir = False
                        with col_botoes3:
                            cancelar = st.form_submit_button("↻ Cancelar")
                        
                        if salvar:
                            dados_atualizados = {
                                "item": item_edit,
                                "quantidade": int(quantidade_edit),
                                "solicitante": solicitante_edit,
                                "departamento": departamento_edit,
                                "prioridade": prioridade_edit,
                                "status": status_edit,
                                "categoria": categoria_edit,
                                "urgencia": bool(urgencia_edit),
                                "observacoes": observacoes_edit,
                                "estimativa_horas": demanda_atual.get("estimativa_horas"),
                            }
                            if atualizar_demanda(demanda_id, dados_atualizados):
                                st.success(f"✅ Demanda #{demanda_id} atualizada com sucesso!")
                                st.rerun()
                            else:
                                st.error("❌ Erro ao atualizar demanda")
                        
                        if excluir and usuario_admin:
                            if excluir_demanda(demanda_id):
                                st.warning(f"⚠️ Demanda #{demanda_id} excluída!")
                                st.rerun()
                            else:
                                st.error("❌ Erro ao excluir demanda")
                        
                        if cancelar:
                            st.rerun()
        else:
            st.info("Não existem demandas para editar ainda.")
    
    elif menu_selecionado == "👥 Gerenciar Usuários":
        pagina_gerenciar_usuarios()
    
    elif menu_selecionado == "📊 Estatísticas":
        st.header("📊 Estatísticas Avançadas")
        estatisticas = obter_estatisticas()
        
        if estatisticas:
            totais = estatisticas.get("totais", {})
            st.metric("Total de Horas Estimadas", f"{float(totais.get('total_horas', 0) or 0):.1f}h")
            
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                if estatisticas.get("por_status"):
                    st.subheader("Distribuição por Status")
                    df_status = pd.DataFrame(
                        list(estatisticas["por_status"].items()),
                        columns=["Status", "Quantidade"]
                    )
                    st.bar_chart(df_status.set_index("Status"))
            
            with col_s2:
                try:
                    with get_db_connection() as conn:
                        with conn.cursor() as cur:
                            # Configurar timezone para Fortaleza
                            cur.execute("SET TIME ZONE 'America/Fortaleza'")
                            
                            cur.execute("""
                                SELECT DATE(data_criacao) as data, COUNT(*) as quantidade
                                FROM demandas
                                WHERE data_criacao >= CURRENT_DATE - INTERVAL '7 days'
                                GROUP BY DATE(data_criacao)
                                ORDER BY data
                            """)
                            dados_periodo = cur.fetchall()
                    
                    if dados_periodo:
                        df_periodo = pd.DataFrame(dados_periodo, columns=["Data", "Quantidade"])
                        st.subheader("Demandas nos últimos 7 dias")
                        st.line_chart(df_periodo.set_index("Data"))
                    else:
                        st.info("Sem dados nos últimos 7 dias.")
                except Exception:
                    st.info("Não foi possível carregar dados temporais")
    
    elif menu_selecionado == "⚙️ Configurações":
        st.header("⚙️ Configurações do Sistema")
        
        # Informações do usuário atual
        with st.expander("👤 Meus Dados", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                st.info(f"**Nome:** {st.session_state.get('usuario_nome', 'N/A')}")
                st.info(f"**Username:** {st.session_state.get('usuario_username', 'N/A')}")
            with col2:
                st.info(f"**Nível:** {st.session_state.get('usuario_nivel', 'N/A').title()}")
                st.info(f"**Admin:** {'✅ Sim' if st.session_state.get('usuario_admin', False) else '❌ Não'}")
        
        # Configurações do sistema
        with st.expander("🔧 Configurações do Banco de Dados"):
            cfg = get_db_config()
            st.code(
                "Host: {h}\nDatabase: {d}\nUser: {u}\nPort: {p}\nSSL: {s}\nTimezone: America/Fortaleza".format(
                    h=cfg.get("host", "N/A"),
                    d=cfg.get("database", "N/A"),
                    u=cfg.get("user", "N/A"),
                    p=cfg.get("port", "N/A"),
                    s=cfg.get("sslmode", "N/A"),
                )
            )
            
            if st.button("🔄 Testar Conexão com Banco"):
                conexao_ok, mensagem = test_db_connection()
                if conexao_ok:
                    st.success(mensagem)
                else:
                    st.error(mensagem)
        
        # Estatísticas do sistema
        with st.expander("📈 Informações do Sistema"):
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        # Configurar timezone para Fortaleza
                        cur.execute("SET TIME ZONE 'America/Fortaleza'")
                        
                        # Demandas
                        cur.execute("""
                            SELECT
                                COUNT(*) as total_demandas,
                                MIN(data_criacao) as primeira_demanda,
                                MAX(data_criacao) as ultima_demanda
                            FROM demandas
                        """)
                        info_demandas = cur.fetchone()
                        
                        # Usuários
                        cur.execute("""
                            SELECT
                                COUNT(*) as total_usuarios,
                                COUNT(CASE WHEN ativo = TRUE THEN 1 END) as usuarios_ativos,
                                COUNT(CASE WHEN is_admin = TRUE THEN 1 END) as administradores
                            FROM usuarios
                        """)
                        info_usuarios = cur.fetchone()
                
                if info_demandas and info_usuarios:
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric("Total de Demandas", info_demandas[0])
                        if info_demandas[1]:
                            primeira_fortaleza = converter_para_fortaleza(info_demandas[1])
                            st.caption(f"Primeira demanda: {primeira_fortaleza.strftime('%d/%m/%Y %H:%M')}")
                        if info_demandas[2]:
                            ultima_fortaleza = converter_para_fortaleza(info_demandas[2])
                            st.caption(f"Última demanda: {ultima_fortaleza.strftime('%d/%m/%Y %H:%M')}")
                    
                    with col2:
                        st.metric("Usuários Ativos", info_usuarios[1])
                        st.caption(f"Total de usuários: {info_usuarios[0]}")
                        st.caption(f"Administradores: {info_usuarios[2]}")
            except Exception:
                st.info("Não foi possível carregar informações do sistema")

# ============================================
# INICIALIZAÇÃO E ROTEAMENTO
# ============================================

# Inicializar estado da sessão
if "init_complete" not in st.session_state:
    conexao_ok, mensagem = test_db_connection()
    if conexao_ok:
        init_ok, init_msg = init_database()
        if init_ok:
            st.session_state.init_complete = True
        else:
            st.warning(init_msg)
    else:
        st.error(mensagem)
        st.session_state.demo_mode = True

if "pagina_atual" not in st.session_state:
    st.session_state.pagina_atual = "inicio"

# Inicializar variáveis de sessão para autenticação
if "usuario_logado" not in st.session_state:
    st.session_state.usuario_logado = False

if "filtros" not in st.session_state:
    st.session_state.filtros = {}

# Inicializar variáveis de sessão para página de solicitação
if "solicitacao_enviada" not in st.session_state:
    st.session_state.solicitacao_enviada = False
if "ultima_demanda_id" not in st.session_state:
    st.session_state.ultima_demanda_id = None

# Roteamento das páginas
if st.session_state.pagina_atual == "inicio":
    pagina_inicial()
elif st.session_state.pagina_atual == "solicitacao":
    pagina_solicitacao()
elif st.session_state.pagina_atual == "login_admin":
    pagina_login_admin()
elif st.session_state.pagina_atual == "admin":
    pagina_admin()
else:
    # Página padrão
    st.session_state.pagina_atual = "inicio"
    st.rerun()

# ============================================
# RODAPÉ
# ============================================

# Mostrar informações de conexão apenas nas páginas apropriadas
if st.session_state.pagina_atual in ["admin", "solicitacao"]:
    st.sidebar.markdown("---")
    
    if DATABASE_URL:
        st.sidebar.success("✅ Conectado ao Railway Postgres")
        if st.sidebar.checkbox("Mostrar informações de debug", key="debug_info"):
            cfg = get_db_config()
            st.sidebar.text(f"Host: {cfg.get('host')}")
            st.sidebar.text(f"Database: {cfg.get('database')}")
            st.sidebar.text(f"User: {cfg.get('user')}")
            st.sidebar.text(f"Port: {cfg.get('port')}")
            st.sidebar.text(f"Timezone: America/Fortaleza")
    else:
        st.sidebar.warning("⚠️ DATABASE_URL não encontrada")
    
    st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas v2.0")
    st.sidebar.caption("Conectado ao Railway PostgreSQL")
