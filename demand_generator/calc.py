#!/usr/bin/env python3
"""
rekompensa.pl — Kalkulator rekompensat i odsetek handlowych
Moduł reusable: importowany przez generator wezwań, generator pozwów, raport prospektowy.

Podstawa prawna:
    - Art. 7 ust. 1 ustawy z 8.03.2013 o przeciwdziałaniu nadmiernym opóźnieniom
      w transakcjach handlowych (t.j. Dz.U. 2023 poz. 1790) — odsetki od należności głównej
    - Art. 10 ust. 1 i 1a tamże — rekompensaty
    - Art. 481 § 2 KC — odsetki ustawowe za opóźnienie od kwoty rekompensaty
      (zob. wyrok SO Białystok VII Ga 377/25 z 28.11.2025)
    - Art. 118 KC — przedawnienie (3 lata + reguła końca roku)
"""

import warnings
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from enum import Enum
from typing import Optional

import holidays
import requests


class DebtorType(str, Enum):
    """Status dłużnika — dwie NIEZALEŻNE osie z art. 4 pkt 3 ustawy z 8.03.2013.

    Oś 1 (publiczny czy nie) rozstrzyga PODSTAWĘ PRAWNĄ odsetek: art. 7 ust. 1
    wprost wyłącza transakcje, w których dłużnikiem jest podmiot publiczny —
    dla nich podstawą jest art. 8 ust. 1.
    Oś 2 (podmiot leczniczy czy nie) rozstrzyga STAWKĘ: art. 4 pkt 3 lit. a daje
    stopę referencyjną NBP + 8 p.p. dla podmiotu publicznego BĘDĄCEGO podmiotem
    leczniczym, lit. b + 10 p.p. dla pozostałych.

    Gmina jest więc PUBLIC_NON_MEDICAL: art. 8 ust. 1 + 10 p.p.

    Bliźniak w torze A: rekompensa/calculator/calc.py (tplegal-tools), gdzie te
    same wartości siedzą w enumie `debtor_type` w bazie. Klasyfikacja podmiotu to
    OCENA PRAWNA — nie ustawiamy jej regexem po nazwie.
    """

    PRIVATE = "private"
    PUBLIC_NON_MEDICAL = "public_non_medical"
    PUBLIC_MEDICAL = "public_medical"


# Komunikat bramki reżimu art. 8 — patrz _require_implemented_debtor_type.
ART8_NOT_IMPLEMENTED_MSG = (
    "reżim art. 8 niekompletny — brak limitu terminu z art. 8 ust. 2/4a, "
    "wymagana decyzja o dacie doręczenia"
)


def _require_implemented_debtor_type(debtor_type: "DebtorType") -> None:
    """Twardy stop dla dłużników publicznych (art. 8 ust. 1).

    Stawka +8 p.p. i podstawa prawna są już zaimplementowane, ale reżim art. 8
    ma jeszcze jedną warstwę: art. 8 ust. 2/4/4a ogranicza termin zapłaty do
    30 dni (60 dni dla podmiotu leczniczego), liczonych od DORĘCZENIA faktury.
    Eksporty ERP nie zawierają daty doręczenia, więc bez decyzji o domniemaniu
    (np. data wystawienia + 3 dni) każde naliczenie byłoby zgadywaniem terminu
    wymagalności. Lepiej niepoliczone niż policzone źle.

    Bramka stoi na WEJŚCIACH liczących kwoty (calculate_interest / _detailed /
    calculate_invoice / calculate_batch). Sam lookup stawki (get_interest_rate)
    jej NIE ma — ma pozostać testowalny dla obu kolumn tabeli.
    """
    if debtor_type != DebtorType.PRIVATE:
        raise NotImplementedError(ART8_NOT_IMPLEMENTED_MSG)


class UnknownRatePeriodError(RuntimeError):
    """Brak stawki odsetek handlowych dla żądanej daty w INTEREST_RATES.

    Podnoszony, gdy naliczanie wychodzi POZA ostatni znany okres tabeli — czyli
    gdy weszło w życie nowe obwieszczenie, a tabela nie została zaktualizowana.
    Wcześniej w tej sytuacji działał cichy fallback na ostatnią znaną stawkę,
    co dawało wezwania z błędną kwotą odsetek i bez żadnego sygnału o błędzie.
    """


# ═══════════════════════════════════════════════════════════════════════
# STAŁE
# ═══════════════════════════════════════════════════════════════════════

