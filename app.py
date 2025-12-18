# app.py

import streamlit as st
import pandas as pd
import time
from datetime import datetime, date, timedelta

# Importações dos módulos refatorados
from sistema_demandas.config import TEMA_CORES

# =============================
# CSS Customizado (Tema Cogerh/Água)
# =============================
CSS_CUSTOM = f"""
<style>
    /* Main Streamlit Theme Overrides */
    .st-emotion-cache-1cypcdb {{ /* Main sidebar */
        background-color: {TEMA_CORES['secondary']}; /* Light blue background */
    }}
    .st-emotion-cache-1dp5vir {{ /* Main content area */
        padding-top: 2rem;
    }}
    /* Primary Color for Buttons/Links */
    .st-emotion-cache-1jmvea6 {{ /* Primary button background */
        background-color: {TEMA_CORES['primary']};
        border-color: {TEMA_CORES['primary']};
    }}
    .st-emotion-cache-1jmvea6:hover {{
        background-color: #005A8C;
        border-color: #005A8C;
    }}
    /* Custom Card Style for Metrics */
    [data-testid="stMetric"] {{
        background-color: {TEMA_CORES['background']};
        border-left: 5px solid {TEMA_CORES['info']}; /* Ciano Água line */
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: all 0.3s ease-in-out;
    }}
    [data-testid="stMetric"] > div:first-child {{
        font-size: 1.5rem;
        font-weight: bold;
        color: {TEMA_CORES['primary']};
    }}
    [data-testid="stMetric"] > div:nth-child(2) {{
        font-size: 0.9rem;
        color: {TEMA_CORES['text']};
    }}
    /* Titles */
    h1, h2, h3 {{
        color: {TEMA_CORES['primary']};
    }}
</style>
"""

# Importações dos módulos refatorados
from sistema_demandas.config import CORES_STATUS, CORES_PRIORIDADE, get_db_config, DATABASE_URL
from sistema_demandas.timezone_utils import agora_fortaleza, _to_tz_aware_start, _to_tz_aware_end_exclusive
from sistema_demandas.db_connector import test_db_connection
from sistema_demandas.migrations import init_database
from sistema_demandas.data_access import (
    autenticar_usuario, criar_usuario, listar_usuarios, atualizar_usuario, desativar_usuario,
    carregar_demandas, obter_estatisticas, atualizar_demanda, excluir_demanda, adicionar_demanda,
    carregar_historico_demanda
)

