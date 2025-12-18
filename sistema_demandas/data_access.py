# data_access.py

import json
from datetime import datetime, date, timedelta
from decimal import Decimal
import psycopg2
from psycopg2.extras import RealDictCursor
import streamlit as st
import pandas as pd

from .db_connector import get_db_connection
from .auth import hash_password, verificar_senha
from .timezone_utils import agora_fortaleza, formatar_data_hora_fortaleza
from .email_service import enviar_email_nova_demanda

# =============================
# JSON seguro
# =============================
def json_safe(obj):
    """Converte objetos não serializáveis (Decimal, datetime) para tipos JSON-safe."""
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
    """Serializa um objeto Python para string JSON de forma segura."""
    return json.dumps(json_safe(payload), ensure_ascii=False, default=str)

# =============================
# Auth usuários (DB Access)
# =============================
def autenticar_usuario(username, senha):
    """Autentica o usuário e atualiza o último login."""
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
    """Cria um novo usuário no banco de dados."""
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
    """Lista todos os usuários com dados formatados."""
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
    """Atualiza os dados de um usuário."""
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
    """Desativa um usuário (ativo = FALSE)."""
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
    """Gera um código de demanda único no formato ddmmaa-xx."""
    prefixo = agora_fortaleza().strftime("%d%m%y")
    cur.execute("""
        SELECT COALESCE(MAX(NULLIF(SPLIT_PART(codigo, '-', 2), '')::int), 0)
        FROM demandas
        WHERE codigo LIKE %s
    """, (f"{prefixo}-%",))
    max_seq = cur.fetchone()[0] or 0
    return f"{prefixo}-{(max_seq + 1):02d}"


def normalizar_busca_codigo(texto: str) -> str:
    """Normaliza o texto de busca para o formato de código ddmmaa-xx."""
    if not texto:
        return ""
    s = str(texto).strip()
    s = s.replace("/", "").replace(" ", "").replace(".", "").replace("_", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:6]}-{s[6:]}"
    return s

# =============================
# Demandas (CRUD)
# =============================
def carregar_demandas(filtros=None):
    """Carrega demandas do banco de dados com filtros opcionais."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                query = """
                    SELECT id, codigo, item, quantidade, solicitante, departamento, local, prioridade,
                           observacoes, status, data_criacao, data_atualizacao, categoria, unidade,
                           urgencia, estimativa_horas, almoxarifado, valor
                    FROM demandas
                """
                where = "WHERE 1=1"
                params = []

                if filtros:
                    if filtros.get("solicitante"):
                        where += " AND solicitante ILIKE %s"
                        params.append(f"%{filtros['solicitante']}%")
                    if filtros.get("codigo"):
                        where += " AND codigo = %s"
                        params.append(normalizar_busca_codigo(filtros["codigo"]))
                    if filtros.get("status"):
                        where += " AND status = ANY(%s)"
                        params.append(filtros["status"])
                    if filtros.get("prioridade"):
                        where += " AND prioridade = ANY(%s)"
                        params.append(filtros["prioridade"])
                    if filtros.get("data_inicio"):
                        where += " AND data_criacao >= %s"
                        params.append(filtros["data_inicio"])
                    if filtros.get("data_fim"):
                        where += " AND data_criacao < %s"
                        params.append(filtros["data_fim"])

                query += f" {where} ORDER BY data_criacao DESC"

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
    """Carrega o histórico de ações de uma demanda."""
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
    """Adiciona uma nova demanda e registra no histórico."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                # Tenta gerar código único (até 8 vezes)
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

                        # Envio de e-mail (lógica de negócio)
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
                        # Tenta novamente com novo código
                        continue
                
                return None
    except Exception as e:
        st.error(f"Erro ao adicionar demanda: {str(e)}")
        return None


def atualizar_demanda(demanda_id: int, dados_atualizados: dict) -> bool:
    """Atualiza uma demanda e registra a alteração no histórico."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                # 1. Obter dados antigos para histórico
                cur.execute("SELECT * FROM demandas WHERE id = %s", (demanda_id,))
                demanda_antiga = cur.fetchone()

                if not demanda_antiga:
                    return False

                # 2. Construir query de atualização
                campos = []
                valores = []
                detalhes_historico = {"antigo": {}, "novo": {}}

                for campo, valor in dados_atualizados.items():
                    # Ignora campos que não mudaram
                    if campo in demanda_antiga and demanda_antiga[campo] == valor:
                        continue

                    campos.append(f"{campo} = %s")
                    valores.append(valor)

                    # Registra no histórico
                    detalhes_historico["antigo"][campo] = demanda_antiga.get(campo)
                    detalhes_historico["novo"][campo] = valor

                if not campos:
                    return True # Nada para atualizar

                campos.append("data_atualizacao = CURRENT_TIMESTAMP")

                # 3. Executar atualização
                valores.append(demanda_id)
                cur.execute(f"UPDATE demandas SET {', '.join(campos)} WHERE id = %s", valores)

                # 4. Registrar histórico
                usuario_acao = st.session_state.usuario_logado.get("username", "Sistema") if st.session_state.usuario_logado else "Sistema"
                cur.execute("""
                    INSERT INTO historico_demandas (demanda_id, usuario, acao, detalhes)
                    VALUES (%s, %s, %s, %s)
                """, (demanda_id, usuario_acao, "ATUALIZAÇÃO", dumps_safe(detalhes_historico)))

                conn.commit()
                return True
    except Exception as e:
        st.error(f"Erro ao atualizar demanda: {str(e)}")
        return False


def excluir_demanda(demanda_id: int) -> bool:
    """Exclui uma demanda e seu histórico associado."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")
                
                # A exclusão do histórico é feita via ON DELETE CASCADE na tabela demandas
                cur.execute("DELETE FROM demandas WHERE id = %s", (demanda_id,))
                conn.commit()
                return True
    except Exception as e:
        st.error(f"Erro ao excluir demanda: {str(e)}")
        return False


def obter_estatisticas(filtros=None):
    """Calcula e retorna estatísticas agregadas das demandas com filtros."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                where = "WHERE 1=1"
                params = []

                if filtros:
                    if filtros.get("solicitante"):
                        where += " AND solicitante ILIKE %s"
                        params.append(f"%{filtros['solicitante']}%")
                    if filtros.get("codigo"):
                        where += " AND codigo = %s"
                        params.append(normalizar_busca_codigo(filtros["codigo"]))
                    if filtros.get("data_inicio"):
                        where += " AND data_criacao >= %s"
                        params.append(filtros["data_inicio"])
                    if filtros.get("data_fim"):
                        where += " AND data_criacao < %s"
                        params.append(filtros["data_fim"])
                    if filtros.get("status"):
                        where += " AND status = ANY(%s)"
                        params.append(filtros["status"])
                    if filtros.get("prioridade"):
                        where += " AND prioridade = ANY(%s)"
                        params.append(filtros["prioridade"])

                estat = {}

                # Estatísticas gerais
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

                # Por departamento
                cur.execute(f"""
                    SELECT departamento, COUNT(*) as quantidade
                    FROM demandas
                    {where}
                    GROUP BY departamento
                    ORDER BY quantidade DESC
                """, params)
                estat["por_departamento"] = {r["departamento"]: r["quantidade"] for r in cur.fetchall()}

                # Por prioridade
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

                # Por status
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
