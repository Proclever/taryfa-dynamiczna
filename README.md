# Czy taryfa dynamiczna opłaca się Twojemu domowi?

Raport Power BI, który na publicznych danych PSE liczy roczny koszt energii przeciętnego domu w trzech taryfach – **G11, G12 i dynamicznej** (cena RCE zmieniająca się co 15 minut + marża sprzedawcy) – i pozwala policzyć własny wariant: zużycie, ceny z faktury, marżę, auto elektryczne, pompę ciepła i fotowoltaikę.

Zestaw: darmowa licencja Power BI · GitHub (dane + automatyczna aktualizacja) · Publish to web.

## Co jest w repozytorium

```
├── TaryfaDynamiczna.pbip              ← otwórz w Power BI Desktop
├── TaryfaDynamiczna.SemanticModel/    ← model (TMDL): Power Query, tabele, relacje, miary DAX
├── TaryfaDynamiczna.Report/           ← raport (PBIR): 7 stron, motyw
├── scripts/
│   ├── pobierz_dane.py                ← pobieranie i aktualizacja danych (PSE, IMGW, profil Enea)
│   ├── zrodla_pse.py · zrodla_imgw.py · zrodla_osd.py · kalendarz.py · wspolne.py
│   └── model_referencyjny.py          ← te same obliczenia w pandas – do kontroli liczb w raporcie
├── tests/                             ← testy na danych syntetycznych (bez sieci)
├── data/                              ← CSV tworzone przez skrypt (Power Query czyta je z GitHuba)
└── .github/workflows/aktualizacja-danych.yml   ← codzienna aktualizacja danych
```

## Przepływ danych

```
PSE API v2 (rce-pln, kse-load, his-wlk-cal) ─┐
IMGW-PIB – temperatura godzinowa (Poznań)  ──┼─► scripts/pobierz_dane.py ─► data/*.csv ─► GitHub (raw)
Enea Operator – profil G11 (IRiESD)        ──┘        ▲ GitHub Actions codziennie        │
                                                                                          ▼
                     Publish to web ◄── raport (7 stron) ◄── model gwiazdy ◄── Power Query (DaneSurowe)
```

Power Query sięga do źródła w jednym zapytaniu (`DaneSurowe`, stały adres `https://raw.githubusercontent.com` + `RelativePath`), więc odświeżanie w usłudze Power BI działa bez bramy i bez błędów zapory prywatności.

## Uruchomienie krok po kroku

### 1. Pobierz dane (lokalnie, Python 3.10+)

```powershell
py -m pip install -r requirements.txt
py scripts\pobierz_dane.py
```

Pierwsze uruchomienie pobiera wszystko od 14.06.2024 (start API v2): ok. 860 dób × 3 raporty PSE, 11 lat temperatury IMGW i profile Enea – zwykle 15–25 minut. Kolejne uruchomienia są przyrostowe: nowe doby, 3 ostatnie oraz starsze doby brakujące lub niekompletne (ponawiane automatycznie). Skrypt kończy się podsumowaniem: liczba dób, niepełne doby, kwadranse z RCE < 0 i kształt profilu G11 (godzina szczytu – dla G11 powinna wypaść wieczorem).

Źródła są od siebie niezależne: jeśli jedno zawiedzie (np. Enea zmieni adres pliku profilu), pozostałe dane i tak zostaną zapisane, a skrypt zakończy się kodem 1 z opisem błędu. Przydatne opcje:

| Polecenie | Działanie |
|---|---|
| `py scripts\pobierz_dane.py --tylko pse` | tylko wybrane źródła (`pse`, `profil`, `imgw`) |
| `py scripts\pobierz_dane.py --od 2025-03-01 --wymus pse` | PSE od nowa od wskazanej doby |
| `py scripts\pobierz_dane.py --wymus profil` | ponowne pobranie i odczyt profili Enea |

Profil Enea jest odczytywany heurystycznie (układ pliku XLSX nie jest nigdzie opisany): parser rozpoznaje m.in. układ długi i szeroki, numery godzin, etykiety „2A”, etykiety końca okresu i arkusz na miesiąc, a każdą dobę sprawdza (liczba okresów, energia w ≥ 20 godzinach). Gdy nie rozpozna pliku, zatrzymuje się z diagnozą arkuszy zamiast zapisać błędne dane.

