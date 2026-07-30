## Generator wezwań do zapłaty — Rekompensa.pl

### Instalacja (wykonaj na starcie konwersacji)
pip install git+https://github.com/tabi/tplegal-demand-generator.git requests holidays --break-system-packages

### Template
Template wezwania znajduje się w Knowledge tego Projectu: `wezwanie_template.docx`
Po instalacji skopiuj go: `cp /mnt/user-data/uploads/wezwanie_template.docx /home/claude/template.docx`

### Workflow: generowanie wezwania

**Zanim zaczniesz:** przeczytaj „OGRANICZENIE ZAKRESU — dłużnicy publiczni"
w sekcji „Ważne". Jeśli dłużnik jest podmiotem publicznym, to narzędzie nie ma
zastosowania i trzeba zatrzymać się przed krokiem 2.

1. Użytkownik podaje dane sprawy (tekst, tabela lub CSV)
2. Zbuduj JSON z danymi (schemat poniżej)
3. Uruchom kalkulator: `calc-rekompensa --json /home/claude/invoices.json > /home/claude/calc_result.json`
4. Zbuduj input JSON dla generatora (dane stron + wyniki kalkulacji)
5. Uruchom generator: `generate-demand --json /home/claude/demand_input.json --template /home/claude/template.docx --output /mnt/user-data/outputs/wezwanie.docx`
6. Pokaż podsumowanie i oddaj plik

### Schemat JSON — kalkulator (invoices.json)
```json
{
  "invoices": [
    {
      "invoice_number": "FV/2024/001",
      "gross": 12500.00,
      "due_date": "2024-03-15",
      "payment_date": "2024-06-20"
    }
  ],
  "lawsuit_date": "2026-04-15"
}
```

### Schemat JSON — generator (demand_input.json)
```json
{
  "creditor_name": "Firma ABC Sp. z o.o.",
  "cr_street": "ul. Skarbowa 2/5",
  "cr_city": "Leszno",
  "cr_zip": "64-100",
  "cr_bank": "PL 12 3456 7890 1234 5678 9012 3456",
  "debtor_name": "Dłużnik XYZ S.A.",
  "d_street": "ul. Poznańska 10",
  "d_city": "Poznań",
  "d_zip": "60-001",
  "assigned_to": "Bartłomieja Przyniczkę",
  "total_compensation_pln": 1234.56,
  "total_interest_pln": 567.89,
  "invoice_numbers": ["FV/2024/001", "FV/2024/002"],
  "invoice_tiers": ["EUR_40", "EUR_70"]
}
```

### Strategia tonalna
Domyślna: standard_collect. Dostępne: soft_collect, standard_collect, hard_collect, pre_litigation.
Zapytaj użytkownika jeśli nie sprecyzował.

### Ważne
- Rekompensata = per faktura (nie per dłużnika)
- Kurs EUR/PLN z NBP — kalkulator pobiera automatycznie
- Przedawnienie: 3 lata + koniec roku — kalkulator filtruje automatycznie
- bank_account może być "___" jeśli wierzyciel go nie podał

**Termin płatności podajesz SUROWY z faktury.** Kalkulator sam nakłada korektę
art. 115 KC (termin w sobotę/niedzielę/święto przechodzi na pierwszy dzień
roboczy) — od wersji 0.4.0. Nie przeliczaj tego ręcznie i nie podawaj daty już
przesuniętej „na wszelki wypadek": korekta jest idempotentna, więc data
skorygowana da ten sam wynik, ale ręczne liczenie dni to niepotrzebne ryzyko.
Skutki korekty: odsetki startują dzień po terminie EFEKTYWNYM, a faktura
zapłacona w pierwszy dzień roboczy po weekendowym terminie **nie jest**
opóźniona (odsetki 0). Uwaga na koniec miesiąca: termin 31.01 wypadający
w sobotę daje wymagalność w lutym, więc kurs EUR bierze się z ostatniego dnia
roboczego STYCZNIA — to zmienia też kwotę rekompensaty.

**OGRANICZENIE ZAKRESU — dłużnicy publiczni.** Narzędzie liczy **wyłącznie
dłużników prywatnych**: podstawa art. 7 ust. 1 u.p.n.o.t.h., stawka z art. 4
pkt 3 lit. b (stopa referencyjna NBP + 10 p.p.). Dla dłużnika publicznego
podstawą jest **art. 8 ust. 1, a nie art. 7**, a gdy podmiot publiczny jest
jednocześnie podmiotem leczniczym, stawka wynosi **+8 p.p.** (art. 4 pkt 3
lit. a), nie +10 p.p.

Od wersji 0.4.0 obie te rzeczy są w kodzie: `DebtorType`
(`private` / `public_non_medical` / `public_medical`), druga kolumna stawek
`rate_medical` w `INTEREST_RATES` i podstawa prawna w piśmie zależna od statusu
(placeholder `{{PODSTAWA_ODSETEK}}`). **Naliczanie dla dłużnika publicznego jest
jednak nadal ZABLOKOWANE** — kalkulator podnosi `NotImplementedError`
z komunikatem „reżim art. 8 niekompletny". Brakuje ostatniej warstwy: art. 8
ust. 2/4/4a ogranicza termin zapłaty do 30 dni (60 dni dla podmiotu leczniczego)
liczonych od **doręczenia** faktury, a eksporty ERP daty doręczenia nie mają.

