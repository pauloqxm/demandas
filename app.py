import streamlit as st
import pandas as pd
import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import os
from urllib.parse import urlparse

# Configuração da página
st.set_page_config(
    page_title="Sistema de Demandas - Railway",
    page_icon="🚂",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================
# CONFIGURAÇÃO DA CONEXÃO COM RAILWAY POSTGRES
# ============================================

# Obter DATABASE_URL do Railway (definida automaticamente no ambiente Railway)
DATABASE_URL = os.environ.get('DATABASE_URL')

# Função para parsear a DATABASE_URL do Railway
def get_db_config():
    if DATABASE_URL:
        # Parse da URL de conexão do Railway
        url = urlparse(DATABASE_URL)
        
        return {
            'host': url.hostname,
            'database': url.path[1:],  # Remove a barra inicial
            'user': url.username,
            'password': url.password,
            'port': url.port or 5432,
            'sslmode': 'require'  # Railway geralmente requer SSL
        }
    else:
        # Para desenvolvimento local (usar secrets.toml ou variáveis locais)
        return {
            'host': st.secrets.get("DB_HOST", "localhost"),
            'database': st.secrets.get("DB_NAME", "railway"),
            'user': st.secrets.get("DB_USER", "postgres"),
            'password': st.secrets.get("DB_PASSWORD", ""),
            'port': st.secrets.get("DB_PORT", 5432),
            'sslmode': 'prefer'
        }

# Senha de administrador (usar variável de ambiente no Railway)
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', st.secrets.get("ADMIN_PASSWORD", "admin123"))

@contextmanager
def get_db_connection():
    """Context manager para conexões com o banco de dados no Railway"""
    config = get_db_config()
    conn = None
    
    try:
        # Conexão específica para Railway (com SSL se necessário)
        if config.get('sslmode') == 'require':
            conn = psycopg2.connect(
                host=config['host'],
                database=config['database'],
                user=config['user'],
                password=config['password'],
                port=config['port'],
                sslmode='require'
            )
        else:
            conn = psycopg2.connect(**{k: v for k, v in config.items() if k != 'sslmode'})
        
        conn.autocommit = False
        yield conn
        
    except psycopg2.OperationalError as e:
        st.error(f"❌ **Erro de conexão com o Railway Postgres:**")
        st.error(f"Verifique se a DATABASE_URL está configurada corretamente")
        st.error(f"Detalhes: {str(e)}")
        
        # Modo de demonstração (fallback)
        st.warning("🔶 Executando em modo de demonstração (dados voláteis)")
        raise
        
    except Exception as e:
        st.error(f"Erro inesperado: {str(e)}")
        raise
        
    finally:
        if conn:
            conn.close()

def test_db_connection():
    """Testa a conexão com o banco de dados"""
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
                
                # Tabela de histórico (logs)
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
                
                # Tabela de usuários (simplificada)
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
                
                # Índices para performance
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
    """Carrega demandas com filtros opcionais"""
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
                    if filtros.get('status'):
                        query += " AND status = ANY(%s)"
                        params.append(filtros['status'])
                    
                    if filtros.get('departamento'):
                        query += " AND departamento = ANY(%s)"
                        params.append(filtros['departamento'])
                    
                    if filtros.get('prioridade'):
                        query += " AND prioridade = ANY(%s)"
                        params.append(filtros['prioridade'])
                    
                    if filtros.get('search'):
                        query += " AND (item ILIKE %s OR solicitante ILIKE %s)"
                        params.append(f"%{filtros['search']}%")
                        params.append(f"%{filtros['search']}%")
                
                query += " ORDER BY "
                
                if filtros and filtros.get('sort_by') == 'prioridade':
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
    """Adiciona uma nova demanda"""
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
                
                # Registrar no histórico
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
    """Atualiza uma demanda existente"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Obter dados antigos para histórico
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados_antigos = cur.fetchone()
                
                # Atualizar demanda
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
                
                # Registrar no histórico
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (demanda_id, "Administrador", "ATUALIZAÇÃO", json.dumps({
                    "antigo": dict(dados_antigos) if dados_antigos else {},
                    "novo": dados
                })))
                
                conn.commit()
                return True
                
    except Exception as e:
        st.error(f"Erro ao atualizar demanda: {str(e)}")
        return False

def excluir_demanda(demanda_id):
    """Exclui uma demanda"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Obter dados para histórico antes de excluir
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                dados = cur.fetchone()
                
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (demanda_id, "Administrador", "EXCLUSÃO", 
                      json.dumps(dict(dados) if dados else {})))
                
                cur.execute("DELETE FROM demandas WHERE id = %s", (demanda_id,))
                conn.commit()
                return True
                
    except Exception as e:
        st.error(f"Erro ao excluir demanda: {str(e)}")
        return False