Kontrola liczb (opcjonalnie): `py scripts\model_referencyjny.py` wypisze koszty, oszczędność, marżę graniczną itd. dla domyślnego domu – te same wartości powinny pokazać karty na stronie „Werdykt”.

### 2. Repozytorium na GitHubie

1. Utwórz **publiczne** repozytorium (np. `taryfa-dynamiczna`) i wypchnij do niego cały folder razem z `data/`.
2. *Settings → Actions → General → Workflow permissions*: **Read and write permissions**.
3. Workflow „Aktualizacja danych” ma harmonogram codziennie o 6:17 UTC, ale GitHub uruchamia zaplanowane przebiegi z opóźnieniem – często kilkugodzinnym (w pierwszych dniach start ok. 13:00–14:00 czasu polskiego). Commituje nowe CSV (także wtedy, gdy jedno ze źródeł zawiodło – przebieg jest wtedy oznaczony jako nieudany i GitHub wysyła powiadomienie). Ręcznie: *Actions → Aktualizacja danych → Run workflow* (pole `od` pobiera dane PSE od nowa od wskazanej doby).

### 3. Power BI Desktop

1. Otwórz `TaryfaDynamiczna.pbip`.
2. Parametr `RepoGitHub` jest ustawiony na `Proclever/taryfa-dynamiczna` (gałąź `GalazGitHub` = `main`). Przy forku zmień go w *Narzędzia główne → Przekształć dane → Edytuj parametry*.
3. *Odśwież*. Przy pytaniu o poświadczenia wybierz **Anonimowe** dla `https://raw.githubusercontent.com`, poziom prywatności **Publiczny**.
4. Zapisz (projekt zostaje w formacie PBIP – zmiany widać w Git jako tekst).

Wymagany aktualny Power BI Desktop (raport w formacie PBIR, model w TMDL; schematy z listopada 2025). W starszej wersji włącz w *Opcje → Funkcje w wersji zapoznawczej*: „Power BI Project (.pbip)”, „Store semantic model using TMDL format” i „Store reports using enhanced metadata format (PBIR)”.

### 4. Publikacja i Publish to web

1. *Opublikuj → Mój obszar roboczy*.
2. W usłudze: *Ustawienia modelu semantycznego → Poświadczenia źródła danych → Web*: Anonimowe, Publiczny, zaznacz **Pomiń test połączenia**.
3. *Zaplanowane odświeżanie*: codziennie, strefa czasowa Warszawa, np. 16:00 – po przebiegu GitHub Actions, który bywa opóźniony o kilka godzin. Włącz powiadomienia o nieudanym odświeżeniu.
4. W raporcie: *Plik → Osadź raport → Publikuj w Internecie (publiczne)*. Wymaga włączonego przez administratora ustawienia dzierżawy **Publish to web**; dane są wtedy dostępne dla każdego (tu: wyłącznie dane publiczne).

## Model

| Tabela | Opis |
|---|---|
| `FactRynek` | Kwadrans: Timestamp, Data, Slot, RCE [zł/MWh], zapotrzebowanie KSE, PV, wiatr, temperatura, współczynniki `WspProfil`, `WspEV_Wieczor/Noc/Smart`, `WspPC`, `WspPV`, `RangaCenyWDobie`, `CenaUjemna`, `Granulacja` |
| `DimData` | Kalendarz: typ dnia (roboczy / sobota / niedziela lub święto – święta liczone w Power Query, z Wielkanocą), RokMiesiąc, sezon grzewczy, okna analizy (`Okno12M`), średnia temperatura doby |
| `DimSlot` | 96 kwadransów: godzina, etykiety, strefy G12/G12w, część doby |
| `Param*` | Tabele what-if (rozłączne): zużycie, ceny G11/G12, marża (zakres z zerem), EV, tryb EV, pompa ciepła, PV, elastyczność |
| `Taryfy`, `OśMarży` | Osie wykresów: porównanie taryf i krzywa czułości koszt vs marża |
| `Miary` | Ok. 100 miar w folderach (koszty, porównanie taryf, rynek, ryzyko, EV/PC, prosument, teksty) |

Relacje: `FactRynek[Data] → DimData[Data]`, `FactRynek[Slot] → DimSlot[Slot]` oraz nieaktywna `FactRynek[SlotZimowy] → DimSlot[Slot]` – po niej liczone są strefy G12 (liczniki Enei chodzą wg czasu zimowego, więc latem strefy przesuwają się o godzinę; przełącznik `LicznikWgCzasuZimowego`).

