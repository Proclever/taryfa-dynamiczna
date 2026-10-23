"""Niezmienniki modelu na danych syntetycznych: współczynniki sumują się do 1, miary zgadzają się ze sobą."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pd = pytest.importorskip("pandas")
import model_referencyjny as mr  # noqa: E402
from syntetyczne import generuj  # noqa: E402


@pytest.fixture(scope="module")
def dane(tmp_path_factory):
    kat = tmp_path_factory.mktemp("dane")
    generuj(kat)
    d = mr.wczytaj(kat)
    p = mr.Parametry()
    return d, p, mr.fact(d, p)


def test_okna(dane):
    d, _, _ = dane
    o = mr.okna(d["manifest"])
    assert o["o_od"] == date(2025, 10, 1) and o["o_do"] == date(2026, 9, 30)
    assert o["p_od"] == date(2024, 10, 1) and o["p_do"] == date(2025, 9, 30)


def test_wspolczynniki_sumuja_sie_do_jedynki(dane):
    _, _, f = dane
    rok = f[[x.year == 2025 for x in f["Data"]]]
    assert rok["WspEV_Wieczor"].sum() == pytest.approx(1, abs=1e-9)
    assert rok["WspEV_Noc"].sum() == pytest.approx(1, abs=1e-9)
    assert rok["WspEV_Smart"].sum() == pytest.approx(1, abs=1e-9)
    # profil: suma 1 w każdym pełnym oknie 12-miesięcznym (normalizacja w oknie, jak w Power Query);
    # rok kalendarzowy łączy dwa okna, więc tylko ~1
    assert rok["WspProfil"].sum() == pytest.approx(1, abs=1e-2)
    for nazwa in ("Ostatnie 12 mies.", "Poprzednie 12 mies."):
        assert f[f["Okno"] == nazwa]["WspProfil"].sum() == pytest.approx(1, abs=1e-9)
    okno = f[f["Okno"] == "Ostatnie 12 mies."]
    assert okno["WspEV_Smart"].sum() == pytest.approx(1, abs=1e-9)
    assert okno["WspPV"].sum() == pytest.approx(1000, rel=1e-9)
    assert 0.7 < okno["WspPC"].sum() < 1.3


def test_ranga_i_kwadranse_dob(dane):
    _, _, f = dane
    jesien = f[f["Data"] == date(2025, 10, 26)]
    wiosna = f[f["Data"] == date(2026, 3, 29)]
    assert len(jesien) == 100 and len(wiosna) == 92
    assert sorted(jesien["RangaCenyWDobie"].astype(int)) == list(range(1, 101))
    assert (jesien["WspEV_Smart"] > 0).sum() == 16


def test_miary_spojne(dane):
    _, p, f = dane
    w = mr.miary(f, p)
    assert w["Liczba miesięcy"] == 12
    assert w["kWh łącznie"] == pytest.approx(p.zuzycie, rel=3e-3)
    # przy marży granicznej koszt dynamiczny = G11
    p2 = mr.Parametry(marza=w["Marża graniczna (zł/MWh)"])
    w2 = mr.miary(f, p2)
    assert w2["Koszt – dynamiczna"] == pytest.approx(w2["Koszt – G11"], rel=1e-9)
    # elastyczność 0 -> brak zysku z przesunięcia
    w0 = mr.miary(f, mr.Parametry(elastycznosc=0))
    assert w0["Zysk z przesunięcia"] == pytest.approx(0, abs=1e-6)
    assert w["Zysk z przesunięcia"] > 0
    # syntetyczny profil ma szczyt wieczorny przy drogim wieczorze -> kara za profil dodatnia
    assert w["Kara za profil (zł/kWh)"] > 0


def test_ev_smart_tansze_niz_wieczor(dane):
    _, _, f = dane
    wieczor = mr.miary(f, mr.Parametry(zuzycie=0, ev=2000, tryb_ev="Wieczór"))
    smart = mr.miary(f, mr.Parametry(zuzycie=0, ev=2000, tryb_ev="Smart"))
    assert smart["Koszt – dynamiczna"] < wieczor["Koszt – dynamiczna"]
    assert smart["kWh łącznie"] == pytest.approx(2000, rel=1e-9)
