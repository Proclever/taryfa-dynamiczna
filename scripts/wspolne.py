"""Wspólne narzędzia pipeline'u: ścieżki, HTTP z backoffem, zapis CSV, strefa czasowa."""
from __future__ import annotations

import csv
import io
import logging
import os
import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# Na Windows strefy czasowe pochodzą z pakietu tzdata (requirements.txt).
TZ_PL = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc

KATALOG_REPO = Path(__file__).resolve().parent.parent
KATALOG_DANYCH = KATALOG_REPO / "data"
KATALOG_MARKET = KATALOG_DANYCH / "market"
KATALOG_POGODA = KATALOG_DANYCH / "pogoda"
KATALOG_PROFIL = KATALOG_DANYCH / "profil"
KATALOG_ZRODLA = KATALOG_DANYCH / "zrodla"
PLIK_STANU = KATALOG_DANYCH / "aktualizacja.csv"

log = logging.getLogger("taryfa")

_USER_AGENT = (
    "taryfa-dynamiczna-portfolio/1.0 (analiza publicznych danych PSE/IMGW; "
    "+https://github.com)"
)


def nowa_sesja() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json, */*"})
    return s


class BladPobierania(RuntimeError):
    pass


def pobierz(
    sesja: requests.Session,
    url: str,
    *,
    proby: int = 6,
    timeout: float = 60,
    oczekuj_json: bool = False,
    dozwolone_404: bool = False,
):
    """GET z wykładniczym backoffem (1, 2, 4, 8, 16 s + losowy jitter) na 429/5xx i błędy sieci.

    Zwraca obiekt JSON (oczekuj_json=True), surową odpowiedź albo None dla 404 (dozwolone_404=True).
    """
    ostatni_blad: Exception | None = None
    for proba in range(proby):
        try:
            r = sesja.get(url, timeout=timeout)
            if r.status_code == 404 and dozwolone_404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                raise BladPobierania(f"HTTP {r.status_code}" + (f" (Retry-After {retry_after})" if retry_after else ""))
            r.raise_for_status()
            return r.json() if oczekuj_json else r
        except (requests.RequestException, BladPobierania, ValueError) as e:
            ostatni_blad = e
            if isinstance(e, requests.HTTPError) and e.response is not None and 400 <= e.response.status_code < 500 \
                    and e.response.status_code != 429:
                raise  # błąd zapytania – ponawianie nic nie da
            czekaj = min(2 ** proba, 60) + random.uniform(0, 0.5)
            log.warning("  %s – próba %d/%d nieudana (%s), czekam %.1f s", url[:120], proba + 1, proby, e, czekaj)
            time.sleep(czekaj)
    raise BladPobierania(f"Nie udało się pobrać {url}: {ostatni_blad}")


def zapisz_csv_atomowo(sciezka: Path, naglowek: list[str], wiersze: list[list]) -> None:
    """Zapis CSV (UTF-8, przecinek, kropka dziesiętna) przez plik tymczasowy – brak półzapisanych plików."""
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    bufor = io.StringIO()
    w = csv.writer(bufor, lineterminator="\n")
    w.writerow(naglowek)
    w.writerows(wiersze)
    tresc = bufor.getvalue()
    if sciezka.exists() and sciezka.read_text(encoding="utf-8") == tresc:
        return  # bez zmian – nie dotykamy pliku (czysty diff w Git)
    # nazwa tymczasowa nie kończy się na .csv – przerwany zapis nie trafi do manifestu ani do Power Query
    fd, tmp = tempfile.mkstemp(dir=sciezka.parent, prefix=".tmp_", suffix=".part")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(tresc)
    os.replace(tmp, sciezka)


def czytaj_csv(sciezka: Path) -> list[dict]:
    if not sciezka.exists():
        return []
    with sciezka.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fmt_liczba(x: float | None, miejsca: int = 3) -> str:
    if x is None:
        return ""
    s = f"{x:.{miejsca}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fmt_czas(dt: datetime) -> str:
    """Czas bez strefy w formacie 'RRRR-MM-DD GG:MM:SS' (Power Query parsuje go jako datetime)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def zakres_dni(od: date, do: date):
    d = od
    while d <= do:
        yield d
        d += timedelta(days=1)


def poczatek_doby_utc(dzien: date) -> datetime:
    """Lokalna północ (Europe/Warsaw) danego dnia wyrażona w UTC."""
    return datetime(dzien.year, dzien.month, dzien.day, tzinfo=TZ_PL).astimezone(UTC)


def kwadranse_doby(dzien: date) -> int:
    """Liczba kwadransów w dobie lokalnej: 96, a w dni zmiany czasu 92 lub 100."""
    start = poczatek_doby_utc(dzien)
    koniec = poczatek_doby_utc(dzien + timedelta(days=1))
    return int((koniec - start).total_seconds() // 900)


def dzis_pl() -> date:
    return datetime.now(TZ_PL).date()


def czytaj_stan() -> dict[str, str]:
    """Metadane aktualizacji (data/aktualizacja.csv: klucz, wartosc) – czyta je też Power Query (InfoDane)."""
    return {w["klucz"]: w["wartosc"] for w in czytaj_csv(PLIK_STANU)}


def zapisz_stan(zmiany: dict[str, str]) -> None:
    """Dopisuje/zmienia klucze w data/aktualizacja.csv, zachowując pozostałe."""
    stan = czytaj_stan()
    stan.update({k: str(v) for k, v in zmiany.items()})
    zapisz_csv_atomowo(PLIK_STANU, ["klucz", "wartosc"], [[k, v] for k, v in stan.items()])


def konsola_utf8() -> None:
    """Polskie znaki i symbole (≤, –) także przy przekierowaniu wyjścia do pliku w Windows (cp1250)."""
    for strumien in (sys.stdout, sys.stderr):
        try:
            if (strumien.encoding or "").lower().replace("-", "") != "utf8":
                strumien.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
