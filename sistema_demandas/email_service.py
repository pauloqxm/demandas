# email_service.py

import os
import smtplib
import socket
import requests
from email.message import EmailMessage
from .config import get_email_config, get_brevo_config, _env_int

def _tcp_probe(host: str, port: int, timeout: int = 5) -> tuple:
    """Verifica se a porta TCP está aberta no host."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, "OK"
    except Exception as e:
        return False, str(e)


def enviar_email_brevo_api(assunto: str, corpo_texto: str) -> tuple:
    """Envia e-mail usando a API Brevo (Sendinblue)."""
    cfg = get_brevo_config()
    if not cfg["api_key"]:
        return False, "BREVO_API_KEY não configurada"
    if not cfg["sender_email"]:
        return False, "BREVO_SENDER não configurado"
    if not cfg["to"]:
        return False, "BREVO_TO vazio"

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": cfg["api_key"],
        "content-type": "application/json",
    }
    payload = {
        "sender": {"name": cfg["sender_name"], "email": cfg["sender_email"]},
        "to": [{"email": e} for e in cfg["to"]],
        "subject": assunto,
        "textContent": corpo_texto,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"])
        if 200 <= r.status_code < 300:
            return True, "E-mail enviado ao responsável"
        return False, f"Brevo API erro {r.status_code}. {r.text}"
    except Exception as e:
        return False, f"Brevo API falhou. {str(e)}"


def enviar_email_smtp(assunto: str, corpo: str) -> tuple:
    """Envia e-mail usando o protocolo SMTP."""
    cfg = get_email_config()

    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return False, "SMTP não configurado nas variáveis"
    if not cfg["to"]:
        return False, "MAIL_TO vazio"

    mail_from = cfg["from"] or cfg["user"]

    # Probe TCP para erro mais claro
    timeout_int = _env_int("MAIL_SEND_TIMEOUT", 20)
    ok_tcp, msg_tcp = _tcp_probe(cfg["host"], cfg["port"], timeout=min(6, timeout_int))
    if not ok_tcp:
        return False, f"TCP timeout em {cfg['host']}:{cfg['port']}. {msg_tcp}"

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = mail_from
    msg["To"] = ", ".join(cfg["to"])
    if cfg["cc"]:
        msg["Cc"] = ", ".join(cfg["cc"])
    msg.set_content(corpo)

    destinos = cfg["to"] + cfg["cc"] + cfg["bcc"]

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout_int) as server:
            server.ehlo()
            if cfg["starttls"]:
                server.starttls()
                server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg, from_addr=mail_from, to_addrs=destinos)
        return True, "SMTP OK"
    except Exception as e:
        return False, str(e)


def enviar_email_nova_demanda(dados_email: dict) -> tuple:
    """
    Envia e-mail de notificação de nova demanda, usando Brevo API como preferencial
    e SMTP como fallback.
    """
    cfg_mail = get_email_config()
    if not cfg_mail["enabled_new"]:
        return True, "Envio de email desativado por variável"

    codigo = dados_email.get("codigo", "SEM-COD")

    subject_prefix = (os.environ.get("MAIL_SUBJECT_PREFIX") or "Sistema de Demandas").strip()
    assunto = f"{subject_prefix} | Nova demanda {codigo}"

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

    brevo_cfg = get_brevo_config()
    brevo_ok = bool(brevo_cfg.get("api_key"))

    # 1. Preferir API no Railway quando configurada
    if brevo_ok:
        ok_api, msg_api = enviar_email_brevo_api(assunto, corpo)
        if ok_api:
            return True, msg_api
        # Se API falhar, tenta SMTP como fallback
        ok_smtp, msg_smtp = enviar_email_smtp(assunto, corpo)
        if ok_smtp:
            return True, f"API falhou, mas SMTP funcionou. {msg_smtp}"
        return False, f"API falhou. {msg_api}. SMTP também falhou. {msg_smtp}"

    # 2. Sem API configurada, tenta SMTP
    ok_smtp, msg_smtp = enviar_email_smtp(assunto, corpo)
    if ok_smtp:
        return True, msg_smtp

    # 3. Se SMTP falhar e tiver API, tenta fallback
    if brevo_ok:
        ok_api, msg_api = enviar_email_brevo_api(assunto, corpo)
        if ok_api:
            return True, f"SMTP falhou, mas API funcionou. {msg_api}"
        return False, f"SMTP falhou. {msg_smtp}. API também falhou. {msg_api}"

    return False, f"Falha ao enviar email. {msg_smtp}"
