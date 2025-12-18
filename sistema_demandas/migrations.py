# migrations.py

import streamlit as st
from .db_connector import get_db_connection
from .auth import hash_password

def verificar_e_atualizar_tabela_usuarios():
    """Verifica e atualiza a tabela de usuários (migração)."""
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
    """Verifica e atualiza a tabela de demandas (migração)."""
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
                    # A tabela será criada no init_database, apenas retorna OK
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
    """Inicializa o banco de dados, criando tabelas e o usuário admin padrão."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'America/Fortaleza'")

                # Criação da tabela demandas (se não existir)
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

                # Criação da tabela historico_demandas (se não existir)
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

                # Aplica migrações
                ok_d, msg_d = verificar_e_atualizar_tabela_demandas()
                if not ok_d:
                    conn.rollback()
                    return False, msg_d

                ok_u, msg_u = verificar_e_atualizar_tabela_usuarios()
                if not ok_u:
                    conn.rollback()
                    return False, msg_u

                # Cria índices
                try:
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_demandas_codigo ON demandas(codigo)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_status ON demandas(status)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_prioridade ON demandas(prioridade)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_demandas_data_criacao ON demandas(data_criacao DESC)")
                except Exception:
                    pass

                # Cria usuário admin padrão se não existir
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
