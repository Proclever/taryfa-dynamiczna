"""Pobiera i aktualizuje dane do raportu „Czy taryfa dynamiczna opłaca się Twojemu domowi?”.

Przykłady (z katalogu repozytorium):
    python scripts/pobierz_dane.py                            # aktualizacja przyrostowa (pierwsze uruchomienie = pełne)
    python scripts/pobierz_dane.py --od 2025-03-01 --wymus pse # PSE od nowa od wskazanej doby
    python scripts/pobierz_dane.py --tylko pse                # tylko PSE (pse, imgw, profil – po przecinku)
    python scripts/pobierz_dane.py --wymus profil             # ponowne pobranie i odczyt profili Enea

Źródła są niezależne: błąd jednego (np. zmiana adresu pliku Enea) nie blokuje zapisu pozostałych;
skrypt kończy się wtedy kodem 1 i listą błędów.

Wynik trafia do katalogu data/ (CSV w UTF-8, przecinek jako separator, kropka dziesiętna) wraz z
plikiem data/manifest.csv, z którego Power Query bierze listę plików.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zrodla_imgw  # noqa: E402
import zrodla_osd  # noqa: E402
import zrodla_pse  # noqa: E402
from wspolne import (KATALOG_DANYCH, KATALOG_MARKET, KATALOG_POGODA, KATALOG_PROFIL, UTC, czytaj_csv,  # noqa: E402
                     dzis_pl, konsola_utf8, kwadranse_doby, log, zapisz_csv_atomowo, zapisz_stan)

ZRODLA = ("pse", "profil", "imgw")


def zapisz_manifest() -> list[list]:
    wiersze = []
    for typ, katalog, wzor in (("market", KATALOG_MARKET, "????-??.csv"),
                               ("pogoda", KATALOG_POGODA, "*.csv"),
                               ("profil", KATALOG_PROFIL, "*.csv")):
        for p in sorted(katalog.glob(wzor)):
            if p.name.startswith("."):
                continue  # pliki tymczasowe / ukryte
            dane = czytaj_csv(p)
            if not dane:
                continue
            klucz = "data" if "data" in dane[0] else "czas_utc"
            od, do = min(w[klucz] for w in dane)[:10], max(w[klucz] for w in dane)[:10]
            wiersze.append([typ, p.relative_to(KATALOG_DANYCH).as_posix(), len(dane), od, do])
    zapisz_csv_atomowo(KATALOG_DANYCH / "manifest.csv", ["typ", "plik", "wiersze", "od", "do"], wiersze)
    return wiersze


def kontrola_jakosci() -> dict:
    """Szybki raport: doby niepełne, braki RCE, liczba kwadransów z ceną ujemną."""
    niepelne, braki_rce, ujemne, razem, dni = [], 0, 0, 0, set()
    for p in sorted(KATALOG_MARKET.glob("????-??.csv")):
        po_dniach: dict[str, list[dict]] = {}
        for w in czytaj_csv(p):
            po_dniach.setdefault(w["data"], []).append(w)
        for d, ws in po_dniach.items():
            dni.add(d)
            if len(ws) != kwadranse_doby(date.fromisoformat(d)):
                niepelne.append(d)
            for w in ws:
                razem += 1
                if w["rce"] == "":
                    braki_rce += 1
                elif float(w["rce"]) < 0:
                    ujemne += 1
    return {"doby": len(dni), "kwadranse": razem, "niepelne_doby": niepelne, "braki_rce": braki_rce,
            "ujemne_rce": ujemne, "od": min(dni) if dni else None, "do": max(dni) if dni else None}


def _lista_zrodel(tekst: str) -> set[str]:
    zrodla = {z.strip() for z in tekst.split(",") if z.strip()}
    nieznane = zrodla - set(ZRODLA)
    if nieznane:
        raise argparse.ArgumentTypeError(f"nieznane źródła: {', '.join(sorted(nieznane))} (dozwolone: {', '.join(ZRODLA)})")
    return zrodla


def main(argv=None) -> int:
    konsola_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--od", type=date.fromisoformat, help="pierwsza doba PSE (RRRR-MM-DD); domyślnie cała historia "
                    "(pobierane są tylko doby brakujące, niekompletne i ostatnie)")
    ap.add_argument("--do", type=date.fromisoformat, help="ostatnia doba PSE; domyślnie wczoraj")
    ap.add_argument("--tylko", type=_lista_zrodel, default=set(ZRODLA), help="lista źródeł: pse,profil,imgw")
    ap.add_argument("--odswiez-dni", type=int, default=3, help="ile ostatnich dób zawsze pobrać ponownie")
    ap.add_argument("--wymus", type=_lista_zrodel, nargs="?", const=set(ZRODLA), default=set(),
                    help="pobierz ponownie także kompletne dane: bez wartości – wszystkie źródła, "
                         "albo lista, np. --wymus pse")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    do = a.do or (dzis_pl() - timedelta(days=1))

    def profil():
        lata = sorted({int(p.stem[:4]) for p in KATALOG_MARKET.glob("????-??.csv")}) or [dzis_pl().year]
        return zrodla_osd.aktualizuj(lata, "profil" in a.wymus)

    kroki = {
        "pse": ("PSE: RCE, zapotrzebowanie KSE, PV i wiatr (do %s)" % do,
                lambda: zrodla_pse.aktualizuj(a.od, do, a.odswiez_dni, "pse" in a.wymus)),
        "profil": ("OSD: standardowy profil zużycia G11 (Enea Operator)", profil),
        "imgw": ("IMGW: temperatura godzinowa, stacja %s" % zrodla_imgw.STACJA_KOD,
                 lambda: zrodla_imgw.aktualizuj(wymus="imgw" in a.wymus)),
    }
    wyniki, bledy = {}, {}
    for nazwa in ZRODLA:  # kolejność ma znaczenie: lata profilu wynikają z danych PSE
        if nazwa not in a.tylko:
            continue
        opis, krok = kroki[nazwa]
        log.info("== %s", opis)
        try:
            wyniki[nazwa] = krok()
        except Exception as e:  # noqa: BLE001 – błąd jednego źródła nie blokuje pozostałych
            log.exception("%s: przerwano – %s", nazwa.upper(), e)
            bledy[nazwa] = str(e).splitlines()[0]

    manifest = zapisz_manifest()
    jakosc = kontrola_jakosci()
    zapisz_stan({
        "ostatnia_aktualizacja_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "pierwsza_doba_pse": jakosc["od"] or "",
        "ostatnia_doba_pse": jakosc["do"] or "",
        "stacja_imgw": f"{zrodla_imgw.STACJA_KOD} (Poznań-Ławica)",
        "profil_osd": "Enea Operator, G11",
    })
    log.info("== Podsumowanie: %s", wyniki)
    log.info("   doby PSE: %s (%s – %s), kwadranse: %s, braki RCE: %s, kwadranse z RCE < 0: %s",
             jakosc["doby"], jakosc["od"], jakosc["do"], jakosc["kwadranse"], jakosc["braki_rce"], jakosc["ujemne_rce"])
    if jakosc["niepelne_doby"]:
        log.warning("   niepełne doby (%d): %s", len(jakosc["niepelne_doby"]), ", ".join(jakosc["niepelne_doby"][:20]))
    for p in sorted(KATALOG_PROFIL.glob("enea_g11_*.csv")):
        wiersze = [[w["data"], w["slot"], w["wartosc"], w["zrodlo"]] for w in czytaj_csv(p)]
        if wiersze:
            log.info("   %s: %s; źródło: %s", p.name, zrodla_osd.opis_ksztaltu(wiersze), wiersze[0][3])
    log.info("   manifest: %d plików", len(manifest))
    if bledy:
        for nazwa, opis in bledy.items():
            log.error("   BŁĄD %s: %s", nazwa.upper(), opis)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
