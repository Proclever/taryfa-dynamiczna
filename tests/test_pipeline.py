"""Testy pipeline'u na danych syntetycznych (bez sieci). Uruchom: python -m pytest -q"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import kalendarz  # noqa: E402
import zrodla_imgw  # noqa: E402
import zrodla_osd  # noqa: E402
import zrodla_pse  # noqa: E402
from wspolne import TZ_PL, UTC, kwadranse_doby, poczatek_doby_utc  # noqa: E402


# ------------------------------------------------------------------ kalendarz

def test_wielkanoc_i_swieta_ruchome():
    assert kalendarz.wielkanoc(2024) == date(2024, 3, 31)
    assert kalendarz.wielkanoc(2025) == date(2025, 4, 20)
    assert kalendarz.wielkanoc(2026) == date(2026, 4, 5)
    assert kalendarz.wielkanoc(2027) == date(2027, 3, 28)
    s25 = kalendarz.swieta(2025)
    assert s25[date(2025, 6, 19)] == "Boże Ciało"
    assert s25[date(2025, 6, 8)] == "Zielone Świątki"
    assert date(2025, 12, 24) in s25 and date(2024, 12, 24) not in kalendarz.swieta(2024)
    assert kalendarz.typ_dnia(date(2026, 6, 4)) == "niedziela lub święto"  # Boże Ciało (czwartek)
    assert kalendarz.typ_dnia(date(2026, 10, 24)) == "sobota"
    assert kalendarz.typ_dnia(date(2026, 10, 21)) == "roboczy"


def test_dlugosc_dob_zmiany_czasu():
    assert kwadranse_doby(date(2025, 3, 30)) == 92
    assert kwadranse_doby(date(2025, 10, 26)) == 100
    assert kwadranse_doby(date(2025, 10, 27)) == 96


# ------------------------------------------------------------------ PSE

def _rekordy_api(dzien: date, cena, godzinowe_wartosci: bool, tylko_godzinowe: bool = False):
    """Rekordy w konwencji API v2: dtime = KONIEC okresu (czas lokalny), dtime_utc = koniec w UTC."""
    start = poczatek_doby_utc(dzien)
    n = kwadranse_doby(dzien)
    krok = timedelta(hours=1) if tylko_godzinowe else timedelta(minutes=15)
    ile = n // 4 if tylko_godzinowe else n
    out = []
    for k in range(ile):
        s = start + k * krok
        e = s + krok
        sl, el = s.astimezone(TZ_PL), e.astimezone(TZ_PL)
        wartosc = cena(k // 4 if (godzinowe_wartosci and not tylko_godzinowe) else k)
        rec = {
            "dtime": el.strftime("%Y-%m-%d %H:%M:%S"),
            "dtime_utc": e.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "rce_pln": f"{wartosc:.2f}", "business_date": dzien.isoformat(),
            "load_actual": 20000 + k, "pv": max(0.0, 5000 - abs(k - 48) * 100), "wi": 3000.0, "demand": 19999,
        }
        if not tylko_godzinowe:
            rec["period"] = f"{sl:%H:%M} - {el:%H:%M}"
        out.append(rec)
    return out


@pytest.fixture
def api(monkeypatch):
    stan = {"godzinowe": False, "tylko_godzinowe": False}

    def falszywy(sesja, raport, select, dzien):
        rek = _rekordy_api(dzien, lambda i: 100 + i, stan["godzinowe"], stan["tylko_godzinowe"] and raport == "rce-pln")
        return rek

    monkeypatch.setattr(zrodla_pse, "pobierz_raport", falszywy)
    monkeypatch.setattr(zrodla_pse.time, "sleep", lambda s: None)
    return stan


@pytest.mark.parametrize("dzien,n", [(date(2025, 10, 22), 96), (date(2025, 3, 30), 92), (date(2025, 10, 26), 100)])
def test_doba_na_siatce_kwadransow(api, dzien, n):
    w = zrodla_pse.pobierz_dzien(None, dzien)
    assert len(w) == n
    assert [r[1] for r in w] == list(range(1, n + 1))
    # pierwszy kwadrans zaczyna się o lokalnej północy, a jego cena to pierwszy rekord API
    assert w[0][3].endswith("00:00:00") and w[0][3].startswith(dzien.isoformat())
    assert w[0][5] == "100"
    assert w[-1][3].endswith("23:45:00")
    if n == 100:  # jesienna zmiana czasu: dwa razy 02:00 lokalnie, z różną flagą czasu letniego
        dwie = [r for r in w if r[3].endswith("02:00:00")]
        assert len(dwie) == 2 and {r[4] for r in dwie} == {0, 1}


def test_rce_godzinowe_rozpisane_x4(api):
    api["tylko_godzinowe"] = True  # 24 rekordy RCE bez pola period
    w = zrodla_pse.pobierz_dzien(None, date(2025, 9, 10))
    assert len(w) == 96
    ceny = [r[5] for r in w]
    assert ceny[:4] == ["100"] * 4 and ceny[4:8] == ["101"] * 4 and ceny[-1] == "123"


def test_aktualizacja_zapisuje_miesiac(api, tmp_path, monkeypatch):
    monkeypatch.setattr(zrodla_pse, "KATALOG_MARKET", tmp_path)
    st = zrodla_pse.aktualizuj(date(2025, 9, 29), date(2025, 10, 2), odswiez_dni=0)
    assert st["pobrane"] == 4
    assert (tmp_path / "2025-09.csv").exists() and (tmp_path / "2025-10.csv").exists()
    # drugie uruchomienie nic nie pobiera (doby kompletne)
    st2 = zrodla_pse.aktualizuj(date(2025, 9, 29), date(2025, 10, 2), odswiez_dni=0)
    assert st2["pobrane"] == 0 and st2["pominiete"] == 4


def test_wariant_dtime_jako_poczatek(monkeypatch):
    """Gdyby API zmieniło konwencję (dtime = początek), heurystyka z polem period to wykryje."""
    dzien = date(2025, 10, 22)
    rek = _rekordy_api(dzien, lambda i: 100 + i, False)
    for r in rek:  # przesuń dtime_utc o kwadrans wstecz (początek okresu)
        t = datetime.strptime(r["dtime_utc"], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=15)
        r["dtime_utc"] = t.strftime("%Y-%m-%d %H:%M:%S")
    mapa = zrodla_pse.rozpisz_na_kwadranse(rek, dzien, {"rce": "rce_pln"})
    assert mapa[poczatek_doby_utc(dzien)]["rce"] == 100


# ------------------------------------------------------------------ IMGW

def test_parser_synop():
    def wiersz(h, temp, status=""):
        pola = [""] * 107
        pola[0], pola[1], pola[2], pola[3], pola[4], pola[5] = "352160330", "POZNAŃ", "2025", "1", "15", str(h)
        pola[29], pola[30] = temp, status
        return ",".join(pola)
    tekst = "\n".join([wiersz(0, "-3.4"), wiersz(1, '"-3,9"'), wiersz(2, "", "8"), wiersz(3, "99.0"),
                       wiersz(4, "1.0").replace("352160330", "350150500")])
    wynik = zrodla_imgw.parsuj_csv_synop(tekst)
    assert wynik[datetime(2025, 1, 15, 0)] == -3.4
    assert wynik[datetime(2025, 1, 15, 1)] == -3.9
    assert wynik[datetime(2025, 1, 15, 2)] is None   # status 8 = brak pomiaru
    assert wynik[datetime(2025, 1, 15, 3)] is None   # wartość niefizyczna
    assert datetime(2025, 1, 15, 4) not in wynik     # inna stacja


# ------------------------------------------------------------------ profil OSD

def _xlsx_profilu(sciezka: Path, rok: int, dst_jako_25: bool = True):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Profile"
    ws.append(["Załącznik nr 4 – standardowe profile zużycia"])
    ws.append([])
    ws.append(["Data", "Godzina", "G11", "G12", "G12w"])
    d = date(rok, 1, 1)
    while d.year == rok:
        godziny = kwadranse_doby(d) // 4
        for h in range(1, godziny + 1):
            ws.append([datetime(d.year, d.month, d.day), h, 0.0001 * (1 + (h % 24) / 24), 0.0002, 0.0003])
        d += timedelta(days=1)
    wb.save(sciezka)


def test_parser_profilu_format_dlugi(tmp_path):
    p = tmp_path / "profil.xlsx"
    _xlsx_profilu(p, 2025)
    wiersze = zrodla_osd.parsuj_profil(p, 2025)
    dni = {w[0] for w in wiersze}
    assert len(dni) == 365
    # zwykła doba: 96 slotów; wiosenna zmiana czasu: brak slotów 9–12 (02:00–02:59)
    wiosna = [w for w in wiersze if w[0] == "2025-03-30"]
    assert len(wiosna) == 92 and not any(9 <= w[1] <= 12 for w in wiosna)
    jesien = [w for w in wiersze if w[0] == "2025-10-26"]
    assert len(jesien) == 96  # 25 godzin -> godzina 2:00 zsumowana w tych samych slotach zegarowych
    suma_jesien = sum(float(w[2]) for w in jesien)
    suma_zwykla = sum(float(w[2]) for w in wiersze if w[0] == "2025-10-27")
    assert suma_jesien > suma_zwykla  # doba 25-godzinna ma więcej energii


def test_profil_przyblizony_dla_brakujacego_roku():
    wzor = [[f"2025-{m:02d}-{d:02d}", s, "0.001", "Enea 2025"] for m in range(1, 13) for d in range(1, 29)
            for s in range(1, 97)]
    out = zrodla_osd.przyblizony_profil(2027, wzor, 2025)
    assert len({w[0] for w in out}) == 365 and all(w[3].startswith("przybliżenie") for w in out)


# ------------------------------------------------------------------ odporność aktualizacji

def test_luka_w_starszych_danych_jest_ponawiana(api, tmp_path, monkeypatch):
    from wspolne import czytaj_csv
    monkeypatch.setattr(zrodla_pse, "KATALOG_MARKET", tmp_path)
    monkeypatch.setattr(zrodla_pse, "POCZATEK_API_V2", date(2025, 9, 1))
    brak = {date(2025, 9, 15)}
    oryginal = zrodla_pse.pobierz_raport

    def z_luka(sesja, raport, select, dzien):
        return [] if dzien in brak else oryginal(sesja, raport, select, dzien)

    monkeypatch.setattr(zrodla_pse, "pobierz_raport", z_luka)
    zrodla_pse.aktualizuj(None, date(2025, 9, 30), odswiez_dni=2)
    dni = {w["data"] for w in czytaj_csv(tmp_path / "2025-09.csv")}
    assert "2025-09-15" not in dni and len(dni) == 29
    brak.clear()  # PSE uzupełniło dane
    st = zrodla_pse.aktualizuj(None, date(2025, 10, 1), odswiez_dni=2)
    assert st["luki"] == 1
    assert "2025-09-15" in {w["data"] for w in czytaj_csv(tmp_path / "2025-09.csv")}


def test_imgw_odswiezanie_wg_daty_sprawdzenia(tmp_path, monkeypatch):
    import wspolne
    monkeypatch.setattr(wspolne, "PLIK_STANU", tmp_path / "aktualizacja.csv")
    monkeypatch.setattr(zrodla_imgw, "KATALOG_POGODA", tmp_path)
    monkeypatch.setattr(zrodla_imgw, "dzis_pl", lambda: date(2026, 10, 21))
    monkeypatch.setattr(zrodla_imgw, "nowa_sesja", lambda: None)
    monkeypatch.setattr(zrodla_imgw, "_sprawdz_format", lambda sesja: None)
    monkeypatch.setattr(zrodla_imgw, "_lista",
                        lambda sesja, url: ["2026/"] if url == zrodla_imgw.BAZA else ["2026_09_s.zip"])
    pobrane = []

    def zip_(sesja, url):
        pobrane.append(url)
        return {datetime(2026, 9, 1, h): 10.0 for h in range(24)}

    monkeypatch.setattr(zrodla_imgw, "_pobierz_zip", zip_)
    zrodla_imgw.aktualizuj([2026])
    assert len(pobrane) == 1 and (tmp_path / "poznan_2026.csv").exists()
    assert wspolne.czytaj_stan()["imgw_sprawdzono"] == "2026-10-21"
    zrodla_imgw.aktualizuj([2026])  # sprawdzone dziś – czas modyfikacji pliku nie ma znaczenia
    assert len(pobrane) == 1
    monkeypatch.setattr(zrodla_imgw, "dzis_pl", lambda: date(2026, 10, 29))
    zrodla_imgw.aktualizuj([2026])  # minął tydzień – ponowne sprawdzenie archiwum
    assert len(pobrane) == 2


def test_imgw_przesuniety_uklad_kolumn_zatrzymuje(monkeypatch):
    import io
    import zipfile

    def wiersz(h):
        pola = [""] * 107
        pola[0], pola[2], pola[3], pola[4], pola[5] = "352160330", "2025", "1", "15", str(h % 24)
        pola[29], pola[30] = "", "-3.4"  # temperatura przesunięta do pola statusu
        return ",".join(pola)

    bufor = io.BytesIO()
    with zipfile.ZipFile(bufor, "w") as z:
        z.writestr("s_t_01_2025.csv", "\n".join(wiersz(h) for h in range(200)).encode("cp1250"))

    class Odp:
        content = bufor.getvalue()

    monkeypatch.setattr(zrodla_imgw, "pobierz", lambda sesja, url, timeout=60: Odp())
    with pytest.raises(Exception, match="układ kolumn"):
        zrodla_imgw._pobierz_zip(None, "https://example.invalid/2025_01_s.zip")


def test_blad_zrodla_nie_blokuje_zapisu(tmp_path, monkeypatch):
    import pobierz_dane
    import wspolne
    for nazwa, sciezka in (("KATALOG_DANYCH", tmp_path), ("KATALOG_MARKET", tmp_path / "market"),
                           ("KATALOG_POGODA", tmp_path / "pogoda"), ("KATALOG_PROFIL", tmp_path / "profil")):
        sciezka.mkdir(exist_ok=True)
        monkeypatch.setattr(pobierz_dane, nazwa, sciezka)
    monkeypatch.setattr(wspolne, "PLIK_STANU", tmp_path / "aktualizacja.csv")
    (tmp_path / "pogoda" / "poznan_2025.csv").write_text("czas_utc,temp\n2025-01-01 00:00:00,1.5\n", encoding="utf-8")
    (tmp_path / "pogoda" / ".tmp_x.csv").write_text("czas_utc,temp\n2025-01-01 00:00:00,9\n", encoding="utf-8")

    def awaria(*a, **k):
        raise RuntimeError("PSE nie odpowiada")

    monkeypatch.setattr(pobierz_dane.zrodla_pse, "aktualizuj", awaria)
    monkeypatch.setattr(pobierz_dane.zrodla_osd, "aktualizuj", lambda *a, **k: {"lata": []})
    monkeypatch.setattr(pobierz_dane.zrodla_imgw, "aktualizuj", lambda *a, **k: {"lata": []})
    assert pobierz_dane.main([]) == 1
    manifest = (tmp_path / "manifest.csv").read_text(encoding="utf-8")
    assert "pogoda/poznan_2025.csv" in manifest and ".tmp_" not in manifest
    assert "ostatnia_aktualizacja_utc" in wspolne.czytaj_stan()