# Stawki odsetek ustawowych za opóźnienie w transakcjach handlowych.
# DWIE KOLUMNY, obie z tego samego obwieszczenia M.P. (art. 11c):
#   "rate"         — art. 4 pkt 3 lit. b: stopa referencyjna NBP + 10 p.p.
#                    Dłużnik prywatny ORAZ publiczny NIE będący podmiotem
#                    leczniczym (np. gmina).
#   "rate_medical" — art. 4 pkt 3 lit. a: stopa referencyjna NBP + 8 p.p.
#                    WYŁĄCZNIE podmiot publiczny będący podmiotem leczniczym.
# Inwariant: rate_medical == rate - 2.00 (różne dodatki do tej samej stopy).
# Uwaga: samo naliczanie dla dłużnika publicznego jest ZABLOKOWANE
# (_require_implemented_debtor_type) — brakuje limitu terminu z art. 8 ust. 2/4a.
#
# Art. 11b: stawka jest ZAMROŻONA na całe półrocze — stosuje się stopę
# referencyjną NBP z dnia 1 stycznia do odsetek należnych za okres 1.01-30.06
# i z dnia 1 lipca do odsetek za okres 1.07-31.12. Zmiana stopy NBP w trakcie
# półrocza NIE zmienia stawki. Dlatego wiersze MUSZĄ pokrywać się z półroczami:
# "from" wyłącznie 01-01 albo 07-01, "to" wyłącznie 06-30 albo 12-31.
#
# UWAGA: wymaga manualnej aktualizacji dwa razy w roku (2 stycznia i 2 lipca).
# Instrukcja krok po kroku: PROJECT_INSTRUCTIONS.md, sekcja "Aktualizacja stawek".
INTEREST_RATES = [
    # Oba wiersze 2022 są TO_VERIFY — nie mają cytowanego obwieszczenia. Roszczenia
    # z 2022 r. są już przedawnione (art. 118 KC), więc nie blokują naliczania.
    {"from": "2022-01-01", "to": "2022-06-30", "rate": 11.75, "rate_medical": 9.75},   # ref 1.75, TO_VERIFY
    {"from": "2022-07-01", "to": "2022-12-31", "rate": 16.00, "rate_medical": 14.00},  # ref 6.00, TO_VERIFY
    {"from": "2023-01-01", "to": "2023-06-30", "rate": 16.75, "rate_medical": 14.75},  # ref 6.75, M.P. 2022 poz. 1263
    {"from": "2023-07-01", "to": "2023-12-31", "rate": 16.75, "rate_medical": 14.75},  # ref 6.75, M.P. 2023 poz. 626
    {"from": "2024-01-01", "to": "2024-06-30", "rate": 15.75, "rate_medical": 13.75},  # ref 5.75, M.P. 2023 poz. 1465
    {"from": "2024-07-01", "to": "2024-12-31", "rate": 15.75, "rate_medical": 13.75},  # ref 5.75, M.P. 2024 poz. 546
    {"from": "2025-01-01", "to": "2025-06-30", "rate": 15.75, "rate_medical": 13.75},  # ref 5.75, M.P. 2024 poz. 1106
    {"from": "2025-07-01", "to": "2025-12-31", "rate": 15.25, "rate_medical": 13.25},  # ref 5.25, M.P. 2025 poz. 602
    {"from": "2026-01-01", "to": "2026-06-30", "rate": 14.00, "rate_medical": 12.00},  # ref 4.00, M.P. 2025 poz. 1257
    # Stawka wpisana wprost z obwieszczenia (nie wyliczona z 3,75 + 10 p.p.):
    # M.P. 2026 poz. 642 — obwieszczenie Ministra Finansów i Gospodarki
    # z 22.06.2026 (ogłoszone 26.06.2026) podaje dla okresu 1.07-31.12.2026
    # 13,75% dla dłużnika, który NIE jest podmiotem publicznym będącym podmiotem
    # leczniczym, oraz 11,75% dla takiego podmiotu (art. 4 pkt 3 lit. a). OBIE
    # stawki przepisane wprost z obwieszczenia — one potwierdzają inwariant
    # rate_medical == rate - 2,00 p.p. dla całej tabeli.
    {"from": "2026-07-01", "to": "2026-12-31", "rate": 13.75, "rate_medical": 11.75},  # M.P. 2026 poz. 642, ref 3.75
]

# Preparse dates
for _r in INTEREST_RATES:
    _r["from_d"] = date.fromisoformat(_r["from"])
    _r["to_d"] = date.fromisoformat(_r["to"])

# Progi rekompensaty (art. 10 ust. 1)
# (próg_kwoty_brutto, rekompensata_eur)
COMPENSATION_THRESHOLDS = [
    (Decimal("5000"), Decimal("40")),           # pkt 1: <= 5000 PLN
    (Decimal("49999.99"), Decimal("70")),        # pkt 2: > 5000 i < 50000 PLN (strict <)
    (Decimal("999999999"), Decimal("100")),      # pkt 3: >= 50000 PLN
]

# Tier labels
TIER_LABELS = {
    Decimal("40"): "EUR_40",
    Decimal("70"): "EUR_70",
    Decimal("100"): "EUR_100",
}

# Polskie święta
PL_HOLIDAYS = holidays.Poland()

# Cache kursów NBP EUR/PLN
_nbp_cache: dict[str, Decimal] = {}


# ═══════════════════════════════════════════════════════════════════════
# DNI ROBOCZE
# ═══════════════════════════════════════════════════════════════════════

def next_business_day(d: date) -> date:
    """Art. 115 KC: jeśli termin wypada w dzień wolny, przesuwa na następny roboczy.

    IDEMPOTENTNA: wynik jest zawsze dniem roboczym, więc kolejne wywołanie zwraca
    tę samą datę. Na tym stoi nakładanie korekty wewnątrz kalkulatora — wywołujący,
    który skorygował termin sam, dostaje ten sam wynik.
    """
    while d.weekday() >= 5 or d in PL_HOLIDAYS:
        d += timedelta(days=1)
    return d


