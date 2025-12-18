# timezone_utils.py

from datetime import datetime, date, timedelta
import pytz
from .config import FORTALEZA_TZ

def agora_fortaleza() -> datetime:
    """Retorna a data e hora atual no fuso horário de Fortaleza."""
    return datetime.now(FORTALEZA_TZ)


def converter_para_fortaleza(dt: datetime) -> datetime:
    """Converte um objeto datetime para o fuso horário de Fortaleza."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume UTC se não tiver fuso horário (comportamento do código original)
        dt = pytz.utc.localize(dt)
    return dt.astimezone(FORTALEZA_TZ)


def formatar_data_hora_fortaleza(dt: datetime, formato: str = "%d/%m/%Y %H:%M") -> str:
    """Formata um objeto datetime para string no fuso horário de Fortaleza."""
    if not dt:
        return ""
    return converter_para_fortaleza(dt).strftime(formato)


def _to_tz_aware_start(d: date) -> datetime:
    """Retorna o início do dia (00:00:00) da data em Fortaleza."""
    if not d:
        return None
    return FORTALEZA_TZ.localize(datetime(d.year, d.month, d.day, 0, 0, 0))


def _to_tz_aware_end_exclusive(d: date) -> datetime:
    """Retorna o início do dia seguinte (00:00:00) da data em Fortaleza."""
    if not d:
        return None
    dd = d + timedelta(days=1)
    return FORTALEZA_TZ.localize(datetime(dd.year, dd.month, dd.day, 0, 0, 0))
