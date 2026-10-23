"""Model referencyjny: te same obliczenia co Power Query (FactRynek, Wsp*) i kluczowe miary DAX – w pandas.

Służy do kontroli raportu: uruchom na tych samych danych i porównaj liczby z kartami w Power BI.
    python scripts/model_referencyjny.py                       # domyślny dom, ostatnie 12 pełnych miesięcy
    python scripts/model_referencyjny.py --zuzycie 4000 --ev 2000 --tryb Smart --pc 4000 --pv 5
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalendarz import swieta  # noqa: E402
from wspolne import konsola_utf8  # noqa: E402

KATALOG = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Parametry:
    zuzycie: float = 3000          # kWh/rok
    cena_g11: float = 0.498        # zł/kWh
    cena_g12_dzien: float = 0.5779
    cena_g12_noc: float = 0.3369
    marza: float = 87              # zł/MWh
    ev: float = 0                  # kWh/rok
    tryb_ev: str = "Wieczór"       # Wieczór / Noc / Smart
    pc: float = 0                  # kWh/rok
    pv: float = 0                  # kWp
    elastycznosc: float = 0.10
    g12_noc: tuple = (22, 6)
    g12_dzien: tuple = (13, 15)
    licznik_czas_zimowy: bool = True
    prog_pc: float = 15.0


# ---------------------------------------------------------------- wczytanie (jak Manifest/RynekSurowy/...)

def wczytaj(katalog: Path = KATALOG) -> dict[str, pd.DataFrame]:
    man = pd.read_csv(katalog / "manifest.csv", dtype=str)
    def razem(typ):
        pliki = man.loc[man["typ"] == typ, "plik"]
        return pd.concat([pd.read_csv(katalog / p, dtype=str, keep_default_na=False) for p in pliki], ignore_index=True)
    rynek = razem("market")
    for k in ("rce", "zapotrzebowanie", "pv", "wiatr"):
        rynek[k] = pd.to_numeric(rynek[k].replace("", None), errors="coerce")
    rynek["data"] = pd.to_datetime(rynek["data"]).dt.date
    rynek["od_utc"] = pd.to_datetime(rynek["od_utc"])
    rynek["od_lokalnie"] = pd.to_datetime(rynek["od_lokalnie"])
    rynek["czas_letni"] = rynek["czas_letni"].astype(int)
    rynek = rynek.drop_duplicates("od_utc")
    pogoda = razem("pogoda")
    pogoda["czas_utc"] = pd.to_datetime(pogoda["czas_utc"])
    pogoda["temp"] = pd.to_numeric(pogoda["temp"].replace("", None), errors="coerce")
    pogoda = pogoda.drop_duplicates("czas_utc")
    profil = razem("profil")
    profil["data"] = pd.to_datetime(profil["data"]).dt.date
    profil["slot"] = profil["slot"].astype(int)
    profil["wartosc"] = pd.to_numeric(profil["wartosc"])
    return {"manifest": man, "rynek": rynek, "pogoda": pogoda, "profil": profil}


def okna(man: pd.DataFrame) -> dict:
    r = man[man["typ"] == "market"]
    pierwsza = min(date.fromisoformat(x) for x in r["od"])
    ostatnia = max(date.fromisoformat(x) for x in r["do"])
    nast = ostatnia + timedelta(days=1)
    koniec = ostatnia if nast.month != ostatnia.month else (date(ostatnia.year, ostatnia.month, 1) - timedelta(days=1))
    def start_mies(d, cofnij):
        m = d.year * 12 + d.month - 1 - cofnij
        return date(m // 12, m % 12 + 1, 1)
    o_od = start_mies(koniec, 11)
    return {"pierwsza": pierwsza, "ostatnia": ostatnia, "o_od": o_od, "o_do": koniec,
            "p_od": start_mies(koniec, 23), "p_do": o_od - timedelta(days=1)}


def okno_dla(d: date, o: dict) -> str:
    if o["o_od"] <= d <= o["o_do"]:
        return "Ostatnie 12 mies."
    if o["p_od"] <= d <= o["p_do"]:
        return "Poprzednie 12 mies."
    if d > o["o_do"]:
        return "Bieżący miesiąc"
    return "Wcześniej"


def _w_przedziale(g, od, do):
    return (g >= od) & (g < do) if od <= do else (g >= od) | (g < do)


# ---------------------------------------------------------------- FactRynek (odpowiednik zapytania Power Query)

def fact(dane: dict, p: Parametry) -> pd.DataFrame:
    f = dane["rynek"].rename(columns={"data": "Data", "kwadrans": "Kwadrans", "od_utc": "OdUTC", "od_lokalnie": "Timestamp",
                                      "czas_letni": "CzasLetni", "rce": "RCE", "zapotrzebowanie": "Zapotrzebowanie",
                                      "pv": "PV", "wiatr": "Wiatr"}).copy()
    f["Godzina"] = f["Timestamp"].dt.hour
    f["Slot"] = f["Godzina"] * 4 + f["Timestamp"].dt.minute // 15 + 1
    if p.licznik_czas_zimowy:
        t = f["OdUTC"] + pd.Timedelta(hours=1)
        f["SlotZimowy"] = t.dt.hour * 4 + t.dt.minute // 15 + 1
    else:
        f["SlotZimowy"] = f["Slot"]
    f["GodzinaUTC"] = f["OdUTC"].dt.floor("h")
    # granulacja: kwadrans bez ceny dostaje cenę swojej godziny
    pierwsza = f.groupby("GodzinaUTC")["RCE"].transform(lambda s: s.dropna().iloc[0] if s.notna().any() else math.nan)
    f["RCE"] = f["RCE"].fillna(pierwsza)
    f["Granulacja"] = ["1 h" if d < date(2025, 10, 1) else "15 min" for d in f["Data"]]
    # doba
    g = f.groupby("Data")
    f["KwadransowWDobie"] = g["Slot"].transform("size")
    f["KwadransowWieczor"] = g["Godzina"].transform(lambda s: ((s >= 18) & (s < 22)).sum())
    f["KwadransowNoc"] = g["Godzina"].transform(lambda s: ((s >= 23) | (s < 6)).sum())
    f["KwadransowZCena"] = g["RCE"].transform(lambda s: s.notna().sum())
    f["BrakCeny"] = f["RCE"].isna().astype(int)
    f = f.sort_values(["Data", "BrakCeny", "RCE", "OdUTC"])
    f["RangaCenyWDobie"] = f.groupby("Data").cumcount() + 1
    f.loc[f["RCE"].isna(), "RangaCenyWDobie"] = pd.NA
    f = f.sort_values("OdUTC").reset_index(drop=True)
    # EV
    f["DniWRoku"] = [366 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 365 for d in f["Data"]]
    wiecz = (f["Godzina"] >= 18) & (f["Godzina"] < 22) & (f["KwadransowWieczor"] > 0)
    noc = ((f["Godzina"] >= 23) | (f["Godzina"] < 6)) & (f["KwadransowNoc"] > 0)
    k = f["KwadransowZCena"].clip(upper=16)
    smart = f["RangaCenyWDobie"].notna() & (f["RangaCenyWDobie"].fillna(99) <= 16) & (k > 0)
    f["WspEV_Wieczor"] = (1 / (f["DniWRoku"] * f["KwadransowWieczor"])).where(wiecz, 0.0)
    f["WspEV_Noc"] = (1 / (f["DniWRoku"] * f["KwadransowNoc"])).where(noc, 0.0)
    f["WspEV_Smart"] = (1 / (f["DniWRoku"] * k)).where(smart, 0.0)
    # profil OSD
    pr = dane["profil"].groupby(["data", "slot"], as_index=False)["wartosc"].sum()  # jak Table.Group w ProfilOSD
    pr["Rok"] = [d.year for d in pr["data"]]
    pr["UdzialProfilu"] = pr["wartosc"] / pr.groupby("Rok")["wartosc"].transform("sum")
    f = f.merge(pr[["data", "slot", "UdzialProfilu"]].rename(columns={"data": "Data", "slot": "Slot"}),
                on=["Data", "Slot"], how="left")
    f["KwadransowWSlocie"] = ((f["KwadransowWDobie"] == 100) & (f["Godzina"] == 2)).map({True: 2, False: 1})
    f["WspProfil"] = (f["UdzialProfilu"] / f["KwadransowWSlocie"]).fillna(0.0)
    # temperatura i pompa ciepła
    po = dane["pogoda"].rename(columns={"czas_utc": "GodzinaUTC", "temp": "TemperaturaPomiar"})
    pom = po.dropna(subset=["TemperaturaPomiar"])
    klim = pom.groupby([pom["GodzinaUTC"].dt.month, pom["GodzinaUTC"].dt.hour])["TemperaturaPomiar"].mean()
    f = f.merge(po, on="GodzinaUTC", how="left")
    norma_t = pd.Series([klim.get((m, h)) for m, h in zip(f["GodzinaUTC"].dt.month, f["GodzinaUTC"].dt.hour)], index=f.index)
    f["Temperatura"] = f["TemperaturaPomiar"].fillna(norma_t)
    f["TemperaturaUzupełniona"] = f["TemperaturaPomiar"].isna()
    # norma stopniogodzin (pełne lata IMGW)
    po2 = po.copy()
    norma_po = pd.Series([klim.get((m, h)) for m, h in zip(po2["GodzinaUTC"].dt.month, po2["GodzinaUTC"].dt.hour)], index=po2.index)
    po2["T"] = po2["TemperaturaPomiar"].fillna(norma_po)
    po2["DH"] = (p.prog_pc - po2["T"]).clip(lower=0).fillna(0)
    lata = po2.groupby(po2["GodzinaUTC"].dt.year).agg(Godzin=("DH", "size"), Pomiarow=("TemperaturaPomiar", "count"), DH=("DH", "sum"))
    pelne = lata[(lata["Godzin"] >= 8000) & (lata["Pomiarow"] >= 0.95 * lata["Godzin"])]
    norma = pelne["DH"].mean() if len(pelne) else lata["DH"].sum() * 8760 / max(1, lata["Godzin"].sum())
    f["WspPC"] = (0.25 * (p.prog_pc - f["Temperatura"]).clip(lower=0) / norma).fillna(0.0)
    # PV w oknach 12-miesięcznych
    o = okna(dane["manifest"])
    f["Okno"] = [okno_dla(d, o) for d in f["Data"]]
    sumy = f.groupby("Okno")["PV"].sum(min_count=1).to_dict()
    cel = {"Wcześniej": "Poprzednie 12 mies.", "Bieżący miesiąc": "Ostatnie 12 mies."}
    s = f["Okno"].map(lambda w: sumy.get(cel.get(w, w), f["PV"].sum()))
    f["WspPV"] = (1000 * f["PV"] / s).where(f["PV"].notna() & (s > 0), 0.0)
    # profil OSD: suma 1 w pełnym oknie 12-miesięcznym (≥ 365 dób), jak w Power Query
    g = f.groupby("Okno").agg(SumaProfilu=("WspProfil", "sum"), Dob=("Data", "nunique"))
    pelne = g.loc[g["Dob"] >= 365, "SumaProfilu"].to_dict()
    sp = f["Okno"].map(lambda w: pelne.get(cel.get(w, w)))
    f["WspProfil"] = (f["WspProfil"] / sp).where(sp.notna() & (sp > 0), f["WspProfil"])
    f["CenaUjemna"] = f["RCE"] < 0
    # strefa G12 wg slotu zimowego (DimSlot)
    gz = (f["SlotZimowy"] - 1) // 4
    tania = _w_przedziale(gz, *p.g12_noc) | _w_przedziale(gz, *p.g12_dzien)
    f["StrefaG12"] = tania.map({True: "tania", False: "droga"})
    return f


def dim_data(f: pd.DataFrame) -> pd.DataFrame:
    d = f.groupby("Data").agg(Okno=("Okno", "first"), Temperatura=("Temperatura", "mean")).reset_index()
    d["RokMiesiac"] = [f"{x.year}-{x.month:02d}" for x in d["Data"]]
    d["SezonGrzewczy"] = [x.month in (10, 11, 12, 1, 2, 3, 4) for x in d["Data"]]
    d["Swieto"] = [x in swieta(x.year) for x in d["Data"]]
    return d


# ---------------------------------------------------------------- miary (odpowiedniki DAX)

def _kwh(f, p: Parametry) -> pd.Series:
    ev = {"Noc": f["WspEV_Noc"], "Smart": f["WspEV_Smart"]}.get(p.tryb_ev, f["WspEV_Wieczor"])
    return p.zuzycie * f["WspProfil"] + p.ev * ev + p.pc * f["WspPC"]


def miary(f: pd.DataFrame, p: Parametry, okno: str | None = "Ostatnie 12 mies.") -> dict:
    x = f if okno is None else f[f["Okno"] == okno]
    rce = x["RCE"].fillna(0.0)   # DAX: BLANK + marża = marża
    kwh = _kwh(x, p)
    koszt_dyn = (kwh * (rce + p.marza) / 1000).sum()
    koszt_dyn0 = (kwh * rce / 1000).sum()
    kwh_suma = kwh.sum()
    g11 = kwh_suma * p.cena_g11
    tania = x["StrefaG12"] == "tania"
    g12 = kwh[tania].sum() * p.cena_g12_noc + kwh[~tania].sum() * p.cena_g12_dzien
    sr_rce = x["RCE"].mean()
    # przesunięcie: udział e zużycia każdej doby w 16 najtańszych kwadransach
    e = p.elastycznosc
    pom = pd.DataFrame({"Data": x["Data"], "kwh": kwh, "koszt": kwh * (rce + p.marza) / 1000,
                        "rce_tania": x["RCE"].where(x["RangaCenyWDobie"].fillna(99) <= 16)})
    dz = pom.groupby("Data").agg(kwh=("kwh", "sum"), koszt=("koszt", "sum"), rce_tania=("rce_tania", "mean"))
    koszt_przes = ((1 - e) * dz["koszt"] + e * dz["kwh"] * (dz["rce_tania"].fillna(0) + p.marza) / 1000).sum()
    # najdroższy miesiąc
    pom["RokMiesiac"] = [f"{d.year}-{d.month:02d}" for d in x["Data"]]
    mies = pom.groupby("RokMiesiac")["koszt"].sum()
    # prosument
    pv = p.pv * x["WspPV"]
    autok = pd.concat([kwh, pv], axis=1).min(axis=1)
    eksport = (pv - kwh).clip(lower=0)
    depozyt = 1.23 * (eksport * x["RCE"].clip(lower=0).fillna(0) / 1000).sum()
    wynik = {
        "Okres": f"{x['Data'].min()} – {x['Data'].max()}",
        "Liczba miesięcy": int(pom["RokMiesiac"].nunique()),
        "kWh łącznie": kwh_suma,
        "Koszt – G11": g11,
        "Koszt – G12": g12,
        "Koszt – dynamiczna": koszt_dyn,
        "Oszczędność vs G11": g11 - koszt_dyn,
        "Oszczędność %": (g11 - koszt_dyn) / g11 if g11 else math.nan,
        "Marża graniczna (zł/MWh)": (g11 - koszt_dyn0) / kwh_suma * 1000 if kwh_suma else math.nan,
        "Średnia RCE (zł/MWh)": sr_rce,
        "Średnia RCE ważona profilem (zł/MWh)": koszt_dyn0 / kwh_suma * 1000 if kwh_suma else math.nan,
        "Kara za profil (zł/kWh)": (koszt_dyn / kwh_suma - p.marza / 1000 - sr_rce / 1000) if kwh_suma else math.nan,
        "Koszt – dynamiczna z przesunięciem": koszt_przes,
        "Zysk z przesunięcia": koszt_dyn - koszt_przes,
        "Najdroższy miesiąc": mies.idxmax() if len(mies) else None,
        "Najdroższy miesiąc (zł)": mies.max() if len(mies) else math.nan,
        "Udział kwadransów z ceną ≤ 0": ((x["RCE"] <= 0).sum() / x["RCE"].notna().sum()
                                         if x["RCE"].notna().any() else math.nan),
        "P95 RCE (zł/MWh)": x["RCE"].quantile(0.95),
        "PV produkcja (kWh)": pv.sum(),
        "Autokonsumpcja (kWh)": autok.sum(),
        "Wartość depozytu (zł)": depozyt,
    }
    return wynik


def main(argv=None) -> int:
    konsola_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dane", type=Path, default=KATALOG)
    ap.add_argument("--okno", default="Ostatnie 12 mies.", help='"Ostatnie 12 mies.", "Poprzednie 12 mies." albo "wszystko"')
    for nazwa, typ in (("zuzycie", float), ("cena-g11", float), ("cena-g12-dzien", float), ("cena-g12-noc", float),
                       ("marza", float), ("ev", float), ("pc", float), ("pv", float), ("elastycznosc", float)):
        ap.add_argument(f"--{nazwa}", type=typ)
    ap.add_argument("--tryb", choices=["Wieczór", "Noc", "Smart"])
    a = ap.parse_args(argv)
    p = Parametry()
    for nazwa in ("zuzycie", "cena_g11", "cena_g12_dzien", "cena_g12_noc", "marza", "ev", "pc", "pv", "elastycznosc"):
        v = getattr(a, nazwa)
        if v is not None:
            setattr(p, nazwa, v)
    if a.tryb:
        p.tryb_ev = a.tryb
    f = fact(wczytaj(a.dane), p)
    w = miary(f, p, None if a.okno == "wszystko" else a.okno)
    szer = max(len(k) for k in w)
    for k, v in w.items():
        if isinstance(v, float):
            v = f"{v:,.4f}".replace(",", " ") if abs(v) < 10 else f"{v:,.0f}".replace(",", " ")
        print(f"{k:<{szer}}  {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