def compute_effective_due_date(due: date) -> date:
    """Termin płatności po korekcie art. 115 KC — alias czytelności intencji.

    Bliźniak: rekompensa/dates.py w torze A (tplegal-tools).
    """
    return next_business_day(due)


def last_business_day_of_month(year: int, month: int) -> date:
    """Ostatni dzień roboczy danego miesiąca."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    while last_day.weekday() >= 5 or last_day in PL_HOLIDAYS:
        last_day -= timedelta(days=1)
    return last_day


# ═══════════════════════════════════════════════════════════════════════
# ODSETKI (art. 7 ust. 1)
# ═══════════════════════════════════════════════════════════════════════

def _unknown_rate_period_error(d: date) -> UnknownRatePeriodError:
    """Buduje wyjątek z gotową instrukcją naprawy dla operatora."""
    last = INTEREST_RATES[-1]
    return UnknownRatePeriodError(
        f"Brak stawki odsetek handlowych dla dnia {d.isoformat()}. "
        f"Ostatni znany okres kończy się {last['to_d'].isoformat()} "
        f"(stawka {last['rate']:.2f}%). "
        "Co zrobić: pobierz z Monitora Polskiego obwieszczenie ministra właściwego "
        "do spraw gospodarki (art. 11c ustawy z 8.03.2013) obejmujące tę datę "
        "i dopisz wiersz do INTEREST_RATES w demand_generator/calc.py. "
        "Instrukcja krok po kroku: PROJECT_INSTRUCTIONS.md, sekcja "
        "'Aktualizacja stawek'."
    )


def _rate_key(debtor_type: DebtorType) -> str:
    """Która kolumna tabeli stawek obowiązuje dla danego statusu dłużnika."""
    return "rate_medical" if debtor_type == DebtorType.PUBLIC_MEDICAL else "rate"


def get_interest_rate(d: date, debtor_type: DebtorType = DebtorType.PRIVATE) -> float:
    """Stawka odsetek handlowych obowiązująca w danym dniu.

    Art. 11b: stawka jest zamrożona na całe półrocze, więc tabela ma domknięte
    okresy. Data po ostatnim okresie oznacza nieaktualną tabelę, nie "brak
    zmiany" — dlatego jest to błąd, a nie fallback.

    debtor_type wybiera kolumnę: PUBLIC_MEDICAL bierze +8 p.p. (art. 4 pkt 3
    lit. a), pozostałe +10 p.p. (lit. b). Sam lookup nie ma bramki reżimu art. 8 —
    nie produkuje kwoty roszczenia.
    """
    key = _rate_key(debtor_type)
    for r in INTEREST_RATES:
        if r["from_d"] <= d <= r["to_d"]:
            return r[key]
    if d > INTEREST_RATES[-1]["to_d"]:
        raise _unknown_rate_period_error(d)
    # Data przed początkiem tabeli. Zachowane dotychczasowe zachowanie — takie
    # roszczenia są i tak przedawnione (art. 118 KC), a zmiana tej gałęzi jest
    # poza zakresem tej poprawki.
    return INTEREST_RATES[-1][key]


def _interest_start_date(due_date: date, interest_start_override: Optional[date]) -> date:
    """Pierwszy dzień naliczania odsetek po uwzględnieniu override."""
    return interest_start_override if interest_start_override else due_date + timedelta(days=1)


def _interest_rate_period_end(current: date, payment_date: date) -> date:
    """Koniec podokresu dla stawki obowiązującej w dniu current.

    Bliźniak fallbacku z get_interest_rate: wcześniej zwracał payment_date, przez
    co cały ogon okresu po nieznanej granicy leciał jednym podokresem po ostatniej
    znanej stawce.
    """
    for r in INTEREST_RATES:
        if r["from_d"] <= current <= r["to_d"]:
            return min(payment_date, r["to_d"])
    if current > INTEREST_RATES[-1]["to_d"]:
        raise _unknown_rate_period_error(current)
    return payment_date


def _iter_interest_periods(
    gross: Decimal,
    start_date: date,
    payment_date: date,
    debtor_type: DebtorType = DebtorType.PRIVATE,
) -> list[dict]:
    """Podziel okres naliczania na podokresy stawek i policz kwoty."""
    periods = []
    current = start_date

    while current <= payment_date:
        rate = get_interest_rate(current, debtor_type)
        period_end = _interest_rate_period_end(current, payment_date)
        days = (period_end - current).days + 1
        raw_amount = gross * Decimal(str(rate)) / Decimal("100") * Decimal(str(days)) / Decimal("365")

        periods.append({
            "from": current,
            "to": period_end,
            "days": days,
            "rate": rate,
            "raw_amount": raw_amount,
            "amount": raw_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        })
        current = period_end + timedelta(days=1)

    return periods


def calculate_interest(
    gross: Decimal,
    due_date: date,
    payment_date: date,
    interest_start_override: Optional[date] = None,
    debtor_type: DebtorType = DebtorType.PRIVATE,
) -> Decimal:
    """
    Oblicza odsetki za opóźnienie w transakcji handlowej (art. 7 ust. 1).
    Naliczane dziennie, od dnia po terminie płatności do dnia zapłaty (włącznie).
    Uwzględnia zmiany stawki w trakcie okresu (podział na podokresy).

    Args:
        gross: kwota brutto faktury
        due_date: termin płatności SUROWY z faktury — korektę art. 115 KC
            kalkulator nakłada sam (idempotentnie, więc data już skorygowana
            przez wywołującego daje ten sam wynik)
        payment_date: data faktycznej zapłaty
        interest_start_override: jeśli podany, odsetki liczone od tej daty
            (kroczące przedawnienie — odcięcie przedawnionych dni)
        debtor_type: status dłużnika; != PRIVATE podnosi NotImplementedError
            (reżim art. 8 niekompletny)

    Returns:
        Kwota odsetek w PLN, zaokrąglona do 2 miejsc.
    """
    _require_implemented_debtor_type(debtor_type)
    due_date = compute_effective_due_date(due_date)

    if payment_date <= due_date:
        return Decimal("0")

    start_date = _interest_start_date(due_date, interest_start_override)
    if start_date > payment_date:
        return Decimal("0")

    total = sum(
        (
            period["raw_amount"]
            for period in _iter_interest_periods(
                gross, start_date, payment_date, debtor_type
            )
        ),
        Decimal("0"),
    )

    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_interest_detailed(
    gross: Decimal,
    due_date: date,
    payment_date: date,
    interest_start_override: Optional[date] = None,
    debtor_type: DebtorType = DebtorType.PRIVATE,
) -> dict:
    """
    Jak calculate_interest, ale zwraca szczegóły podokresów.

    Returns:
        {
            "total": Decimal,
            "periods": [{"from": date, "to": date, "days": int, "rate": float, "amount": Decimal}, ...],
            "start_date": date,
            "end_date": date,
        }
    """
    _require_implemented_debtor_type(debtor_type)
    due_date = compute_effective_due_date(due_date)

    result = {"total": Decimal("0"), "periods": [], "start_date": None, "end_date": None}

    if payment_date <= due_date:
        return result

    start_date = _interest_start_date(due_date, interest_start_override)
    if start_date > payment_date:
        return result

    result["start_date"] = start_date
    result["end_date"] = payment_date

    for period in _iter_interest_periods(gross, start_date, payment_date, debtor_type):
        result["periods"].append({
            "from": period["from"],
            "to": period["to"],
            "days": period["days"],
            "rate": period["rate"],
            "amount": period["amount"],
        })
        result["total"] += period["amount"]

    result["total"] = result["total"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result


# ═══════════════════════════════════════════════════════════════════════
# KURSY NBP EUR/PLN
# ═══════════════════════════════════════════════════════════════════════

def get_nbp_eur_rate(target_date: date) -> Decimal:
    """
    Pobiera kurs średni EUR/PLN z tabeli A NBP dla danej daty.
    Jeśli data to dzień wolny, cofa się do 5 dni wstecz.
    Cachuje wyniki w pamięci.
    """
    cache_key = target_date.isoformat()
    if cache_key in _nbp_cache:
        return _nbp_cache[cache_key]

    for offset in range(6):
        check_date = target_date - timedelta(days=offset)
        url = f"https://api.nbp.pl/api/exchangerates/rates/a/eur/{check_date.isoformat()}/?format=json"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                rate = Decimal(str(data["rates"][0]["mid"]))
                _nbp_cache[cache_key] = rate
                return rate
        except Exception:
            continue

    warnings.warn(f"NBP API failed for {target_date}, using fallback 4.30")
    fallback = Decimal("4.30")
    _nbp_cache[cache_key] = fallback
    return fallback


def get_compensation_eur_rate_date(due_date: date) -> date:
    """
    Art. 10 ust. 1a: kurs EUR z ostatniego dnia roboczego miesiąca
    POPRZEDZAJĄCEGO miesiąc, w którym świadczenie stało się wymagalne.

    Wymagalność liczona od terminu PO korekcie art. 115 KC — termin z końca
    miesiąca wypadający w dzień wolny przenosi więc miesiąc kursu (31.01.2026 to
    sobota → wymagalność w lutym → kurs z 30.01.2026). Korekta jest idempotentna.
    """
    due_date = compute_effective_due_date(due_date)
    if due_date.month == 1:
        prev_month = 12
        prev_year = due_date.year - 1
    else:
        prev_month = due_date.month - 1
        prev_year = due_date.year
    return last_business_day_of_month(prev_year, prev_month)


# ═══════════════════════════════════════════════════════════════════════
# REKOMPENSATY (art. 10 ust. 1)
# ═══════════════════════════════════════════════════════════════════════

def get_compensation_tier(gross: Decimal) -> tuple[Decimal, str]:
    """
    Zwraca (kwota_eur, tier_label) na podstawie kwoty brutto faktury.

    Art. 10 ust. 1:
        pkt 1: <= 5000 PLN -> 40 EUR
        pkt 2: > 5000 i < 50000 PLN -> 70 EUR
        pkt 3: >= 50000 PLN -> 100 EUR

    UWAGA: pkt 2 mówi "niższa niż 50 000", a pkt 3 "równa lub wyższa od 50 000".
    Dlatego próg 50000 daje 100 EUR (nie 70).
    """
    for threshold, amount in COMPENSATION_THRESHOLDS:
        if gross <= threshold:
            return amount, TIER_LABELS[amount]
    return Decimal("100"), "EUR_100"


def calculate_compensation(gross: Decimal, due_date: date) -> dict:
    """
    Oblicza rekompensatę za koszty odzyskiwania należności (art. 10 ust. 1).
    Per invoice, per TSUE C-585/20.

    due_date surowa — korekta art. 115 KC nakładana wewnątrz (przez
    get_compensation_eur_rate_date). Art. 10 ust. 1 odsyła i do art. 7 ust. 1,
    i do art. 8 ust. 1, więc sama rekompensata nie zależy od statusu dłużnika.

    Returns:
        {
            "comp_eur": Decimal,    # kwota EUR (40/70/100)
            "tier": str,            # "EUR_40" / "EUR_70" / "EUR_100"
            "eur_rate": Decimal,    # kurs EUR/PLN
            "eur_rate_date": date,  # data kursu
            "comp_pln": Decimal,    # kwota PLN
        }
    """
    comp_eur, tier = get_compensation_tier(gross)
    rate_date = get_compensation_eur_rate_date(due_date)
    eur_rate = get_nbp_eur_rate(rate_date)
    comp_pln = (comp_eur * eur_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "comp_eur": comp_eur,
        "tier": tier,
        "eur_rate": eur_rate,
        "eur_rate_date": rate_date,
        "comp_pln": comp_pln,
    }


# ═══════════════════════════════════════════════════════════════════════
# ODSETKI USTAWOWE OD REKOMPENSATY (art. 481 § 2 KC)
# ═══════════════════════════════════════════════════════════════════════

def compensation_interest_start_date(due_date: date) -> date:
    """
    Pierwszy dzień odsetek KC od rekompensaty: termin efektywny + 2 dni.

    Rekompensata przysługuje „od dnia nabycia uprawnienia do odsetek" (art. 10
    ust. 1 u.p.n.o.t.h.), czyli od dnia po terminie płatności — tego samego,
    od którego biegną odsetki handlowe (art. 7 ust. 1). Tego dnia rekompensata
    dopiero staje się wymagalna; opóźnienie w jej zapłacie (art. 481 § 1 KC)
    zaczyna się dzień później.

    VII Ga 377/25 (SO Białystok, 28.11.2025) wiąże wymagalność rekompensaty
    z „chwilą popadnięcia przez dłużnika w opóźnienie z płatnością należności
    głównej" — nie z samym terminem płatności. Wyrok NIE rozstrzyga, od którego
    dnia biegną odsetki od rekompensaty (powódka liczyła je od dnia jej
    wymagalności, sąd zasądził bez badania tego dnia) — ten start jest
    konsekwencją art. 481 § 1 KC, nie cytatem z wyroku.

    due_date surowa — korekta art. 115 KC nakładana wewnątrz.
    """
    return compute_effective_due_date(due_date) + timedelta(days=2)


def calculate_civil_interest_for_invoice(
    compensation_pln: Decimal,
    due_date: date,
    cutoff_date: Optional[date] = None,
) -> Decimal:
    """
    Thin wrapper na civil_interest.calculate_civil_interest.
    Liczy odsetki ustawowe za opóźnienie (art. 481 § 2 KC) od kwoty rekompensaty
    za okres [termin_efektywny+2, cutoff_date) — start: patrz
    compensation_interest_start_date.

    Odsetki ustawowe KC, NIE handlowe art. 7 UPNOTH (VII Ga 377/25 za
    SO Rzeszów VI Ga 528/21).

    Args:
        compensation_pln: kwota rekompensaty PLN
        due_date: termin płatności faktury SUROWY — korekta art. 115 KC
            nakładana wewnątrz
        cutoff_date: dzień naliczania do (None → date.today())

    Returns:
        Decimal('0') gdy compensation_pln == 0, start >= cutoff_date,
        lub gdy start jest przed zakresem tabeli CIVIL_INTEREST_RATES
        (np. faktury sprzed 08.09.2022 — graceful fallback, bez raise).
    """
    from demand_generator.civil_interest import calculate_civil_interest
    if cutoff_date is None:
        cutoff_date = date.today()
    try:
        return calculate_civil_interest(
            amount_pln=compensation_pln,
            start_date=compensation_interest_start_date(due_date),
            end_date=cutoff_date,
        )
    except ValueError:
        # start_date przed zakresem tabeli stawek — zwracamy 0 zamiast crashować
        # batcha. Faktury pre-2022 nie powinny mieć civil interest w tym module;
        # jeśli potrzebujesz, rozszerz CIVIL_INTEREST_RATES wstecz.
        return Decimal("0")


# ═══════════════════════════════════════════════════════════════════════
# PRZEDAWNIENIE (art. 118 KC)
# ═══════════════════════════════════════════════════════════════════════

def prescription_expiry_date(claim_accrual_date: date) -> date:
    """
    Data upływu przedawnienia roszczenia.
    Art. 118 KC: 3 lata + reguła końca roku kalendarzowego (zd. 2, nowela 9.07.2018).
    """
    raw_expiry_year = claim_accrual_date.year + 3
    return date(raw_expiry_year, 12, 31)


def is_fully_prescribed(due_date: date, lawsuit_date: date) -> bool:
    """
    Czy roszczenie (rekompensata + pierwszy dzień odsetek) jest przedawnione?
    Dies a quo = dzień po terminie PO korekcie art. 115 KC.
    """
    claim_accrual = compute_effective_due_date(due_date) + timedelta(days=1)
    expiry = prescription_expiry_date(claim_accrual)
    return lawsuit_date > expiry


def find_earliest_non_prescribed_interest_date(
    interest_start: date,
    lawsuit_date: date,
) -> Optional[date]:
    """
    Kroczące przedawnienie odsetek: pierwszy nieprzedawniony dzień.
    Optymalizacja: skok po granicach lat (ten sam rok -> ten sam expiry).

    Returns:
        Pierwszy nieprzedawniony dzień, lub None jeśli wszystko przedawnione.
    """
    for year in range(interest_start.year, interest_start.year + 5):
        expiry = date(year + 3, 12, 31)
        if lawsuit_date <= expiry:
            if year == interest_start.year:
                return interest_start
            else:
                return date(year, 1, 1)
    return None


def check_near_expiry(due_date: date, reference_date: date, threshold_days: int = 183) -> bool:
    """Czy roszczenie przedawni się w ciągu threshold_days (domyślnie ~6 miesięcy)?

    due_date surowa — korekta art. 115 KC nakładana wewnątrz (idempotentnie).
    """
    claim_accrual = compute_effective_due_date(due_date) + timedelta(days=1)
    expiry = prescription_expiry_date(claim_accrual)
    days_left = (expiry - reference_date).days
    return 0 < days_left <= threshold_days


# ═══════════════════════════════════════════════════════════════════════
# OPŁATA SĄDOWA + KZP
# ═══════════════════════════════════════════════════════════════════════

def court_fee(wps: Decimal) -> Decimal:
    """
    Opłata sądowa wg art. 13 ustawy o kosztach sądowych w sprawach cywilnych
    (t.j. Dz.U. 2025 poz. 1228): ust. 1 to widełki stałe do 20 000 zł, ust. 2
    opłata stosunkowa 5% powyżej, a art. 21 każe zaokrąglić końcówkę W GÓRĘ
    do pełnego złotego.

    Cytowany wcześniej t.j. Dz.U. 2024 poz. 959 ma w ISAP status wygaśnięcia.
    Brzmienie art. 13 i art. 21 jest w obu tekstach identyczne, więc podmiana
    jest porządkowa — żadna kwota się nie zmienia. Nowela Dz.U. 2026 poz. 346
    rusza u.k.s.c., ale wchodzi w życie dopiero 30.09.2028.
    """
    wps = Decimal(str(wps))
    brackets = [
        (Decimal("500"), Decimal("30")),
        (Decimal("1500"), Decimal("100")),
        (Decimal("4000"), Decimal("200")),
        (Decimal("7500"), Decimal("400")),
        (Decimal("10000"), Decimal("500")),
        (Decimal("15000"), Decimal("750")),
        (Decimal("20000"), Decimal("1000")),
    ]
    for threshold, fee in brackets:
        if wps <= threshold:
            return fee

    # > 20000: 5% WPS, zaokrąglone w górę do pełnego złotego (art. 21 u.k.s.c.),
    # max 200 000
    fee = (wps * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_CEILING)
    return min(fee, Decimal("200000"))


def legal_representation_cost(wps: Decimal) -> Decimal:
    """
    Minimalne koszty zastępstwa procesowego radcy prawnego
    (§ 2 rozp. MS w sprawie opłat za czynności radców prawnych).
    """
    wps = Decimal(str(wps))
    brackets = [
        (Decimal("500"), Decimal("90")),
        (Decimal("1500"), Decimal("270")),
        (Decimal("5000"), Decimal("900")),
        (Decimal("10000"), Decimal("1800")),
        (Decimal("50000"), Decimal("3600")),
        (Decimal("200000"), Decimal("5400")),
        (Decimal("2000000"), Decimal("10800")),
        (Decimal("5000000"), Decimal("15000")),
    ]
    for threshold, cost in brackets:
        if wps <= threshold:
            return cost
    return Decimal("25000")


# ═══════════════════════════════════════════════════════════════════════
# KALKULACJA ŁĄCZNA PER FAKTURA
# ═══════════════════════════════════════════════════════════════════════

def _payment_tranches(
    gross: Decimal,
    payment_date: date,
    payments: Optional[list[dict]],
) -> tuple[list[tuple[Decimal, date]], Decimal]:
    """Transze naliczania odsetek: (kwota, dzień końca naliczania) + suma wpłat.

    payments=None — stary kontrakt: całość zapłacona w payment_date.
    payments=[...] — wpłaty częściowe (także pusta lista = nic nie wpłacono);
    każda niesie odsetki od swojej kwoty do swojej daty, niespłacona reszta
    do payment_date (dla faktury niezapłaconej: dzień naliczania). To daje
    odsetki od malejącego salda — wpłata zaliczana na należność główną.
    """
    if payments is None:
        return [(gross, payment_date)], gross

    tranches = []
    paid = Decimal("0")
    for p in payments:
        amount = Decimal(str(p["amount"]))
        if amount <= 0:
            raise ValueError(f"Wpłata musi być dodatnia, jest {amount} ({p['date']})")
        tranches.append((amount, p["date"]))
        paid += amount

    if paid > gross:
        raise ValueError(
            f"Suma wpłat {paid} przekracza kwotę brutto faktury {gross} — "
            "nadpłata albo wpłata przypisana do złej faktury"
        )
    if paid < gross:
        tranches.append((gross - paid, payment_date))
    return tranches, paid


def calculate_invoice(
    gross: Decimal,
    due_date: date,
    payment_date: date,
    lawsuit_date: Optional[date] = None,
    cutoff_date: Optional[date] = None,
    debtor_type: DebtorType = DebtorType.PRIVATE,
    payments: Optional[list[dict]] = None,
) -> dict:
    """
    Pełna kalkulacja dla jednej faktury: rekompensata + odsetki handlowe
    (art. 7 UPNOTH) + odsetki KC od rekompensaty (art. 481 § 2 KC) + przedawnienie.

    Args:
        gross: kwota brutto
        due_date: termin płatności SUROWY z faktury — korekta art. 115 KC
            nakładana wewnątrz (idempotentnie); `delay_days` jest więc liczone
            od terminu efektywnego
        payment_date: data zapłaty
        lawsuit_date: planowana data pozwu (None = bez filtra przedawnienia)
        cutoff_date: dzień do którego liczymy odsetki KC od rekompensaty
            (None = date.today())
        debtor_type: status dłużnika; != PRIVATE podnosi NotImplementedError
        payments: wpłaty częściowe [{"date": date, "amount": Decimal}, ...].
            None = całość zapłacona w payment_date (stary kontrakt). Lista
            (także pusta) = niespłacona reszta niesie odsetki do payment_date.
            Rekompensata zostaje JEDNA, z progu pełnej kwoty brutto.

    Returns:
        {
            "compensation": {...},          # z calculate_compensation
            "interest": Decimal,            # kwota odsetek handlowych PLN (art. 7)
            "interest_detailed": {...},     # podokresy wszystkich transz
            "civil_interest": Decimal,      # odsetki KC od rekompensaty (art. 481 § 2)
            "total_pln": Decimal,           # compensation_pln + interest (BEZ civil_interest)
            "prescription_status": str,     # NIEPRZEDAWNIONE / CZESCIOWE / PRZEDAWNIONE
            "prescribed_days": int,
            "delay_days": int,              # do ostatniej wpłaty, a przy reszcie — do payment_date
            "paid_pln": Decimal,            # suma wpłat
            "outstanding_pln": Decimal,     # należność główna do zapłaty
        }
    """
    _require_implemented_debtor_type(debtor_type)
    due_date = compute_effective_due_date(due_date)
    tranches, paid = _payment_tranches(gross, payment_date, payments)
    last_day = max(end for _, end in tranches)

    result = {
        "delay_days": max(0, (last_day - due_date).days),
        "prescription_status": "NIEPRZEDAWNIONE",
        "prescribed_days": 0,
        "paid_pln": paid,
        "outstanding_pln": gross - paid,
    }

    # Rekompensata
    result["compensation"] = calculate_compensation(gross, due_date)

    # Przedawnienie
    interest_start = due_date + timedelta(days=1)

    if lawsuit_date and is_fully_prescribed(due_date, lawsuit_date):
        result["prescription_status"] = "PRZEDAWNIONE"
        result["compensation"]["comp_eur"] = Decimal("0")
        result["compensation"]["comp_pln"] = Decimal("0")
        result["interest"] = Decimal("0")
        result["interest_detailed"] = {"total": Decimal("0"), "periods": []}
        result["civil_interest"] = Decimal("0")
        result["total_pln"] = Decimal("0")
        return result

    # Odsetki handlowe z ewentualnym kroczącym przedawnieniem
    adjusted_start = interest_start
    if lawsuit_date:
        earliest = find_earliest_non_prescribed_interest_date(interest_start, lawsuit_date)
        if earliest is None:
            adjusted_start = last_day + timedelta(days=1)  # force zero
        elif earliest > interest_start:
            adjusted_start = earliest
            result["prescription_status"] = "CZESCIOWE_PRZEDAWNIENIE"
            result["prescribed_days"] = (earliest - interest_start).days

    # Każda transza liczona jak osobna zapłata swojej kwoty — suma zaokrąglonych
    # podokresów, tak jak dotąd dla całej faktury (kanon ścieżki produkcyjnej).
    detailed = {"total": Decimal("0"), "periods": [], "start_date": None, "end_date": None}
    for amount, end in tranches:
        part = calculate_interest_detailed(
            amount,
            due_date,
            end,
            interest_start_override=adjusted_start,
            debtor_type=debtor_type,
        )
        detailed["total"] += part["total"]
        detailed["periods"].extend({**p, "base": amount} for p in part["periods"])
        if part["start_date"]:
            detailed["start_date"] = part["start_date"]
            detailed["end_date"] = max(filter(None, [detailed["end_date"], part["end_date"]]))
    result["interest_detailed"] = detailed
    result["interest"] = detailed["total"]

    # Odsetki KC od rekompensaty (art. 481 § 2) — od termin_efektywny+2 do cutoff.
    # UWAGA: civil_interest jest OSOBNYM polem. total_pln zachowuje starą
    # semantykę (comp + interest handlowe) — żeby nie łamać existing callerów.
    # Sumę z odsetkami KC czytelnik robi sam: comp_pln + interest + civil_interest.
    result["civil_interest"] = calculate_civil_interest_for_invoice(
        result["compensation"]["comp_pln"], due_date, cutoff_date
    )

    result["total_pln"] = result["compensation"]["comp_pln"] + result["interest"]

    return result


def calculate_batch(
    invoices: list[dict],
    lawsuit_date: Optional[date] = None,
    cutoff_date: Optional[date] = None,
    debtor_type: DebtorType = DebtorType.PRIVATE,
) -> dict:
    """
    Kalkulacja batcha faktur.

    Args:
        invoices: lista dict z kluczami:
            - gross (Decimal): kwota brutto
            - due_date (date): termin płatności SUROWY (korekta art. 115 KC
              nakładana wewnątrz calculate_invoice)
            - payment_date (date): data zapłaty
            - invoice_number (str, optional): numer faktury
            - payments (list, optional): wpłaty częściowe — patrz calculate_invoice
        lawsuit_date: planowana data pozwu
        cutoff_date: dzień naliczania odsetek KC od rekompensaty
            (None = date.today())
        debtor_type: status dłużnika; != PRIVATE podnosi NotImplementedError

    Returns:
        {
            "invoices": [calculate_invoice result per invoice + invoice_number],
            "total_compensation_eur": Decimal,
            "total_compensation_pln": Decimal,
            "total_interest_pln": Decimal,          # odsetki handlowe (art. 7)
            "total_civil_interest_pln": Decimal,    # odsetki KC od rekompensaty (art. 481 § 2)
            "total_outstanding_pln": Decimal,       # należność główna: brutto − wpłaty (bez przedawnionych)
            "total_claim_pln": Decimal,             # compensation + interest handlowe (BEZ civil)
            "wps": Decimal,
            "court_fee": Decimal,
            "legal_representation_cost": Decimal,
            "invoice_count": int,
            "prescribed_count": int,
            "partial_prescribed_count": int,
            "tiers": set[str],
        }
    """
    _require_implemented_debtor_type(debtor_type)

    results = []
    total_comp_eur = Decimal("0")
    total_comp_pln = Decimal("0")
    total_interest = Decimal("0")
    total_civil_interest = Decimal("0")
    total_outstanding = Decimal("0")
    prescribed_count = 0
    partial_count = 0
    tiers = set()

    for inv in invoices:
        calc = calculate_invoice(
            gross=Decimal(str(inv["gross"])),
            due_date=inv["due_date"],
            payment_date=inv["payment_date"],
            lawsuit_date=lawsuit_date,
            cutoff_date=cutoff_date,
            debtor_type=debtor_type,
            payments=inv.get("payments"),
        )
        calc["invoice_number"] = inv.get("invoice_number", "")

        if calc["prescription_status"] == "PRZEDAWNIONE":
            prescribed_count += 1
        else:
            total_outstanding += calc["outstanding_pln"]
            total_comp_eur += calc["compensation"]["comp_eur"]
            total_comp_pln += calc["compensation"]["comp_pln"]
            total_interest += calc["interest"]
            total_civil_interest += calc["civil_interest"]
            tiers.add(calc["compensation"]["tier"])

            if calc["prescription_status"] == "CZESCIOWE_PRZEDAWNIENIE":
                partial_count += 1

        results.append(calc)

    # total_claim_pln / wps zachowują starą semantykę (comp + odsetki handlowe),
    # żeby nie łamać testów i existing readerów. Odsetki KC są OSOBNO
    # w total_civil_interest_pln; sumę z nimi robi caller.
    total_claim = total_comp_pln + total_interest
    wps = total_claim

    return {
        "invoices": results,
        "total_compensation_eur": total_comp_eur,
        "total_compensation_pln": total_comp_pln.quantize(Decimal("0.01")),
        "total_interest_pln": total_interest.quantize(Decimal("0.01")),
        "total_civil_interest_pln": total_civil_interest.quantize(Decimal("0.01")),
        "total_outstanding_pln": total_outstanding.quantize(Decimal("0.01")),
        "total_claim_pln": total_claim.quantize(Decimal("0.01")),
        "wps": wps.quantize(Decimal("0.01")),
        "court_fee": court_fee(wps),
        "legal_representation_cost": legal_representation_cost(wps),
        "invoice_count": len(invoices) - prescribed_count,
        "prescribed_count": prescribed_count,
        "partial_prescribed_count": partial_count,
        "tiers": tiers,
    }
