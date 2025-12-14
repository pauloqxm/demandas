import streamlit as st
import pandas as pd
import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import os
from urllib.parse import urlparse

# =============================
# Configuração da página
# =============================
st.set_page_config(
    page_title="Sistema de Demandas - Railway",
    page_icon="🚂",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================
# CONFIGURAÇÃO DA CONEXÃO COM RAILWAY POSTGRES
# ============================================

DATABASE_URL = os.environ.get("DATABASE_URL")

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

# Senha de administrador (Railway: defina ADMIN_PASSWORD em Variables)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

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

def init_database():
    """Inicializa o banco de dados no Railway"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
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

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS usuarios (
                        id SERIAL PRIMARY KEY,
                        nome VARCHAR(200) NOT NULL,
                        email VARCHAR(200) UNIQUE NOT NULL,
                        departamento VARCHAR(100),
                        is_admin BOOLEAN DEFAULT FALSE,
                        data_cadastro TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_status ON demandas(status)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_departamento ON demandas(departamento)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_prioridade ON demandas(prioridade)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_data_criacao ON demandas(data_criacao DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_historico_demanda_id ON historico_demandas(demanda_id)")

                conn.commit()

        return True, "✅ Banco de dados inicializado com sucesso!"
    except Exception as e:
        return False, f"❌ Erro ao inicializar banco: {str(e)}"

# ============================================
# FUNÇÕES DO SISTEMA
# ============================================

def carregar_demandas(filtros=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT id, item, quantidade, solicitante, departamento,
                           prioridade, observacoes, status, categoria, urgencia,
                           TO_CHAR(data_criacao, 'DD/MM/YYYY HH24:MI') as data_criacao_formatada,
                           TO_CHAR(data_atualizacao, 'DD/MM/YYYY HH24:MI') as data_atualizacao_formatada,
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
                return cur.fetchall()
    except Exception as e:
        st.error(f"Erro ao carregar demandas: {str(e)}")
        return []

def adicionar_demanda(dados):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO demandas
                    (item, quantidade, solicitante, departamento, prioridade, observacoes, categoria, urgencia, estimativa_horas)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
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

                nova_id = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (nova_id, dados["solicitante"], "CRIAÇÃO", json.dumps(dados)))

                conn.commit()
                return nova_id
    except Exception as e:
        st.error(f"Erro ao adicionar demanda: {str(e)}")
        return None

def atualizar_demanda(demanda_id, dados):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados_antigos = cur.fetchone()

        with get_db_connection() as conn:
            with conn.cursor() as cur:
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

                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    demanda_id,
                    "Administrador",
                    "ATUALIZAÇÃO",
                    json.dumps({
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
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados = cur.fetchone()

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (
                    demanda_id,
                    "Administrador",
                    "EXCLUSÃO",
                    json.dumps(dados if dados else {})
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
# INTERFACE STREAMLIT
# ============================================

if "init_complete" not in st.session_state:
    conexao_ok, mensagem = test_db_connection()
    if conexao_ok:
        init_ok, init_msg = init_database()
        if init_ok:
            st.session_state.init_complete = True
            st.sidebar.success(mensagem)
        else:
            st.sidebar.warning(init_msg)
    else:
        st.sidebar.error(mensagem)
        st.session_state.demo_mode = True
    st.session_state.filtros = {}

st.title("🚂 Sistema de Demandas - Railway")
st.caption("Gerenciamento centralizado de solicitações da equipe")

with st.sidebar:
    st.header("🔧 Configuração")

    if DATABASE_URL:
        st.success("✅ Conectado ao Railway Postgres")
        cfg = get_db_config()
        st.info(
            f"**Host:** {cfg.get('host', 'N/A')}\n"
            f"**Banco:** {cfg.get('database', 'N/A')}\n"
            f"**Usuário:** {cfg.get('user', 'N/A')}"
        )
    else:
        st.warning("⚠️ DATABASE_URL não encontrada")
        st.info("Configure a variável DATABASE_URL no Railway")

    st.header("📋 Navegação")
    menu_opcoes = ["🏠 Dashboard", "📝 Nova Demanda", "🔍 Buscar Demandas", "⚙️ Administração"]
    menu_selecionado = st.radio("Selecione uma opção:", menu_opcoes)

    if menu_selecionado in ["🔍 Buscar Demandas", "🏠 Dashboard"]:
        st.subheader("🔎 Filtros Rápidos")

        status_filtro = st.multiselect(
            "Status",
            ["Pendente", "Em andamento", "Concluída", "Cancelada"],
            default=["Pendente", "Em andamento"]
        )

        prioridade_filtro = st.multiselect(
            "Prioridade",
            ["Urgente", "Alta", "Média", "Baixa"],
            default=["Urgente", "Alta", "Média"]
        )

        if st.button("Aplicar Filtros"):
            st.session_state.filtros = {
                "status": status_filtro,
                "prioridade": prioridade_filtro
            }
            st.rerun()

        if st.button("Limpar Filtros"):
            st.session_state.filtros = {}
            st.rerun()

# ============================================
# PÁGINAS DO SISTEMA
# ============================================

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
        demandas_recentes = carregar_demandas(st.session_state.filtros)[:10]

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
                "data_criacao_formatada": "Data"
            })
            st.dataframe(
                df_display[["ID", "Item", "Qtd", "Solicitante", "Depto", "Prioridade", "Status", "Data"]],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhuma demanda encontrada com os filtros atuais.")
    else:
        st.info("Sem estatísticas ainda. Verifique a conexão com o banco.")

elif menu_selecionado == "📝 Nova Demanda":
    st.header("➕ Nova Solicitação")

    with st.form("form_nova_demanda", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            solicitante = st.text_input("👤 Nome do Solicitante*")
            departamento = st.selectbox(
                "🏢 Departamento*",
                ["TI", "RH", "Financeiro", "Comercial", "Operações",
                 "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"]
            )
            categoria = st.selectbox(
                "📂 Categoria",
                ["Geral", "Hardware", "Software", "Infraestrutura", "Suporte",
                 "Treinamento", "Documentação", "Outro"]
            )

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
                    st.success(f"✅ Solicitação **#{demanda_id}** enviada com sucesso!")
                    st.balloons()
                    with st.expander("📋 Ver Resumo da Solicitação"):
                        st.json(nova_demanda)
                else:
                    st.error("❌ Erro ao salvar a solicitação.")
            else:
                st.error("⚠️ Por favor, preencha todos os campos obrigatórios (*)")

elif menu_selecionado == "🔍 Buscar Demandas":
    st.header("🔍 Buscar e Gerenciar Demandas")

    col1, col2 = st.columns([3, 1])
    with col1:
        busca = st.text_input("🔎 Buscar por texto (item ou solicitante):")
    with col2:
        if busca:
            st.session_state.filtros["search"] = busca
        else:
            st.session_state.filtros.pop("search", None)

    demandas = carregar_demandas(st.session_state.filtros)

    if demandas:
        st.info(f"📊 Encontradas **{len(demandas)}** demandas")
        df = pd.DataFrame(demandas)

        colunas_disponiveis = [
            "id", "item", "quantidade", "solicitante", "departamento",
            "prioridade", "status", "data_criacao_formatada", "categoria"
        ]

        colunas_selecionadas = st.multiselect(
            "👁️ Colunas para exibir:",
            colunas_disponiveis,
            default=["id", "item", "quantidade", "solicitante", "departamento", "prioridade", "status"]
        )

        if colunas_selecionadas:
            st.dataframe(df[colunas_selecionadas], use_container_width=True, hide_index=True)

            col_exp1, col_exp2 = st.columns(2)
            with col_exp1:
                if st.button("📥 Exportar para CSV"):
                    csv = df.to_csv(index=False)
                    st.download_button(
                        label="Baixar CSV",
                        data=csv,
                        file_name=f"demandas_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
    else:
        st.info("🔍 Nenhuma demanda encontrada com os critérios atuais.")

elif menu_selecionado == "⚙️ Administração":
    st.header("⚙️ Área de Administração")

    if "admin_autenticado" not in st.session_state:
        st.session_state.admin_autenticado = False

    if not st.session_state.admin_autenticado:
        st.warning("🔒 Área restrita - Autenticação necessária")

        with st.form("form_admin_login"):
            senha = st.text_input("🔑 Senha de administrador:", type="password")
            login_submit = st.form_submit_button("🔓 Entrar")

            if login_submit:
                if senha == ADMIN_PASSWORD:
                    st.session_state.admin_autenticado = True
                    st.rerun()
                else:
                    st.error("❌ Senha incorreta!")
    else:
        if st.sidebar.button("🚪 Logout"):
            st.session_state.admin_autenticado = False
            st.rerun()

        st.success("✅ Autenticado como administrador")

        tab1, tab2, tab3, tab4 = st.tabs(
            ["📋 Todas as Demandas", "✏️ Editar Demanda", "📊 Estatísticas Avançadas", "⚙️ Configurações"]
        )

        with tab1:
            st.subheader("📋 Todas as Demandas")
            todas_demandas = carregar_demandas()

            if todas_demandas:
                df_admin = pd.DataFrame(todas_demandas)

                st.subheader("⚡ Ações em Massa")
                col_acao1, col_acao2, col_acao3 = st.columns(3)
                with col_acao1:
                    if st.button("🔄 Marcar todas como Concluídas"):
                        st.warning("Funcionalidade em desenvolvimento")
                with col_acao2:
                    if st.button("📊 Gerar Relatório Completo"):
                        st.info("Relatório sendo gerado...")
                with col_acao3:
                    if st.button("🧹 Limpar Histórico Antigo"):
                        st.warning("Esta ação não pode ser desfeita!")

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

        with tab2:
            st.subheader("✏️ Editar Demanda")
            todas_demandas = carregar_demandas()

            if todas_demandas:
                opcoes_demanda = [f"#{d['id']} - {d['item'][:50]}..." for d in todas_demandas]
                selecao = st.selectbox("Selecione uma demanda:", opcoes_demanda)

                if selecao:
                    demanda_id = int(selecao.split("#")[1].split(" - ")[0])
                    demanda_atual = next((d for d in todas_demandas if d["id"] == demanda_id), None)

                    if demanda_atual:
                        departamentos_lista = [
                            "TI", "RH", "Financeiro", "Comercial", "Operações",
                            "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"
                        ]
                        status_lista = ["Pendente", "Em andamento", "Concluída", "Cancelada"]
                        prioridade_lista = ["Baixa", "Média", "Alta", "Urgente"]

                        dep_index = departamentos_lista.index(demanda_atual["departamento"]) if demanda_atual["departamento"] in departamentos_lista else len(departamentos_lista) - 1
                        pri_index = prioridade_lista.index(demanda_atual["prioridade"]) if demanda_atual["prioridade"] in prioridade_lista else 1
                        st_index = status_lista.index(demanda_atual["status"]) if demanda_atual["status"] in status_lista else 0

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
                                excluir = st.form_submit_button("🗑️ Excluir Demanda", type="secondary")
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

                            if excluir:
                                if excluir_demanda(demanda_id):
                                    st.warning(f"⚠️ Demanda #{demanda_id} excluída!")
                                    st.rerun()
                                else:
                                    st.error("❌ Erro ao excluir demanda")

                            if cancelar:
                                st.rerun()
            else:
                st.info("Não existem demandas para editar ainda.")

        with tab3:
            st.subheader("📊 Estatísticas Avançadas")
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

        with tab4:
            st.subheader("⚙️ Configurações do Sistema")
            cfg = get_db_config()
            st.code(
                "Host: {h}\nDatabase: {d}\nUser: {u}\nPort: {p}\nSSL: {s}".format(
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

            st.subheader("📈 Informações do Sistema")
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT
                                COUNT(*) as total_demandas,
                                MIN(data_criacao) as primeira_demanda,
                                MAX(data_criacao) as ultima_demanda
                            FROM demandas
                        """)
                        info = cur.fetchone()

                if info:
                    st.metric("Total de Demandas no Banco", info[0])
                    st.caption(f"Primeira demanda: {info[1].strftime('%d/%m/%Y') if info[1] else 'N/A'}")
                    st.caption(f"Última demanda: {info[2].strftime('%d/%m/%Y %H:%M') if info[2] else 'N/A'}")
            except Exception:
                st.info("Não foi possível carregar informações do sistema")

# ============================================
# RODAPÉ
# ============================================

st.sidebar.markdown("---")
st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas v1.0")
st.sidebar.caption("Conectado ao Railway PostgreSQL")

if DATABASE_URL and st.sidebar.checkbox("Mostrar informações de debug"):
    cfg = get_db_config()
    st.sidebar.text(f"Host: {cfg.get('host')}")
    st.sidebar.text(f"Database: {cfg.get('database')}")
    st.sidebar.text(f"User: {cfg.get('user')}")
    st.sidebar.text(f"Port: {cfg.get('port')}")