def obter_estatisticas():
    """Obtém estatísticas das demandas"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                estatisticas = {}
                
                # Totais
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
                estatisticas['totais'] = dict(totais) if totais else {}
                
                # Por departamento
                cur.execute("""
                    SELECT departamento, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY departamento
                    ORDER BY quantidade DESC
                """)
                estatisticas['por_departamento'] = dict(cur.fetchall())
                
                # Por prioridade
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
                estatisticas['por_prioridade'] = dict(cur.fetchall())
                
                # Por status
                cur.execute("""
                    SELECT status, COUNT(*) as quantidade
                    FROM demandas
                    GROUP BY status
                """)
                estatisticas['por_status'] = dict(cur.fetchall())
                
                return estatisticas
                
    except Exception as e:
        st.error(f"Erro ao obter estatísticas: {str(e)}")
        return {}

# ============================================
# INTERFACE STREAMLIT
# ============================================

# Inicialização
if 'init_complete' not in st.session_state:
    # Testar conexão
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
        st.session_state.demo_mode = True  # Modo demonstração
    st.session_state.filtros = {}

# Título principal
st.title("🚂 Sistema de Demandas - Railway")
st.caption("Gerenciamento centralizado de solicitações da equipe")

# Sidebar - Status do sistema
with st.sidebar:
    st.header("🔧 Configuração")
    
    if DATABASE_URL:
        st.success("✅ Conectado ao Railway Postgres")
        # Mostrar informações da conexão (sem senha)
        config = get_db_config()
        st.info(f"**Host:** {config.get('host', 'N/A')}\n"
                f"**Banco:** {config.get('database', 'N/A')}\n"
                f"**Usuário:** {config.get('user', 'N/A')}")
    else:
        st.warning("⚠️ DATABASE_URL não encontrada")
        st.info("Configure a variável DATABASE_URL no Railway")
    
    # Menu principal
    st.header("📋 Navegação")
    
    menu_opcoes = ["🏠 Dashboard", "📝 Nova Demanda", "🔍 Buscar Demandas", "⚙️ Administração"]
    menu_selecionado = st.radio("Selecione uma opção:", menu_opcoes)
    
    # Filtros rápidos (se aplicável)
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
                'status': status_filtro,
                'prioridade': prioridade_filtro
            }
            st.rerun()
        
        if st.button("Limpar Filtros"):
            st.session_state.filtros = {}
            st.rerun()

# ============================================
# PÁGINAS DO SISTEMA
# ============================================

# Página: Dashboard
if menu_selecionado == "🏠 Dashboard":
    st.header("📊 Dashboard de Demandas")
    
    # Estatísticas
    estatisticas = obter_estatisticas()
    
    if estatisticas:
        totais = estatisticas.get('totais', {})
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Demandas", totais.get('total', 0))
        with col2:
            st.metric("Pendentes", totais.get('pendentes', 0), 
                     delta=-totais.get('concluidas', 0) if totais.get('concluidas', 0) > 0 else None)
        with col3:
            st.metric("Urgentes", totais.get('urgentes', 0), 
                     delta_color="inverse")
        with col4:
            st.metric("Total Itens", totais.get('total_itens', 0))
        
        # Gráficos
        col1, col2 = st.columns(2)
        with col1:
            if estatisticas.get('por_departamento'):
                st.subheader("Por Departamento")
                df_dept = pd.DataFrame(list(estatisticas['por_departamento'].items()),
                                     columns=['Departamento', 'Quantidade'])
                st.bar_chart(df_dept.set_index('Departamento'))
        
        with col2:
            if estatisticas.get('por_prioridade'):
                st.subheader("Por Prioridade")
                df_pri = pd.DataFrame(list(estatisticas['por_prioridade'].items()),
                                    columns=['Prioridade', 'Quantidade'])
                st.bar_chart(df_pri.set_index('Prioridade'))
        
        # Últimas demandas
        st.subheader("📋 Últimas Solicitações")
        demandas_recentes = carregar_demandas(st.session_state.filtros)[:10]
        
        if demandas_recentes:
            df_recentes = pd.DataFrame(demandas_recentes)
            # Renomear colunas para exibição
            df_display = df_recentes.rename(columns={
                'id': 'ID',
                'item': 'Item',
                'quantidade': 'Qtd',
                'solicitante': 'Solicitante',
                'departamento': 'Depto',
                'prioridade': 'Prioridade',
                'status': 'Status',
                'data_criacao_formatada': 'Data'
            })
            
            st.dataframe(
                df_display[['ID', 'Item', 'Qtd', 'Solicitante', 'Depto', 'Prioridade', 'Status', 'Data']],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhuma demanda encontrada com os filtros atuais.")

# Página: Nova Demanda
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
            prioridade = st.selectbox(
                "🚨 Prioridade",
                ["Baixa", "Média", "Alta", "Urgente"],
                index=1
            )
            urgencia = st.checkbox("🚨 É urgente?")
        
        with col4:
            observacoes = st.text_area("💬 Observações Adicionais", height=100)
        
        submitted = st.form_submit_button("✅ Enviar Solicitação", type="primary")
        
        if submitted:
            if solicitante and item and departamento:
                nova_demanda = {
                    "item": item,
                    "quantidade": quantidade,
                    "solicitante": solicitante,
                    "departamento": departamento,
                    "prioridade": prioridade,
                    "observacoes": observacoes,
                    "categoria": categoria,
                    "urgencia": urgencia,
                    "estimativa_horas": estimativa_horas if estimativa_horas > 0 else None
                }
                
                demanda_id = adicionar_demanda(nova_demanda)
                
                if demanda_id:
                    st.success(f"✅ Solicitação **#{demanda_id}** enviada com sucesso!")
                    st.balloons()
                    
                    # Mostrar resumo
                    with st.expander("📋 Ver Resumo da Solicitação"):
                        st.json(nova_demanda)
                else:
                    st.error("❌ Erro ao salvar a solicitação.")
            else:
                st.error("⚠️ Por favor, preencha todos os campos obrigatórios (*)")

# Página: Buscar Demandas
elif menu_selecionado == "🔍 Buscar Demandas":
    st.header("🔍 Buscar e Gerenciar Demandas")
    
    # Barra de busca
    col1, col2 = st.columns([3, 1])
    with col1:
        busca = st.text_input("🔎 Buscar por texto (item ou solicitante):")
    with col2:
        if busca:
            st.session_state.filtros['search'] = busca
        else:
            st.session_state.filtros.pop('search', None)
    
    # Carregar demandas com filtros
    demandas = carregar_demandas(st.session_state.filtros)
    
    if demandas:
        # Estatísticas da busca
        st.info(f"📊 Encontradas **{len(demandas)}** demandas")
        
        # Tabela interativa
        df = pd.DataFrame(demandas)
        
        # Seleção de colunas para exibir
        colunas_disponiveis = [
            'id', 'item', 'quantidade', 'solicitante', 'departamento',
            'prioridade', 'status', 'data_criacao_formatada', 'categoria'
        ]
        
        colunas_selecionadas = st.multiselect(
            "👁️ Colunas para exibir:",
            colunas_disponiveis,
            default=['id', 'item', 'quantidade', 'solicitante', 'departamento', 'prioridade', 'status']
        )
        
        if colunas_selecionadas:
            # Exibir tabela
            st.dataframe(
                df[colunas_selecionadas],
                use_container_width=True,
                hide_index=True
            )
            
            # Opção de exportação
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

# Página: Administração
elif menu_selecionado == "⚙️ Administração":
    st.header("⚙️ Área de Administração")
    
    # Verificação de senha
    if 'admin_autenticado' not in st.session_state:
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
        # Logout button
        if st.sidebar.button("🚪 Logout"):
            st.session_state.admin_autenticado = False
            st.rerun()
        
        st.success("✅ Autenticado como administrador")
        
        # Abas da administração
        tab1, tab2, tab3, tab4 = st.tabs(["📋 Todas as Demandas", "✏️ Editar Demanda", "📊 Estatísticas Avançadas", "⚙️ Configurações"])
        
        with tab1:
            st.subheader("📋 Todas as Demandas")
            
            todas_demandas = carregar_demandas()
            
            if todas_demandas:
                df_admin = pd.DataFrame(todas_demandas)
                
                # Adicionar seleção para ação em massa
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
                
                # Tabela administrativa
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
                        "data_atualizacao_formatada": "Última Atualização"
                    }
                )
        
        with tab2:
            st.subheader("✏️ Editar Demanda")
            
            todas_demandas = carregar_demandas()
            
            if todas_demandas:
                # Selecionar demanda para editar
                opcoes_demanda = [f"#{d['id']} - {d['item'][:50]}..." for d in todas_demandas]
                selecao = st.selectbox("Selecione uma demanda:", opcoes_demanda)
                
                if selecao:
                    demanda_id = int(selecao.split('#')[1].split(' - ')[0])
                    demanda_atual = next((d for d in todas_demandas if d['id'] == demanda_id), None)
                    
                    if demanda_atual:
                        with st.form(f"form_editar_{demanda_id}"):
                            col_e1, col_e2 = st.columns(2)
                            
                            with col_e1:
                                item_edit = st.text_area("Descrição", value=demanda_atual['item'], height=100)
                                quantidade_edit = st.number_input("Quantidade", 
                                                                  min_value=1, 
                                                                  value=demanda_atual['quantidade'])
                                solicitante_edit = st.text_input("Solicitante", 
                                                                 value=demanda_atual['solicitante'])
                                departamento_edit = st.selectbox(
                                    "Departamento",
                                    ["TI", "RH", "Financeiro", "Comercial", "Operações", 
                                     "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"],
                                    index=["TI", "RH", "Financeiro", "Comercial", "Operações", 
                                           "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"]
                                          .index(demanda_atual['departamento']) 
                                          if demanda_atual['departamento'] in 
                                          ["TI", "RH", "Financeiro", "Comercial", "Operações", 
                                           "Marketing", "Suporte", "Vendas", "Desenvolvimento", "Outro"]
                                          else 9
                                )
                            
                            with col_e2:
                                prioridade_edit = st.selectbox(
                                    "Prioridade",
                                    ["Baixa", "Média", "Alta", "Urgente"],
                                    index=["Baixa", "Média", "Alta", "Urgente"]
                                          .index(demanda_atual['prioridade'])
                                )
                                status_edit = st.selectbox(
                                    "Status",
                                    ["Pendente", "Em andamento", "Concluída", "Cancelada"],
                                    index=["Pendente", "Em andamento", "Concluída", "Cancelada"]
                                          .index(demanda_atual['status'])
                                )
                                categoria_edit = st.text_input("Categoria", 
                                                              value=demanda_atual.get('categoria', 'Geral'))
                                urgencia_edit = st.checkbox("Urgente", 
                                                           value=demanda_atual.get('urgencia', False))
                                observacoes_edit = st.text_area("Observações", 
                                                               value=demanda_atual.get('observacoes', ''), 
                                                               height=100)
                            
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
                                    "quantidade": quantidade_edit,
                                    "solicitante": solicitante_edit,
                                    "departamento": departamento_edit,
                                    "prioridade": prioridade_edit,
                                    "status": status_edit,
                                    "categoria": categoria_edit,
                                    "urgencia": urgencia_edit,
                                    "observacoes": observacoes_edit
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
        
        with tab3:
            st.subheader("📊 Estatísticas Avançadas")
            
            estatisticas = obter_estatisticas()
            
            if estatisticas:
                # Métricas detalhadas
                totais = estatisticas.get('totais', {})
                
                st.metric("Total de Horas Estimadas", 
                         f"{totais.get('total_horas', 0):.1f}h")
                
                # Gráficos adicionais
                col_s1, col_s2 = st.columns(2)
                
                with col_s1:
                    if estatisticas.get('por_status'):
                        st.subheader("Distribuição por Status")
                        df_status = pd.DataFrame(list(estatisticas['por_status'].items()),
                                               columns=['Status', 'Quantidade'])
                        st.bar_chart(df_status.set_index('Status'))
                
                with col_s2:
                    # Demanda por período (últimos 7 dias)
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
                                    df_periodo = pd.DataFrame(dados_periodo, columns=['Data', 'Quantidade'])
                                    st.subheader("Demandas nos últimos 7 dias")
                                    st.line_chart(df_periodo.set_index('Data'))
                    except:
                        st.info("Não foi possível carregar dados temporais")
        
        with tab4:
            st.subheader("⚙️ Configurações do Sistema")
            
            st.info("Configurações do banco de dados:")
            
            config = get_db_config()
            st.code(f"""
            Host: {config.get('host', 'N/A')}
            Database: {config.get('database', 'N/A')}
            User: {config.get('user', 'N/A')}
            Port: {config.get('port', 'N/A')}
            SSL: {config.get('sslmode', 'N/A')}
            """)
            
            # Testar conexão novamente
            if st.button("🔄 Testar Conexão com Banco"):
                conexao_ok, mensagem = test_db_connection()
                if conexao_ok:
                    st.success(mensagem)
                else:
                    st.error(mensagem)
            
            # Informações do sistema
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
            except:
                st.info("Não foi possível carregar informações do sistema")

# ============================================
# RODAPÉ
# ============================================

st.sidebar.markdown("---")
st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas v1.0")
st.sidebar.caption(f"Conectado ao Railway PostgreSQL")

# Informações de debug (apenas se DATABASE_URL existir)
if DATABASE_URL and st.sidebar.checkbox("Mostrar informações de debug"):
    st.sidebar.text(f"Host: {get_db_config().get('host')}")
    st.sidebar.text(f"Database: {get_db_config().get('database')}")
    st.sidebar.text(f"User: {get_db_config().get('user')}")
    st.sidebar.text(f"Port: {get_db_config().get('port')}")
