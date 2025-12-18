# sistema_demandas/email_html.py

from .config import TEMA_CORES, CORES_STATUS, CORES_PRIORIDADE

def gerar_comprovante_html(dados_email: dict) -> str:
    """
    Gera o corpo do e-mail em HTML com o comprovante de demanda,
    usando o tema visual da Cogerh.
    """
    codigo = dados_email.get("codigo", "SEM-COD")
    solicitante = dados_email.get("solicitante", "Não Informado")
    departamento = dados_email.get("departamento", "Não Informado")
    local = dados_email.get("local", "Não Informado")
    categoria = dados_email.get("categoria", "Geral")
    prioridade = dados_email.get("prioridade", "Média")
    status = dados_email.get("status", "Pendente")
    urgencia = "Sim" if bool(dados_email.get("urgencia", False)) else "Não"
    quantidade = dados_email.get("quantidade", 0)
    unidade = dados_email.get("unidade", "Unid.")
    item = dados_email.get("item", "Sem descrição.")
    observacoes = dados_email.get("observacoes", "Sem observações.")

    cor_status = CORES_STATUS.get(status, TEMA_CORES["danger"])
    cor_prioridade = CORES_PRIORIDADE.get(prioridade, TEMA_CORES["info"])

    # Estilos CSS embutidos para compatibilidade com a maioria dos clientes de e-mail
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Comprovante de Demanda - {codigo}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background-color: #f4f4f4;
                margin: 0;
                padding: 0;
            }}
            .container {{
                width: 100%;
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
            }}
            .header {{
                background-color: {TEMA_CORES['primary']}; /* Azul Cogerh */
                color: #ffffff;
                padding: 20px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 24px;
            }}
            .content {{
                padding: 20px;
            }}
            .card {{
                border: 1px solid #e0e0e0;
                border-left: 5px solid {TEMA_CORES['info']}; /* Ciano Água */
                padding: 15px;
                margin-bottom: 20px;
                border-radius: 6px;
                background-color: {TEMA_CORES['secondary']}; /* Azul Claro */
            }}
            .detail-table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 15px;
            }}
            .detail-table td {{
                padding: 10px 0;
                border-bottom: 1px solid #eee;
            }}
            .detail-table .label {{
                color: #555;
                font-weight: normal;
            }}
            .detail-table .value {{
                color: {TEMA_CORES['text']};
                font-weight: bold;
                text-align: right;
            }}
            .badge {{
                display: inline-block;
                padding: 5px 10px;
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
                color: white;
                text-transform: uppercase;
                margin-left: 5px;
            }}
            .description {{
                margin-top: 20px;
                padding: 15px;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                background-color: #f9f9f9;
            }}
            .footer {{
                text-align: center;
                padding: 15px;
                font-size: 12px;
                color: #888;
                border-top: 1px solid #eee;
            }}
        </style>
    </head>
    <body>
        <div style="padding: 20px;">
            <div class="container">
                <div class="header">
                    <h1>💧 Nova Demanda Registrada</h1>
                    <p style="margin: 5px 0 0 0; font-size: 14px;">Sistema de Gestão de Demandas - Cogerh</p>
                </div>
                <div class="content">
                    <div class="card">
                        <h2 style="margin-top: 0; color: {TEMA_CORES['primary']}; font-size: 18px;">
                            Comprovante: <span style="color: {TEMA_CORES['danger']};">{codigo}</span>
                        </h2>
                        <p style="margin: 5px 0 15px 0; font-size: 14px; color: #555;">
                            Sua solicitação foi registrada com sucesso.
                        </p>

                        <div style="display: flex; justify-content: space-between; margin-bottom: 15px;">
                            <span class="badge" style="background-color: {cor_status};">
                                Status: {status}
                            </span>
                            <span class="badge" style="background-color: {cor_prioridade};">
                                Prioridade: {prioridade}
                            </span>
                        </div>

                        <table class="detail-table">
                            <tr>
                                <td class="label">Solicitante:</td>
                                <td class="value">{solicitante}</td>
                            </tr>
                            <tr>
                                <td class="label">Departamento:</td>
                                <td class="value">{departamento}</td>
                            </tr>
                            <tr>
                                <td class="label">Local:</td>
                                <td class="value">{local}</td>
                            </tr>
                            <tr>
                                <td class="label">Categoria:</td>
                                <td class="value">{categoria}</td>
                            </tr>
                            <tr>
                                <td class="label">Quantidade:</td>
                                <td class="value">{quantidade} {unidade}</td>
                            </tr>
                            <tr>
                                <td class="label">Urgente:</td>
                                <td class="value">{urgencia}</td>
                            </tr>
                        </table>
                    </div>

                    <h3 style="color: {TEMA_CORES['primary']}; font-size: 16px;">Descrição da Demanda</h3>
                    <div class="description">
                        <p style="margin: 0; line-height: 1.5; color: #333;">{item}</p>
                    </div>

                    <h3 style="color: {TEMA_CORES['primary']}; font-size: 16px; margin-top: 20px;">Observações</h3>
                    <div class="description">
                        <p style="margin: 0; line-height: 1.5; color: #333;">{observacoes}</p>
                    </div>

                </div>
                <div class="footer">
                    Este é um e-mail automático. Por favor, não responda.
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
