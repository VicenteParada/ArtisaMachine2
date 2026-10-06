"""Días operativos de la empresa: de 00:00 a 00:00 en hora de Chile.

En la base todas las fechas están en UTC. Un día local se traduce a un rango
UTC semiabierto [inicio, fin): cada reporte pertenece exactamente a un día, así
que el corte de medianoche no pierde ni duplica reportes. Los cambios de horario
de verano (días de 23 o 25 horas) quedan resueltos por zoneinfo.
"""

from datetime import date, datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo


ZONA = os.environ.get("ARTISA_TIMEZONE", "America/Santiago")


def zona():
    return ZoneInfo(ZONA)


def _naive_utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def limites_utc(dia):
    """(inicio_utc, fin_utc) naive del día local `dia`."""
    tz = zona()
    inicio = datetime.combine(dia, time(0), tzinfo=tz)
    fin = datetime.combine(dia + timedelta(days=1), time(0), tzinfo=tz)
    return _naive_utc(inicio), _naive_utc(fin)


def dia_local(utc_naive):
    """Día local al que pertenece un instante UTC naive."""
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(zona()).date()


def hoy(utc_now=None):
    return dia_local(utc_now or datetime.now(timezone.utc).replace(tzinfo=None))


def parse_dia(texto):
    return date.fromisoformat(texto)
