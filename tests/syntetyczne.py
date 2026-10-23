"""Generator SYNTETYCZNYCH danych w formacie pipeline'u – tylko do testów (nie są to dane PSE/IMGW/OSD)."""
from __future__ import annotations

import csv
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from kalendarz import typ_dnia  # noqa: E402
from wspolne import TZ_PL, UTC, kwadranse_doby, poczatek_doby_utc  # noqa: E402


def generuj(katalog: Path, od: date = date(2024, 6, 14), do: date = date(2026, 10, 20), ziarno: int = 7) -> None:
    rnd = random.Random(ziarno)
    (katalog / "market").mkdir(parents=True, exist_ok=True)
    (katalog / "pogoda").mkdir(parents=True, exist_ok=True)
    (katalog / "profil").mkdir(parents=True, exist_ok=True)
    manifest = []
    # --- rynek
    miesiace: dict[str, list] = {}
    d = od
    while d <= do:
        start = poczatek_doby_utc(d)
        n = kwadranse_doby(d)
        sezon = math.cos(2 * math.pi * (d.timetuple().tm_yday - 15) / 365)  # 1 zimą, -1 latem
        for k in range(n):
            q = start + timedelta(minutes=15 * k)
            lok = q.astimezone(TZ_PL)
            h = lok.hour + lok.minute / 60
            # cena: szczyt wieczorny, dolina w południe (PV), wyżej zimą; godzinowa do 30.09.2025
            hh = lok.hour if d < date(2025, 10, 1) else h
            pv_ksztalt = max(0.0, math.sin(math.pi * (hh - 5) / 15)) if 5 <= hh <= 20 else 0.0
            cena = 450 + 120 * sezon + 180 * math.exp(-((hh - 19) ** 2) / 4) - 260 * pv_ksztalt * (1 - 0.5 * sezon)
            if d.weekday() >= 5:
                cena -= 60
            rng = random.Random(f"{d}-{lok.hour if d < date(2025, 10, 1) else k}")
            cena += rng.gauss(0, 40)
            pv = 12000 * pv_ksztalt * (0.6 - 0.4 * sezon) * (1 + 0.15 * (d - od).days / 365)
            wiatr = 3000 + 2500 * math.sin(d.toordinal() / 3.0) + rnd.gauss(0, 300)
            zap = 17000 + 3000 * math.exp(-((h - 19) ** 2) / 8) + 2000 * sezon
            wiersz = [d.isoformat(), k + 1, q.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                      lok.strftime("%Y-%m-%d %H:%M:%S"), 1 if lok.dst() else 0, f"{cena:.2f}", f"{zap:.1f}",
                      f"{pv:.1f}", f"{max(0, wiatr):.1f}"]
            miesiace.setdefault(f"{d.year:04d}-{d.month:02d}", []).append(wiersz)
        d += timedelta(days=1)
    for mies, wiersze in sorted(miesiace.items()):
        p = katalog / "market" / f"{mies}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["data", "kwadrans", "od_utc", "od_lokalnie", "czas_letni", "rce", "zapotrzebowanie", "pv", "wiatr"])
            w.writerows(wiersze)
        manifest.append(["market", f"market/{mies}.csv", len(wiersze), wiersze[0][0], wiersze[-1][0]])
    # --- pogoda (UTC), lata 2016..rok końca (bieżący rok niepełny)
    for rok in range(2016, do.year + 1):
        t = datetime(rok, 1, 1)
        wiersze = []
        while t.year == rok and t.date() <= do:
            dzien = t.timetuple().tm_yday
            temp = 9 - 11 * math.cos(2 * math.pi * (dzien - 15) / 365) + 4 * math.sin(math.pi * (t.hour - 8) / 12) \
                + random.Random(f"{t}").gauss(0, 2)
            wiersze.append([t.strftime("%Y-%m-%d %H:%M:%S"), "" if random.Random(f"x{t}").random() < 0.01 else f"{temp:.1f}"])
            t += timedelta(hours=1)
        p = katalog / "pogoda" / f"poznan_{rok}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["czas_utc", "temp"])
            w.writerows(wiersze)
        manifest.append(["pogoda", f"pogoda/poznan_{rok}.csv", len(wiersze), wiersze[0][0][:10], wiersze[-1][0][:10]])
    # --- profil G11 (pełne lata kalendarzowe)
    for rok in range(od.year, do.year + 1):
        wiersze = []
        d = date(rok, 1, 1)
        while d.year == rok:
            start = poczatek_doby_utc(d)
            for k in range(kwadranse_doby(d)):
                lok = (start + timedelta(minutes=15 * k)).astimezone(TZ_PL)
                h = lok.hour + lok.minute / 60
                v = 0.4 + 0.5 * math.exp(-((h - 7.5) ** 2) / 2) + 1.0 * math.exp(-((h - 19.5) ** 2) / 4)
                if typ_dnia(d) != "roboczy":
                    v *= 1.1
                slot = lok.hour * 4 + lok.minute // 15 + 1
                wiersze.append([d.isoformat(), slot, f"{v:.6f}", f"Enea {rok} (syntetyczny test)"])
            d += timedelta(days=1)
        p = katalog / "profil" / f"enea_g11_{rok}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["data", "slot", "wartosc", "zrodlo"])
            w.writerows(wiersze)
        manifest.append(["profil", f"profil/enea_g11_{rok}.csv", len(wiersze), wiersze[0][0], wiersze[-1][0]])
    with (katalog / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["typ", "plik", "wiersze", "od", "do"])
        w.writerows(manifest)
    with (katalog / "aktualizacja.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["klucz", "wartosc"])
        w.writerow(["ostatnia_aktualizacja_utc", "2026-10-21 06:20:00"])


if __name__ == "__main__":
    generuj(Path(sys.argv[1]))
