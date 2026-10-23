"""PSE API v2 (api.raporty.pse.pl): RCE, zapotrzebowanie KSE, generacja PV i wiatrowa – dzień po dniu.

Pola (zweryfikowane w działających projektach open source korzystających z API v2):
  rce-pln      dtime, dtime_utc, period, rce_pln, business_date
  kse-load     dtime, dtime_utc, load_actual (+ load_fcst), business_date
  his-wlk-cal  dtime, dtime_utc, pv, wi, demand, business_date   (wartości w MW, 15 min)
`dtime` oznacza KONIEC okresu ("00:15" dla 00:00–00:15), a ostatni kwadrans doby ma `dtime` = północ następnego dnia.
RCE do 30.09.2025 było godzinowe (API powtarza wartość godziny w czterech kwadransach), od 1.10.2025 jest 15-minutowe.
Skrypt sprowadza każdy raport do siatki kwadransów doby lokalnej (92/96/100), więc rekordy godzinowe są rozpisywane x4.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

from wspolne import (
    KATALOG_MARKET, TZ_PL, UTC, czytaj_csv, fmt_czas, fmt_liczba, kwadranse_doby, log, nowa_sesja,
    poczatek_doby_utc, pobierz, zakres_dni, zapisz_csv_atomowo,
)

API = "https://api.raporty.pse.pl/api/"
POCZATEK_API_V2 = date(2024, 6, 14)
KWADRANS = timedelta(minutes=15)
PAUZA_S = 0.15  # grzecznościowa przerwa między zapytaniami

RAPORTY = {
    "rce": ("rce-pln", "dtime,dtime_utc,period,rce_pln,business_date", {"rce": "rce_pln"}),
    "kse": ("kse-load", "dtime,dtime_utc,period,load_actual,business_date", {"zapotrzebowanie": "load_actual"}),
    "wlk": ("his-wlk-cal", "dtime,dtime_utc,period,pv,wi,demand,business_date",
            {"pv": "pv", "wiatr": "wi", "zapotrzebowanie_wlk": "demand"}),
}

NAGLOWEK = ["data", "kwadrans", "od_utc", "od_lokalnie", "czas_letni", "rce", "zapotrzebowanie", "pv", "wiatr"]

_RE_OKRES = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def _parsuj_czas(s) -> datetime | None:
    if not s:
        return None
    s = str(s).strip().replace("T", " ").rstrip("Z")
    s = s.split(".")[0].split("+")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _liczba(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _dlugosc_okresu(rec: dict, domyslna: timedelta = KWADRANS) -> timedelta:
    m = _RE_OKRES.search(str(rec.get("period") or ""))
    if not m:
        return domyslna
    h1, m1, h2, m2 = map(int, m.groups())
    minuty = ((h2 * 60 + m2) - (h1 * 60 + m1)) % (24 * 60)
    # W dobie zmiany czasu opis okresu bywa „02:45 - 02:00” – akceptujemy tylko sensowne długości.
    return timedelta(minutes=minuty) if minuty in (15, 30, 60) else domyslna


def _start_okresu_lokalnie(rec: dict) -> str | None:
    m = _RE_OKRES.search(str(rec.get("period") or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _url(raport: str, dzien: date, select: str | None) -> str:
    filtr = quote(f"business_date eq '{dzien.isoformat()}'", safe="'")
    url = f"{API}{raport}?$filter={filtr}&$first=2000"
    if select:
        url += f"&$select={select}"
    return url


def pobierz_raport(sesja: requests.Session, raport: str, select: str, dzien: date) -> list[dict]:
    """Wszystkie rekordy raportu dla doby handlowej (z obsługą stronicowania nextLink)."""
    try:
        url = _url(raport, dzien, select)
        dane = pobierz(sesja, url, oczekuj_json=True)
    except requests.HTTPError as e:  # np. pole spoza $select – ponów bez wyboru pól
        if e.response is not None and e.response.status_code == 400:
            log.warning("  %s: $select odrzucony (%s), pobieram wszystkie pola", raport, e.response.status_code)
            url = _url(raport, dzien, None)
            dane = pobierz(sesja, url, oczekuj_json=True)
        else:
            raise
    wiersze = list(dane.get("value", []))
    nastepny = dane.get("nextLink") or dane.get("@odata.nextLink")
    while nastepny:
        time.sleep(PAUZA_S)
        dane = pobierz(sesja, nastepny, oczekuj_json=True)
        wiersze += dane.get("value", [])
        nastepny = dane.get("nextLink") or dane.get("@odata.nextLink")
    return wiersze


def rozpisz_na_kwadranse(rekordy: list[dict], dzien: date, pola: dict[str, str]) -> dict[datetime, dict]:
    """Mapuje rekordy raportu na początki kwadransów w UTC; rekord godzinowy trafia do 4 kwadransów.

    Początek okresu = dtime_utc - długość okresu (dtime to koniec okresu). Hipotezę weryfikujemy
    z polem `period` (początek w czasie lokalnym); jeśli większość rekordów pasuje lepiej do wariantu
    „dtime = początek okresu”, używamy go (zabezpieczenie na wypadek zmiany konwencji API).
    """
    if not rekordy:
        return {}
    start_doby = poczatek_doby_utc(dzien)
    # Bez pola `period` rozpoznajemy dane godzinowe po liczbie rekordów (23/24/25 zamiast 92/96/100).
    bez_okresu = not any(r.get("period") for r in rekordy)
    dl_domyslna = timedelta(hours=1) if bez_okresu and len(rekordy) * 4 == kwadranse_doby(dzien) else KWADRANS
    if not any(_parsuj_czas(r.get("dtime_utc")) for r in rekordy):
        rekordy = sorted(rekordy, key=lambda r: str(r.get("dtime") or ""))  # najlepsze, co mamy bez UTC

    def poczatki(przesuniecie_koniec: bool) -> list[tuple[datetime, timedelta, dict]]:
        out = []
        wszystkie_bez_utc = not any(_parsuj_czas(r.get("dtime_utc")) for r in rekordy)
        przesuniecie = timedelta()
        for r in rekordy:
            dl = _dlugosc_okresu(r, dl_domyslna)
            t = _parsuj_czas(r.get("dtime_utc"))
            if t is not None:
                t = t.replace(tzinfo=UTC)
                start = t - dl if przesuniecie_koniec else t
            elif wszystkie_bez_utc:
                # brak dtime_utc: rekordy idą chronologicznie – liczymy od lokalnej północy w UTC
                start = start_doby + przesuniecie
                przesuniecie += dl
            else:
                continue
            out.append((start, dl, r))
        return out

    def zgodnosc(lista) -> int:
        ok = 0
        for start, _, r in lista:
            p = _start_okresu_lokalnie(r)
            if p and start.astimezone(TZ_PL).strftime("%H:%M") == p:
                ok += 1
        return ok

    wariant_koniec = poczatki(True)
    if any(r.get("period") for r in rekordy) and any(_parsuj_czas(r.get("dtime_utc")) for r in rekordy):
        wariant_start = poczatki(False)
        if zgodnosc(wariant_start) > zgodnosc(wariant_koniec):
            log.warning("  %s: dtime wygląda na początek okresu – stosuję wariant alternatywny", dzien)
            wariant_koniec = wariant_start

    wynik: dict[datetime, dict] = {}
    for start, dl, r in wariant_koniec:
        wartosci = {nazwa: _liczba(r.get(pole)) for nazwa, pole in pola.items()}
        q = start
        while q < start + dl:
            wynik[q] = wartosci
            q += KWADRANS
    return wynik


def pobierz_dzien(sesja: requests.Session, dzien: date) -> list[list] | None:
    """Wiersze CSV dla jednej doby (siatka kwadransów) albo None, gdy PSE nie ma jeszcze RCE."""
    mapy = {}
    for klucz, (raport, select, pola) in RAPORTY.items():
        rek = pobierz_raport(sesja, raport, select, dzien)
        mapy[klucz] = rozpisz_na_kwadranse(rek, dzien, pola)
        time.sleep(PAUZA_S)
    if not mapy["rce"]:
        return None
    n = kwadranse_doby(dzien)
    start = poczatek_doby_utc(dzien)
    wiersze, braki = [], defaultdict(int)
    for k in range(n):
        q = start + k * KWADRANS
        rce = mapy["rce"].get(q, {}).get("rce")
        kse = mapy["kse"].get(q, {}).get("zapotrzebowanie")
        wlk = mapy["wlk"].get(q, {})
        zap = kse if kse is not None else wlk.get("zapotrzebowanie_wlk")
        pv, wi = wlk.get("pv"), wlk.get("wiatr")
        for nazwa, v in (("rce", rce), ("zapotrzebowanie", zap), ("pv", pv), ("wiatr", wi)):
            if v is None:
                braki[nazwa] += 1
        lok = q.astimezone(TZ_PL)
        wiersze.append([
            dzien.isoformat(), k + 1, fmt_czas(q.replace(tzinfo=None)), fmt_czas(lok.replace(tzinfo=None)),
            1 if lok.dst() else 0, fmt_liczba(rce, 2), fmt_liczba(zap, 1), fmt_liczba(pv, 1), fmt_liczba(wi, 1),
        ])
    if braki:
        log.warning("  %s: braki w danych (kwadranse): %s", dzien, dict(braki))
    return wiersze


# ---------------------------------------------------------------- pliki miesięczne

def plik_miesiaca(rok: int, miesiac: int) -> Path:
    return KATALOG_MARKET / f"{rok:04d}-{miesiac:02d}.csv"


def wczytaj_miesiac(rok: int, miesiac: int) -> dict[date, list[list]]:
    dni: dict[date, list[list]] = defaultdict(list)
    for r in czytaj_csv(plik_miesiaca(rok, miesiac)):
        dni[date.fromisoformat(r["data"])].append([r[k] for k in NAGLOWEK])
    for d in dni:
        dni[d].sort(key=lambda w: int(w[1]))
    return dict(dni)


def zapisz_miesiac(rok: int, miesiac: int, dni: dict[date, list[list]]) -> None:
    wiersze = [w for d in sorted(dni) for w in dni[d]]
    if wiersze:
        zapisz_csv_atomowo(plik_miesiaca(rok, miesiac), NAGLOWEK, wiersze)


def doba_kompletna(wiersze: list[list] | None, dzien: date) -> bool:
    if not wiersze or len(wiersze) != kwadranse_doby(dzien):
        return False
    i = {k: NAGLOWEK.index(k) for k in ("rce", "zapotrzebowanie", "pv", "wiatr")}
    if any(w[i["rce"]] in ("", None) for w in wiersze):
        return False
    for pole in ("zapotrzebowanie", "pv", "wiatr"):  # dopuszczamy pojedyncze luki w danych systemowych
        if sum(1 for w in wiersze if w[i[pole]] in ("", None)) > 0.1 * len(wiersze):
            return False
    return True


def ostatnia_doba_w_danych() -> date | None:
    pliki = sorted(KATALOG_MARKET.glob("????-??.csv"))
    for p in reversed(pliki):
        wiersze = czytaj_csv(p)
        if wiersze:
            return max(date.fromisoformat(w["data"]) for w in wiersze)
    return None


MAKS_LUK_NA_PRZEBIEG = 60  # starsze brakujące/niekompletne doby ponawiane w jednym przebiegu


def aktualizuj(od: date | None, do: date, odswiez_dni: int = 3, wymus: bool = False) -> dict:
    """Pobiera doby z zakresu [od, do]: brakujące, niekompletne i zawsze `odswiez_dni` ostatnich.

    Bez `od` przegląda całą historię od startu API v2, więc luki (np. doba, której PSE nie opublikowało
    w dniu pobierania) są ponawiane w kolejnych przebiegach – najwyżej MAKS_LUK_NA_PRZEBIEG starszych dób naraz.
    `wymus` pobiera ponownie także doby kompletne.
    """
    od_skanu = max(od or POCZATEK_API_V2, POCZATEK_API_V2)
    if od_skanu > do:
        log.info("PSE: brak dób do pobrania (%s > %s)", od_skanu, do)
        return {"pobrane": 0, "pominiete": 0}
    granica_swiezych = do - timedelta(days=odswiez_dni)
    ostatnia = ostatnia_doba_w_danych()
    sesja = nowa_sesja()
    statystyki = {"pobrane": 0, "pominiete": 0, "bez_rce": 0, "luki": 0}
    miesiace = sorted({(d.year, d.month) for d in zakres_dni(od_skanu, do)})
    t0 = time.time()
    for rok, mies in miesiace:
        dni = wczytaj_miesiac(rok, mies)
        zmiany = False
        try:
            for d in zakres_dni(max(od_skanu, date(rok, mies, 1)), do):
                if (d.year, d.month) != (rok, mies):
                    break
                kompletna = doba_kompletna(dni.get(d), d)
                if not wymus and kompletna and d <= granica_swiezych:
                    statystyki["pominiete"] += 1
                    continue
                stara_luka = not kompletna and ostatnia is not None and d <= min(ostatnia, granica_swiezych)
                if stara_luka and not wymus:
                    if statystyki["luki"] >= MAKS_LUK_NA_PRZEBIEG:
                        continue
                    statystyki["luki"] += 1
                    log.info("  %s: doba brakująca lub niekompletna – ponawiam", d)
                wiersze = pobierz_dzien(sesja, d)
                if wiersze is None:
                    statystyki["bez_rce"] += 1
                    log.info("  %s: PSE nie opublikowało (jeszcze) RCE", d)
                    continue
                dni[d] = wiersze
                zmiany = True
                statystyki["pobrane"] += 1
                if statystyki["pobrane"] % 10 == 0:
                    log.info("  pobrano %d dób (ostatnia %s, %.0f s)", statystyki["pobrane"], d, time.time() - t0)
        finally:
            if zmiany:  # także po błędzie – pobrane doby nie przepadają
                zapisz_miesiac(rok, mies, dni)
                log.info("PSE: zapisano %s", plik_miesiaca(rok, mies).name)
    return statystyki