Kluczowe miary: `kWh łącznie`, `Koszt – dynamiczna`, `Koszt – G11`, `Koszt – G12`, `Oszczędność vs G11`, `Marża graniczna (zł/MWh)`, `Kara za profil (zł/kWh)`, `Koszt – dynamiczna z przesunięciem`, `Najdroższy miesiąc (zł)`, `Udział kwadransów z ceną ≤ 0`, `P95 ceny (zł/kWh)`, `Werdykt`. Parametry są pobierane do zmiennych przed `SUMX`, a współczynniki są kolumnami – suwaki reagują od razu także w Publish to web.

## Strony raportu

1. **Werdykt** – koszt roczny w G11, G12 i taryfie dynamicznej, oszczędność w zł i %, marża graniczna, trzy wnioski z miar.
2. **Twój dom** – wszystkie suwaki, słupki trzech taryf, krzywa „koszt vs marża” z punktem opłacalności, kafel elastyczności, koszt miesięczny.
3. **Kiedy prąd jest tani** – mapa ciepła godzina × miesiąc, doba robocza vs weekend, udział kwadransów z ceną ≤ 0, RCE vs udział PV+wiatru.
4. **Ryzyko** – percentyle cen w miesiącach, zużycie vs cena w ciągu doby, 10 najdroższych dób, najdroższy miesiąc obok G11, rok do roku.
5. **EV i pompa ciepła** – 3 tryby ładowania × 3 taryfy, sezon grzewczy, temperatura doby vs RCE.
6. **Prosument** – autokonsumpcja vs eksport, depozyt po RCE × 1,23 z podłogą 0, rachunek w trzech taryfach.
7. **O danych** – źródła, metoda, uproszczenia, świeżość i jakość danych.

## Najważniejsze założenia

- Liczona jest tylko energia czynna: bez dystrybucji, VAT, akcyzy, opłaty handlowej i mocowej.
- RCE (PSE) to publiczny zamiennik indeksu TGE, do którego indeksowane są oferty dynamiczne.
- Porównania mają sens wyłącznie dla 12 pełnych miesięcy – domyślnie „Ostatnie 12 mies.” (fragmentator synchronizowany na stronach 1–6).
- Do 30.09.2025 RCE było godzinowe (kwadranse z tą samą ceną), od 1.10.2025 jest 15-minutowe; doby zmiany czasu mają 92 lub 100 kwadransów.
- Domyślne ceny: taryfa Enea 2026 (G11 0,498 zł/kWh; G12 0,578 / 0,337 zł/kWh netto – w cenniku 0,5779 / 0,3369, suwaki mają krok 0,001 zł/kWh); marża 87 zł/MWh (cennik Enea „Ceny Dynamiczne”, III kw. 2025). Wpisz własne z faktury.

## Dostosowanie

- **Inny OSD / strefy G12**: parametry `G12NocOd`, `G12NocDo`, `G12DzienOd`, `G12DzienDo`, `LicznikWgCzasuZimowego` (Power Query) i adresy profili w `scripts/zrodla_osd.py`.
- **Inna stacja IMGW**: `STACJA_KOD` i `STACJA_ID` w `scripts/zrodla_imgw.py`.
- **Profil na kolejny rok**: dopisz adres pliku w `PROFILE_URL` (`scripts/zrodla_osd.py`); do tego czasu skrypt tworzy przybliżenie z najbliższego roku (oznaczone w kolumnie `zrodlo`).

## Testy

```powershell
py -m pytest -q
```

Testy działają na danych syntetycznych (`tests/syntetyczne.py`) – nie są to dane PSE/IMGW/OSD i nie trafiają do raportu.

## Źródła i atrybucja

- Polskie Sieci Elektroenergetyczne S.A. – [raporty PSE, API v2](https://api.raporty.pse.pl).
- Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy; dane IMGW-PIB zostały przetworzone ([danepubliczne.imgw.pl](https://danepubliczne.imgw.pl)).
- Enea Operator sp. z o.o. – [standardowe profile zużycia (IRiESD)](https://www.operator.enea.pl/uslugi-dystrybucyjne/iriesd) i taryfa OSD.

To model przeciętnego domu na publicznym proxy ceny – nie oferta ani porada.