# =============================
# Configuração da página
st.markdown(CSS_CUSTOM, unsafe_allow_html=True)
# =============================
st.set_page_config(
    page_title="Sistema de Demandas - GRBANABUIU",
    page_icon="🖥️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# =============================
# UI helper
# =============================
def formatar_brl(valor) -> str:
    """Formata um valor numérico para o padrão monetário brasileiro (R$)."""
    try:
        v = float(valor)
    except Exception:
        return "R$ 0,00"
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def dataframe_to_csv_br(df: pd.DataFrame) -> bytes:
    """Converte um DataFrame para CSV com separador ; e decimal , no padrão brasileiro."""
    return df.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig")


def render_comprovante_demanda(d: dict, mostrar_campos_admin: bool = False):
    """Renderiza o comprovante visual de uma demanda."""
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
    """Renderiza a lista de demandas com a opção de expandir para ver detalhes."""
    st.subheader(titulo)

    if not demandas:
        st.info("📭 Nenhuma demanda encontrada.")
        return

    total_itens = sum(d.get("quantidade", 0) for d in demandas)
    total_urgentes = sum(1 for d in demandas if d.get("urgencia"))

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total de Demandas", len(demandas))
    with col2:
        st.metric("Total de Itens", total_itens)
    with col3:
        st.metric("Demandas Urgentes", total_urgentes)

    df = pd.DataFrame(demandas)
    df_display = df[[
        "codigo", "solicitante", "departamento", "item", "quantidade", "prioridade", "status", "data_criacao_formatada"
    ]].rename(columns={
        "codigo": "Código",
        "solicitante": "Solicitante",
        "departamento": "Departamento",
        "item": "Item",
        "quantidade": "Qtd",
        "prioridade": "Prioridade",
        "status": "Status",
        "data_criacao_formatada": "Data Criação"
    })

    st.dataframe(df_display, hide_index=True, use_container_width=True)

    for i, d in enumerate(demandas):
        with st.expander(f"📋 Detalhes da Demanda {d.get('codigo', 'SEM-COD')} - {d['solicitante']}", expanded=False):
            render_comprovante_demanda(d, mostrar_campos_admin=mostrar_campos_admin)


def render_relatorio_mensal_automatico():
    """Renderiza a página de relatório mensal automático."""
    st.header("📅 Relatório Mensal Automático")
    st.caption("Filtro aplicado: Mês atual")

    hoje = agora_fortaleza().date()
    primeiro_dia_mes = hoje.replace(day=1)
    primeiro_dia_proximo_mes = (primeiro_dia_mes + timedelta(days=32)).replace(day=1)

    data_inicio = _to_tz_aware_start(primeiro_dia_mes)
    data_fim = _to_tz_aware_end_exclusive(primeiro_dia_proximo_mes - timedelta(days=1)).replace(day=1)

    filtros = {
        "data_inicio": data_inicio,
        "data_fim": data_fim
    }

    demandas = carregar_demandas(filtros)
    est = obter_estatisticas(filtros)

    if not demandas:
        st.info("📭 Nenhuma demanda registrada neste mês.")
        return

    st.subheader(f"Resumo de {primeiro_dia_mes.strftime('%B/%Y').capitalize()}")

    totais = est.get("totais", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Demandas", totais.get("total", 0))
    col2.metric("Demandas Concluídas", totais.get("concluidas", 0))
    col3.metric("Total de Itens", totais.get("total_itens", 0))
    col4.metric("Total de Valores", formatar_brl(totais.get("total_valor", 0) or 0))

    st.markdown("---")

    if est.get("por_departamento"):
        st.subheader("🏢 Demandas por Departamento")
        df_depto = pd.DataFrame(list(est["por_departamento"].items()), columns=["Departamento", "Quantidade"])
        df_depto = df_depto.sort_values("Quantidade", ascending=False)

        col_g, col_t = st.columns([2, 1])
        with col_g:
            st.bar_chart(df_depto.set_index("Departamento")["Quantidade"], use_container_width=True)
        with col_t:
            st.dataframe(df_depto, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.subheader("📋 Detalhes das Demandas")
    render_resultados_com_detalhes(demandas, "Demandas do Mês", mostrar_campos_admin=True)

    st.markdown("---")
    st.download_button(
        label="📥 Baixar Relatório (CSV)",
        data=dataframe_to_csv_br(pd.DataFrame(demandas)),
        file_name=f"relatorio_demandas_{primeiro_dia_mes.strftime('%Y%m')}.csv",
        mime="text/csv",
        use_container_width=True
    )


def pagina_gerenciar_usuarios():
    """Renderiza a página de gerenciamento de usuários."""
    st.header("👥 Gerenciar Usuários")
    st.caption("Criação, edição e desativação de usuários.")

    usuarios = listar_usuarios()
    df_usuarios = pd.DataFrame(usuarios)

    if not df_usuarios.empty:
        st.dataframe(df_usuarios, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.subheader("➕ Criar Novo Usuário")
    with st.form("form_criar_usuario", clear_on_submit=True):
        col1, col2 = st.columns(2)
        nome = col1.text_input("Nome Completo*")
        email = col2.text_input("Email*")
        username = col1.text_input("Username*")
        senha = col2.text_input("Senha*", type="password")
        departamento = col1.text_input("Departamento")
        nivel_acesso = col2.selectbox("Nível de Acesso", ["usuario", "supervisor", "administrador"])
        is_admin = st.checkbox("É Administrador?", value=(nivel_acesso == "administrador"))

        submitted = st.form_submit_button("✅ Criar Usuário", type="primary")

        if submitted:
            if nome and email and username and senha:
                dados = {
                    "nome": nome,
                    "email": email,
                    "username": username,
                    "senha": senha,
                    "departamento": departamento,
                    "nivel_acesso": nivel_acesso,
                    "is_admin": is_admin
                }
                ok, msg = criar_usuario(dados)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
            else:
                st.error("Preencha todos os campos obrigatórios.")

    st.markdown("---")
    st.subheader("✏️ Editar/Desativar Usuário")
    if not df_usuarios.empty:
        opcoes = [f"{u['id']} - {u['nome']} ({u['username']})" for u in usuarios]
        escolha = st.selectbox("Selecione o usuário para editar", opcoes, index=None)

        if escolha:
            user_id = int(escolha.split(" - ")[0])
            usuario_selecionado = next(u for u in usuarios if u["id"] == user_id)

            with st.form(f"form_editar_usuario_{user_id}"):
                col_e1, col_e2 = st.columns(2)
                nome_e = col_e1.text_input("Nome Completo", value=usuario_selecionado["nome"])
                email_e = col_e2.text_input("Email", value=usuario_selecionado["email"])
                username_e = col_e1.text_input("Username", value=usuario_selecionado["username"], disabled=True)
                senha_e = col_e2.text_input("Nova Senha (deixe em branco para manter)", type="password")
                departamento_e = col_e1.text_input("Departamento", value=usuario_selecionado["departamento"])
                nivel_acesso_e = col_e2.selectbox("Nível de Acesso", ["usuario", "supervisor", "administrador"], index=["usuario", "supervisor", "administrador"].index(usuario_selecionado["nivel_acesso"]))
                is_admin_e = st.checkbox("É Administrador?", value=usuario_selecionado["is_admin"])
                ativo_e = st.checkbox("Usuário Ativo", value=usuario_selecionado["ativo"])

                col_b1, col_b2 = st.columns(2)
                salvar_e = col_b1.form_submit_button("💾 Salvar Alterações", type="primary")
                desativar_e = col_b2.form_submit_button("❌ Desativar Usuário")

                if salvar_e:
                    dados_e = {
                        "nome": nome_e,
                        "email": email_e,
                        "departamento": departamento_e,
                        "nivel_acesso": nivel_acesso_e,
                        "is_admin": is_admin_e,
                        "ativo": ativo_e,
                    }
                    if senha_e:
                        dados_e["senha"] = senha_e

                    ok, msg = atualizar_usuario(user_id, dados_e)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

                if desativar_e:
                    ok, msg = desativar_usuario(user_id)
                    if ok:
                        st.warning(msg)
                        st.rerun()
                    else:
                        st.error(msg)


def pagina_inicial():
    """Renderiza a página inicial (menu de navegação)."""
    st.title("Sistema de Demandas - GRBANABUIU")
    st.markdown("---")

    st.markdown("""
    <div style="
        background: linear-gradient(135deg, #f0f9ff 0%, #cbebff 100%);
        padding: 25px;
        border-radius: 12px;
        margin-bottom: 25px;
        border: 1px solid #a8dadc;
    ">
        <h3 style="margin: 0; color: #1d3557;">Bem-vindo(a) ao Sistema de Demandas</h3>
        <p style="margin: 10px 0 0 0; color: #457b9d;">
            Utilize os botões abaixo para navegar entre as principais funções do sistema.
        </p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("📝 Nova Solicitação", use_container_width=True, type="primary"):
            st.session_state.pagina_atual = "solicitacao"
            st.rerun()

    with col2:
        if st.button("🔎 Consultar Demandas", use_container_width=True):
            st.session_state.pagina_atual = "solicitacao"
            st.rerun()

    with col3:
        if st.button("🔧 Área Administrativa", use_container_width=True):
            st.session_state.pagina_atual = "login_admin"
            st.rerun()

    st.markdown("---")
    st.subheader("Status do Sistema")

    if "init_complete" in st.session_state and st.session_state.init_complete:
        st.success("✅ Sistema pronto para uso. Banco de dados inicializado.")
    elif "demo_mode" in st.session_state and st.session_state.demo_mode:
        st.warning("⚠️ Modo Demonstração. Conexão com o banco de dados falhou.")
    else:
        st.info("Aguardando inicialização do banco de dados...")


def pagina_solicitacao():
    """Renderiza a página de nova solicitação e consulta pública."""
    st.title("📝 Solicitação de Demandas")
    st.markdown("---")

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
            st.info(f"📧 Notificação enviada. {st.session_state.ultima_demanda_email_msg or ''}".strip())
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
                    "🏢 Setor*",
                    ["Administrativo", "Açudes", "EB", "Gestão", "Operação", "Outro"],
                    index=None,
                    placeholder="Escolha um setor",
                    help="Selecione o setor solicitante"
                )
                local = st.selectbox(
                    "📍 Local*",
                    ["Banabuiú", "Capitão Mor", "Cipoada", "Fogareiro", "Gerência", "Outro", "Patu", "Pirabibu", "Poço do Barro", "Quixeramobim", "São Jose I", "São Jose II", "Serafim Dias", "Trapiá II", "Umari", "Vieirão"],
                    index=None,
                    placeholder="Escolha um local",
                    help="Selecione o local solicitante"
                )
                categoria = st.selectbox(
                    "📂 Categoria*",
                    ["Alimentos", "Água potável", "Combustível", "Equipamentos", "Ferramentas", "Lubrificantes", "Materiais", "Outro"],
                    index=None,
                    placeholder="Escolha uma categoria",
                    help="Selecione a categoria solicitante"
                )

            with col2:
                item = st.text_input("📝 Descrição da Demanda*", placeholder="Descreva a solicitação")
                quantidade = st.number_input("🔢 Quantidade*", min_value=1, value=1, step=1)
                unidade = st.selectbox(
                    "📏 Unidade*",
                    ["Kg", "Litros", "Garrafão", "Galão", "Unid.", "Metros", "m²", "m³", "Outro"],
                    index=None,
                    placeholder="Escolha a unidade",
                    help="Selecione a unidade"
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
                    # Simplificando a validação de "Selecione" para verificar se o valor é None (já que index=None)
                    if departamento is None or local is None or unidade is None or categoria is None:
                        st.error("⚠️ Selecione um valor válido para todos os campos obrigatórios.")
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
    """Renderiza a página de login administrativo."""
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
            username = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            login_button = st.form_submit_button("Entrar", type="primary", use_container_width=True)

            if login_button:
                usuario = autenticar_usuario(username, senha)
                if usuario:
                    st.session_state.usuario_logado = usuario
                    st.session_state.pagina_atual = "admin"
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos.")

    if st.button("← Voltar ao Início", use_container_width=True):
        st.session_state.pagina_atual = "inicio"
        st.rerun()


def pagina_admin():
    """Renderiza o dashboard administrativo com tema Cogerh."""
    usuario = st.session_state.usuario_logado
    usuario_nome = usuario.get("nome", "Admin")
    usuario_nivel = usuario.get("nivel_acesso", "usuario")
    usuario_admin = usuario.get("is_admin", False)

    st.sidebar.markdown(f"""
    <div style="
        padding: 15px;
        border-radius: 8px;
        background-color: {TEMA_CORES['primary']};
        color: white;
        margin-bottom: 15px;
    ">
        <h4 style="margin: 0;">Olá, {usuario_nome}!</h4>
        <p style="margin: 0; font-size: 0.85rem; opacity: 0.9;">Nível: {usuario_nivel.capitalize()}</p>
    </div>
    """, unsafe_allow_html=True)

    if st.sidebar.button("🚪 Sair do Sistema", type="secondary", use_container_width=True):
        st.session_state.usuario_logado = False
        st.session_state.pagina_atual = "inicio"
        st.rerun()

    menu_opcoes = ["📋 Dashboard", "🔎 Consultar Demandas", "✏️ Editar Demanda", "📅 Relatório Mensal", "📊 Estatísticas"]
    if usuario_admin:
        menu_opcoes.append("👥 Gerenciar Usuários")
        menu_opcoes.append("⚙️ Configurações")

    st.sidebar.markdown("---")
    menu_sel = st.sidebar.radio("Menu Administrativo", menu_opcoes, index=0)

    # Filtros globais para o dashboard
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filtros de Pesquisa")

    with st.sidebar.expander("Filtros de Data", expanded=False):
        data_inicio = st.date_input("Data Início", value=agora_fortaleza().date() - timedelta(days=30))
        data_fim = st.date_input("Data Fim", value=agora_fortaleza().date())

    with st.sidebar.expander("Filtros de Status", expanded=False):
        status_filtros = st.multiselect("Status", list(CORES_STATUS.keys()), default=list(CORES_STATUS.keys()))

    with st.sidebar.expander("Filtros de Prioridade", expanded=False):
        prioridade_filtros = st.multiselect("Prioridade", list(CORES_PRIORIDADE.keys()), default=list(CORES_PRIORIDADE.keys()))

    filtros = {
        "data_inicio": _to_tz_aware_start(data_inicio),
        "data_fim": _to_tz_aware_end_exclusive(data_fim),
        "status": status_filtros,
        "prioridade": prioridade_filtros
    }

    if menu_sel == "📋 Dashboard":
        st.header("📋 Dashboard de Demandas")
        st.caption(f"Período: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}")

        est = obter_estatisticas(filtros)
        if not est:
            st.info("📭 Sem dados para o período/filtros selecionados.")
            return

        totais = est.get("totais", {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Demandas", totais.get("total", 0))
        col2.metric("Pendentes", totais.get("pendentes", 0), delta_color="inverse")
        col3.metric("Em Andamento", totais.get("em_andamento", 0))
        col4.metric("Concluídas", totais.get("concluidas", 0), delta_color="normal")

        st.markdown("---")

        if est.get("por_status"):
            st.subheader("📈 Distribuição por Status")
            df_status = pd.DataFrame(list(est["por_status"].items()), columns=["Status", "Quantidade"])
            st.bar_chart(df_status.set_index("Status")["Quantidade"], use_container_width=True)

        st.markdown("---")
        st.subheader("🚨 Demandas Urgentes e de Alta Prioridade")
        filtros_urgentes = filtros.copy()
        filtros_urgentes["prioridade"] = ["Urgente", "Alta"]
        demandas_urgentes = carregar_demandas(filtros_urgentes)
        render_resultados_com_detalhes(demandas_urgentes, "Demandas Urgentes/Alta", mostrar_campos_admin=True)

    elif menu_sel == "🔎 Consultar Demandas":
        st.header("🔎 Consultar Demandas (Admin)")
        st.caption("Filtros aplicados na barra lateral.")
        demandas = carregar_demandas(filtros)
        render_resultados_com_detalhes(demandas, "Demandas Encontradas", mostrar_campos_admin=True)

        if demandas:
            st.download_button(
                label="📥 Baixar Dados (CSV)",
                data=dataframe_to_csv_br(pd.DataFrame(demandas)),
                file_name="demandas_filtradas.csv",
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
                c2.text_input("Departamento", value=demanda.get("departamento", ""), disabled=False)
                c3.text_input("Local", value=demanda.get("local", ""), disabled=False)
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
            # Recarrega a demanda para mostrar o estado atualizado
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
        """.strip(), language="bash")

        if st.button("🔄 Testar Conexão com Banco de Dados", use_container_width=True):
            with st.spinner("Testando conexão..."):
                ok, msg = test_db_connection()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

        st.markdown("---")
        st.subheader("📧 Configuração de email (variáveis)")
        st.caption("Preferência: Brevo API. SMTP fica como fallback se quiser.")

        ecfg = get_email_config()
        bcfg = get_brevo_config()

        st.code(f"""
MAIL_ON_NEW_DEMANDA: {ecfg.get("enabled_new")}
MAIL_SUBJECT_PREFIX: {ecfg.get("subject_prefix")}

SMTP_HOST: {ecfg.get("host")}
SMTP_PORT: {ecfg.get("port")}
SMTP_USER: {ecfg.get("user")}
SMTP_STARTTLS: {ecfg.get("starttls")}
MAIL_FROM: {ecfg.get("from")}
MAIL_TO: {", ".join(ecfg.get("to", []))}
MAIL_CC: {", ".join(ecfg.get("cc", []))}
MAIL_BCC: {", ".join(ecfg.get("bcc", []))}
MAIL_SEND_TIMEOUT: {ecfg.get("timeout")}

BREVO_API_KEY: {"CONFIGURADA" if bool(bcfg.get("api_key")) else "NAO"}
BREVO_SENDER: {bcfg.get("sender_email")}
BREVO_SENDER_NAME: {bcfg.get("sender_name")}
BREVO_TO: {", ".join(bcfg.get("to", []))}
BREVO_TIMEOUT: {bcfg.get("timeout")}
        """.strip(), language="bash")

        st.markdown("---")
        st.subheader("📊 Informações do Sistema")

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Versão do Sistema", "3.4")
            st.metric("Fuso Horário", "America/Fortaleza")
        with col2:
            st.metric("Design", "Comprovante Digital + Relatórios + Email API")
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
if st.session_state.usuario_logado:
    pagina_admin()
elif st.session_state.pagina_atual == "inicio":
    pagina_inicial()
elif st.session_state.pagina_atual == "solicitacao":
    pagina_solicitacao()
elif st.session_state.pagina_atual == "login_admin":
    pagina_login_admin()
elif st.session_state.pagina_atual == "admin":
    # Redireciona para login se tentar acessar admin sem estar logado
    st.session_state.pagina_atual = "login_admin"
    st.rerun()
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
    st.sidebar.caption(f"© {datetime.now().year} - Sistema de Demandas - GRBANABUIU v3.4")
