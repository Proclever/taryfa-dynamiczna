"""IMGW-PIB (danepubliczne.imgw.pl): godzinowa temperatura powietrza ze stacji synoptycznej.

Dane terminowe SYNOP (pliki s_t_*.csv w archiwach ZIP, bez nagłówka, kodowanie CP1250).
Układ kolumn wg s_t_format.txt: 1 kod stacji, 3 rok, 4 miesiąc, 5 dzień, 6 godzina (UTC),
30 temperatura powietrza [°C], 31 status pomiaru TEMP (8 = brak pomiaru).
Katalogi archiwum: lata lub zakresy lat; pliki per stacja ("2024_330_s.zip") albo per miesiąc
(wszystkie stacje) – obsługujemy oba warianty.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from wspolne import (KATALOG_POGODA, BladPobierania, czytaj_csv, czytaj_stan, dzis_pl, fmt_czas, fmt_liczba, log,
                     nowa_sesja, pobierz, zapisz_csv_atomowo, zapisz_stan)

BAZA = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne/terminowe/synop/"
FORMAT = BAZA + "s_t_format.txt"

# Poznań-Ławica: kod IMGW 352160330, WMO 12330. Zmień tu, jeśli chcesz inną stację.
STACJA_KOD = "352160330"
STACJA_ID = "330"
STACJA_PLIK = "poznan"
PIERWSZY_ROK_NORMY = 2016  # 10 pełnych lat do normy stopniogodzin (2016–2025)

KOLUMNA_TEMP = 29     # 0-indeksowo (pole 30)
KOLUMNA_STATUS = 30   # pole 31
NAGLOWEK = ["czas_utc", "temp"]
KLUCZ_STANU = "imgw_sprawdzono"   # data ostatniego sprawdzenia archiwum (w data/aktualizacja.csv)
ODSWIEZ_CO_DNI = 7
_RE_LICZBA_DZIES = re.compile(r"-?\d+[.,]\d+")

_RE_HREF = re.compile(r'href="([^"?#]+)"', re.I)


def _lista(sesja, url: str) -> list[str]:
    html = pobierz(sesja, url).text
    return [h for h in _RE_HREF.findall(html) if not h.startswith(("/", "http", "..", "?"))]


def _lata_katalogu(nazwa: str) -> range | None:
    lata = [int(x) for x in re.findall(r"\d{4}", nazwa)]
    if not lata:
        return None
    return range(min(lata), max(lata) + 1)


def _sprawdz_format(sesja) -> None:
    """Porównuje udokumentowany układ (TEMP = pole 30, status = pole 31) z s_t_format.txt – tylko ostrzeżenie.

    Kolumn nie bierzemy z pliku formatu (heurystyka mogłaby po cichu wskazać inne pole); poprawność
    układu sprawdzają dane w _pobierz_zip (status nie może wyglądać jak temperatura).
    """
    try:
        tekst = pobierz(sesja, FORMAT).content.decode("cp1250", errors="replace")
        linie = [l.strip() for l in tekst.splitlines() if l.strip()]
        start = next(i for i, l in enumerate(linie) if re.search(r"Kod stacji", l, re.I))
        temp = next(i for i, l in enumerate(linie)
                    if i > start and re.search(r"Temperatura powietrza\b", l, re.I))
        status_ok = re.search(r"Status pomiaru\s+TEMP\b", linie[temp + 1], re.I) is not None
        if temp - start != KOLUMNA_TEMP or not status_ok:
            log.warning("IMGW: s_t_format.txt sugeruje inny układ (TEMP w polu %d) – używam udokumentowanego "
                        "(pole %d, status %d); jeśli dane wyglądają źle, sprawdź plik formatu",
                        temp - start + 1, KOLUMNA_TEMP + 1, KOLUMNA_STATUS + 1)
    except Exception as e:  # noqa: BLE001 – plik formatu jest tylko pomocniczy
        log.warning("IMGW: nie odczytano s_t_format.txt (%s) – zakładam udokumentowany układ kolumn", e)


def parsuj_csv_synop(tekst: str, kod_stacji: str = STACJA_KOD, kol_temp: int = KOLUMNA_TEMP,
                     kol_status: int = KOLUMNA_STATUS, statystyka: Counter | None = None) -> dict[datetime, float | None]:
    wynik: dict[datetime, float | None] = {}
    for w in csv.reader(io.StringIO(tekst)):
        if len(w) <= kol_status or w[0].strip().strip('"') != kod_stacji:
            continue
        try:
            t = datetime(int(w[2]), int(w[3]), int(w[4]), int(w[5]))
        except ValueError:
            continue
        status = w[kol_status].strip()
        surowa = w[kol_temp].strip().replace(",", ".")
        temp = None
        if status != "8" and surowa not in ("", "-"):
            try:
                temp = float(surowa)
            except ValueError:
                temp = None
        if statystyka is not None:
            statystyka["wiersze"] += 1
            statystyka["status_jak_liczba"] += bool(_RE_LICZBA_DZIES.fullmatch(status))
            statystyka["brak_temperatury"] += temp is None and status != "8"
        if temp is not None and not (-45 <= temp <= 45):  # odrzucamy wartości niefizyczne
            temp = None
        wynik[t] = temp
    return wynik


def _pobierz_zip(sesja, url: str) -> dict[datetime, float | None]:
    r = pobierz(sesja, url, timeout=120)
    wynik: dict[datetime, float | None] = {}
    stat: Counter = Counter()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for nazwa in z.namelist():
            if not nazwa.lower().endswith(".csv") or "s_t" not in nazwa.lower():
                continue
            tekst = z.read(nazwa).decode("cp1250", errors="replace")
            wynik.update(parsuj_csv_synop(tekst, STACJA_KOD, statystyka=stat))
    if stat["wiersze"] >= 100 and (stat["status_jak_liczba"] > 0.05 * stat["wiersze"]
                                   or stat["brak_temperatury"] > 0.2 * stat["wiersze"]):
        raise BladPobierania(
            f"IMGW: układ kolumn w {url.rsplit('/', 1)[-1]} nie pasuje do opisu (pole {KOLUMNA_TEMP + 1} = temperatura, "
            f"{KOLUMNA_STATUS + 1} = status): {dict(stat)} – sprawdź s_t_format.txt i popraw KOLUMNA_TEMP/KOLUMNA_STATUS")
    return wynik


def plik_roku(rok: int) -> Path:
    return KATALOG_POGODA / f"{STACJA_PLIK}_{rok}.csv"


def aktualizuj(lata: list[int] | None = None, wymus: bool = False) -> dict:
    """Pobiera brakujące lata; bieżący i poprzedni rok (do marca) sprawdza ponownie co ODSWIEZ_CO_DNI dni.

    Datę sprawdzenia trzymamy w data/aktualizacja.csv, a nie w czasie modyfikacji pliku –
    w GitHub Actions checkout ustawia wszystkim plikom bieżący czas.
    """
    dzis = dzis_pl()
    lata = lata or list(range(PIERWSZY_ROK_NORMY, dzis.year + 1))
    try:
        sprawdzono = date.fromisoformat(czytaj_stan().get(KLUCZ_STANU, ""))
    except ValueError:
        sprawdzono = None
    niedawno = sprawdzono is not None and (dzis - sprawdzono).days < ODSWIEZ_CO_DNI
    do_pobrania = []
    for rok in lata:
        stary_rok = rok < dzis.year - 1 or (rok == dzis.year - 1 and dzis.month > 3)
        if not wymus and plik_roku(rok).exists() and (stary_rok or niedawno):
            continue
        do_pobrania.append(rok)
    if not do_pobrania:
        log.info("IMGW: dane temperatury aktualne (sprawdzono %s)", sprawdzono)
        return {"lata": []}
    sesja = nowa_sesja()
    _sprawdz_format(sesja)
    katalogi = [k.rstrip("/") for k in _lista(sesja, BAZA) if k.endswith("/")]
    zapisane = []
    for rok in do_pobrania:
        kat = next((k for k in katalogi if (z := _lata_katalogu(k)) and rok in z), None)
        if kat is None:
            log.warning("IMGW: brak katalogu z rokiem %d (dane mogą jeszcze nie być opublikowane)", rok)
            continue
        url_kat = f"{BAZA}{kat}/"
        pliki = [p for p in _lista(sesja, url_kat) if p.lower().endswith(".zip")]
        stacyjne = [p for p in pliki if re.search(rf"(^|_){STACJA_ID}(_|\.)", p) and str(rok) in p]
        if not stacyjne:
            stacyjne = [p for p in pliki if re.search(rf"(^|_){STACJA_ID}(_|\.)", p)]
        kandydaci = stacyjne or [p for p in pliki if p.startswith(f"{rok}_")]
        if not kandydaci:
            log.warning("IMGW: w %s nie ma plików dla roku %d", url_kat, rok)
            continue
        pomiary: dict[datetime, float | None] = {}
        for p in kandydaci:
            log.info("IMGW: pobieram %s", p)
            pomiary.update(_pobierz_zip(sesja, url_kat + p))
        pomiary = {t: v for t, v in pomiary.items() if t.year == rok}
        if not pomiary:
            log.warning("IMGW: brak pomiarów stacji %s w roku %d", STACJA_KOD, rok)
            continue
        wiersze = [[fmt_czas(t), fmt_liczba(v, 1)] for t, v in sorted(pomiary.items())]
        zapisz_csv_atomowo(plik_roku(rok), NAGLOWEK, wiersze)
        brak = sum(1 for _, v in pomiary.items() if v is None)
        log.info("IMGW: %d – %d godzin (do %s UTC), %d braków", rok, len(pomiary), max(pomiary), brak)
        zapisane.append(rok)
    zapisz_stan({KLUCZ_STANU: dzis.isoformat()})
    return {"lata": zapisane}


def wczytaj_wszystko() -> dict[datetime, float]:
    out = {}
    for p in sorted(KATALOG_POGODA.glob(f"{STACJA_PLIK}_*.csv")):
        for w in czytaj_csv(p):
            if w["temp"] != "":
                out[datetime.fromisoformat(w["czas_utc"])] = float(w["temp"])
    return out
