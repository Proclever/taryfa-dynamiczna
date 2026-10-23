"""Polskie dni ustawowo wolne od pracy – ta sama logika co w zapytaniu Power Query `Swieta`."""
from __future__ import annotations

from datetime import date, timedelta


def wielkanoc(rok: int) -> date:
    """Niedziela Wielkanocna (anonimowy algorytm gregoriański Meeusa/Jonesa/Butchera)."""
    a = rok % 19
    b, c = divmod(rok, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    miesiac, dzien = divmod(h + l - 7 * m + 114, 31)
    return date(rok, miesiac, dzien + 1)


def swieta(rok: int) -> dict[date, str]:
    w = wielkanoc(rok)
    s = {
        date(rok, 1, 1): "Nowy Rok",
        date(rok, 1, 6): "Trzech Króli",
        w: "Wielkanoc",
        w + timedelta(days=1): "Poniedziałek Wielkanocny",
        date(rok, 5, 1): "Święto Pracy",
        date(rok, 5, 3): "Święto Konstytucji 3 Maja",
        w + timedelta(days=49): "Zielone Świątki",
        w + timedelta(days=60): "Boże Ciało",
        date(rok, 8, 15): "Wniebowzięcie NMP",
        date(rok, 11, 1): "Wszystkich Świętych",
        date(rok, 11, 11): "Narodowe Święto Niepodległości",
        date(rok, 12, 25): "Boże Narodzenie",
        date(rok, 12, 26): "Drugi dzień Bożego Narodzenia",
    }
    if rok >= 2025:  # Wigilia wolna od pracy od 2025 r.
        s[date(rok, 12, 24)] = "Wigilia"
    return s


def typ_dnia(d: date) -> str:
    """'roboczy' / 'sobota' / 'niedziela lub święto' – jak kolumna DimData[TypDnia]."""
    if d in swieta(d.year) or d.weekday() == 6:
        return "niedziela lub święto"
    if d.weekday() == 5:
        return "sobota"
    return "roboczy"