Jeśli dłużnik wygląda na podmiot publiczny — SPZOZ, szpital, jednostka
budżetowa, uczelnia, gmina, instytut — **ZATRZYMAJ SIĘ i zapytaj użytkownika.**
Nie ustawiaj `debtor_type` samodzielnie „po nazwie": to ocena prawna. Nie próbuj
też obejść `NotImplementedError` — pismo policzone bez limitu z art. 8 miałoby
zawyżone odsetki, tak samo jak wcześniej miałoby błędną podstawę prawną.

### Czego NIE robić

**Jeśli `calc-rekompensa` zwróci `UnknownRatePeriodError`:**

- **ZATRZYMAJ SIĘ. Nie generuj wezwania.**
- **NIE obchodź błędu.** W szczególności: nie licz odsetek ręcznie, nie pisz
  własnego kalkulatora, nie szukaj stawek w internecie, nie podstawiaj ostatniej
  znanej stawki, nie zawężaj okresu naliczania, żeby zmieścić się w tabeli.
- Napisz użytkownikowi dokładnie to: „Tabela stawek odsetek handlowych nie
  pokrywa daty [X]. Wymagane dopisanie wiersza z obwieszczenia M.P. do
  INTEREST_RATES przed wygenerowaniem wezwania."
- Wyjątek jest **zamierzony**. Oznacza, że narzędzie nie zna prawidłowej stawki
  ustawowej za ten okres. Wygenerowanie wezwania mimo to = pismo z błędną kwotą
  wysłane do dłużnika. Dawniej kalkulator brał w tej sytuacji po cichu ostatnią
  znaną stawkę — dlatego ten wyjątek istnieje.
- Naprawa nie należy do Ciebie, tylko do właściciela repo: sekcja „Aktualizacja
  stawek" poniżej opisuje, co trzeba zrobić.

### Aktualizacja stawek

Stawki odsetek nie aktualizują się same — dwa razy w roku trzeba dopisać jeden
wiersz do tabeli. Instrukcja jest napisana tak, żeby dało się ją wykonać bez
znajomości Pythona.

**Kiedy:** 2 stycznia i 2 lipca. Obwieszczenie ukazuje się zwykle w drugiej
połowie grudnia i drugiej połowie czerwca, więc na początku miesiąca jest już
opublikowane.

**Skąd wziąć stawkę:**

1. Wejdź na https://isap.sejm.gov.pl i szukaj frazy
   `odsetek ustawowych za opóźnienie w transakcjach handlowych`, rocznik bieżący.
2. Otwórz najnowsze **obwieszczenie ministra właściwego do spraw gospodarki**
   (do 2025 r. był to Minister Rozwoju i Technologii, od obwieszczenia na
   I półrocze 2026 — Minister Finansów i Gospodarki).
3. Obwieszczenie podaje **dwie** stawki i od wersji 0.4.0 przepisujesz **OBIE**:
   - `rate` — z punktu „w przypadku transakcji handlowych, w których dłużnikiem
     **nie** jest podmiot publiczny będący podmiotem leczniczym" (wyższa),
   - `rate_medical` — z punktu o publicznym podmiocie leczniczym (niższa).
   Różnica między nimi wynosi zawsze 2,00 p.p. (+10 p.p. vs +8 p.p. do tej samej
   stopy referencyjnej) i pilnuje tego test `test_rate_medical_o_dwa_punkty_nizsza`.
   Jeśli test padnie po Twojej edycji — przepisałeś którąś wartość z błędem.

**Co zmienić:** plik `demand_generator/calc.py`, tabela `INTEREST_RATES`. Dopisz
na SAMYM KOŃCU listy jeden wiersz, kopiując format poprzedniego:

    {"from": "2027-01-01", "to": "2027-06-30", "rate": 12.34, "rate_medical": 10.34},  # M.P. 2026 poz. 1234

Trzy reguły, których nie wolno złamać:

- `from` może być **tylko** `-01-01` albo `-07-01`, a `to` **tylko** `-06-30`
  albo `-12-31`. Stawka jest zamrożona na całe półrocze (art. 11b ustawy
  z 8.03.2013), więc obniżka stóp NBP w środku półrocza nic nie zmienia i
  **nie wolno** dodawać wiersza z datą w środku półrocza.
- W komentarzu po wierszu wpisz sygnaturę obwieszczenia w formacie
  `M.P. rok poz. numer`. Wiersz bez sygnatury oznacz `TO_VERIFY`.
- Stawkę **przepisz** z obwieszczenia. Nie licz jej samodzielnie jako
  „stopa referencyjna NBP + 10 punktów" (dłużnik prywatny; pozostałe przypadki
  poza zakresem narzędzia) — pomyłka w stopie da błędne wezwania.

**Odsetki KC** (plik `demand_generator/civil_interest.py`, tabela
`CIVIL_INTEREST_RATES`) działają inaczej: zmieniają się w dniu decyzji RPP, a nie
co pół roku. Tam dopisujesz wiersz tylko wtedy, gdy RPP zmieniła stopy — i za
każdym razem, gdy to sprawdzasz, podnieś `LAST_VERIFIED_DATE` na dzień
sprawdzenia, **nawet jeśli nic się nie zmieniło**. Ta data jest jedynym
sygnałem, że ktoś w ogóle patrzył.

**Jak sprawdzić, czy jest robota:**

    check-rates

Komenda wypisuje, ile dni zostało do końca każdej z tabel. Kod wyjścia `0`
oznacza spokój, `1` — mniej niż 30 dni, `2` — termin już minął. Sam kalkulator
też ostrzega (na stderr) przy każdym uruchomieniu, gdy zostało mniej niż 30 dni.
