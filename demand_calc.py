#!/usr/bin/env python3
"""
rekompensa.pl — Kalkulator rekompensat i odsetek handlowych
Moduł reusable: importowany przez generator wezwań, generator pozwów, raport prospektowy.

Podstawa prawna:
    - Art. 7 ust. 1 ustawy z 8.03.2013 o przeciwdziałaniu nadmiernym opóźnieniom
      w transakcjach handlowych (t.j. Dz.U. 2023 poz. 1790) — odsetki
    - Art. 10 ust. 1 i 1a tamże — rekompensaty
    - Art. 118 KC — przedawnienie (3 lata + reguła końca roku)
"""

import warnings
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import holidays
import requests

# ═══════════════════════════════════════════════════════════════════════
# STAŁE
# ═══════════════════════════════════════════════════════════════════════

# Stawki odsetek ustawowych za opóźnienie w transakcjach handlowych
# (stopa referencyjna NBP + 10 p.p., art. 7 ust. 1)
# UWAGA: wymaga manualnej aktualizacji przy każdej zmianie stóp RPP.
INTEREST_RATES = [
    {"from": "2022-01-01", "to": "2022-06-30", "rate": 11.75},  # ref 1.75
    {"from": "2022-07-01", "to": "2022-12-31", "rate": 16.00},  # ref 6.00
    {"from": "2023-01-01", "to": "2023-06-30", "rate": 16.75},  # ref 6.75
    {"from": "2023-07-01", "to": "2023-12-31", "rate": 16.75},  # ref 6.75
    {"from": "2024-01-01", "to": "2024-06-30", "rate": 15.75},  # ref 5.75
    {"from": "2024-07-01", "to": "2024-12-31", "rate": 15.75},  # ref 5.75
    {"from": "2025-01-01", "to": "2025-06-30", "rate": 15.75},  # ref 5.75
    {"from": "2025-07-01", "to": "2025-12-31", "rate": 15.25},  # ref 5.25 (obniżka maj 2025)
    {"from": "2026-01-01", "to": "2026-06-30", "rate": 14.00},  # ref 4.00 (M.P. 2025 poz. 1257)
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
    """Art. 115 KC: jeśli termin wypada w dzień wolny, przesuwa na następny roboczy."""
    while d.weekday() >= 5 or d in PL_HOLIDAYS:
        d += timedelta(days=1)
    return d


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

def get_interest_rate(d: date) -> float:
    """Stawka odsetek handlowych obowiązująca w danym dniu."""
    for r in INTEREST_RATES:
        if r["from_d"] <= d <= r["to_d"]:
            return r["rate"]
    # Fallback: ostatnia znana stawka
    return INTEREST_RATES[-1]["rate"]


def calculate_interest(
    gross: Decimal,
    due_date: date,
    payment_date: date,
    interest_start_override: Optional[date] = None,
) -> Decimal:
    """
    Oblicza odsetki za opóźnienie w transakcji handlowej (art. 7 ust. 1).
    Naliczane dziennie, od dnia po terminie płatności do dnia zapłaty (włącznie).
    Uwzględnia zmiany stawki w trakcie okresu (podział na podokresy).

    Args:
        gross: kwota brutto faktury
        due_date: termin płatności (po korekcie art. 115 KC)
        payment_date: data faktycznej zapłaty
        interest_start_override: jeśli podany, odsetki liczone od tej daty
            (kroczące przedawnienie — odcięcie przedawnionych dni)

    Returns:
        Kwota odsetek w PLN, zaokrąglona do 2 miejsc.
    """
    if payment_date <= due_date:
        return Decimal("0")

    total = Decimal("0")
    current = interest_start_override if interest_start_override else (due_date + timedelta(days=1))

    if current > payment_date:
        return Decimal("0")

    while current <= payment_date:
        rate = get_interest_rate(current)

        # Koniec bieżącego podokresu stawki
        period_end = payment_date
        for r in INTEREST_RATES:
            if r["from_d"] <= current <= r["to_d"]:
                period_end = min(payment_date, r["to_d"])
                break

        days = (period_end - current).days + 1
        interest = gross * Decimal(str(rate)) / Decimal("100") * Decimal(str(days)) / Decimal("365")
        total += interest
        current = period_end + timedelta(days=1)

    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_interest_detailed(
    gross: Decimal,
    due_date: date,
    payment_date: date,
    interest_start_override: Optional[date] = None,
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
    result = {"total": Decimal("0"), "periods": [], "start_date": None, "end_date": None}

    if payment_date <= due_date:
        return result

    current = interest_start_override if interest_start_override else (due_date + timedelta(days=1))
    if current > payment_date:
        return result

    result["start_date"] = current
    result["end_date"] = payment_date

    while current <= payment_date:
        rate = get_interest_rate(current)

        period_end = payment_date
        for r in INTEREST_RATES:
            if r["from_d"] <= current <= r["to_d"]:
                period_end = min(payment_date, r["to_d"])
                break

        days = (period_end - current).days + 1
        amount = (gross * Decimal(str(rate)) / Decimal("100") * Decimal(str(days)) / Decimal("365")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        result["periods"].append({
            "from": current,
            "to": period_end,
            "days": days,
            "rate": rate,
            "amount": amount,
        })
        result["total"] += amount
        current = period_end + timedelta(days=1)

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
    """
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
    Dies a quo = due_date + 1.
    """
    claim_accrual = due_date + timedelta(days=1)
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
    """Czy roszczenie przedawni się w ciągu threshold_days (domyślnie ~6 miesięcy)?"""
    claim_accrual = due_date + timedelta(days=1)
    expiry = prescription_expiry_date(claim_accrual)
    days_left = (expiry - reference_date).days
    return 0 < days_left <= threshold_days


# ═══════════════════════════════════════════════════════════════════════
# OPŁATA SĄDOWA + KZP
# ═══════════════════════════════════════════════════════════════════════

def court_fee(wps: Decimal) -> Decimal:
    """
    Opłata sądowa wg art. 13 ust. 1 ustawy o kosztach sądowych
    (t.j. Dz.U. 2024 poz. 959).
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

    # > 20000: 5% WPS, zaokrąglone w górę do pełnego złotego, max 200 000
    fee = (wps * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
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

def calculate_invoice(
    gross: Decimal,
    due_date: date,
    payment_date: date,
    lawsuit_date: Optional[date] = None,
) -> dict:
    """
    Pełna kalkulacja dla jednej faktury: rekompensata + odsetki + przedawnienie.

    Args:
        gross: kwota brutto
        due_date: termin płatności (po art. 115 KC)
        payment_date: data zapłaty
        lawsuit_date: planowana data pozwu (None = bez filtra przedawnienia)

    Returns:
        {
            "compensation": {...},          # z calculate_compensation
            "interest": Decimal,            # kwota odsetek PLN
            "interest_detailed": {...},     # z calculate_interest_detailed
            "total_pln": Decimal,           # compensation_pln + interest
            "prescription_status": str,     # NIEPRZEDAWNIONE / CZESCIOWE / PRZEDAWNIONE
            "prescribed_days": int,
            "delay_days": int,
        }
    """
    result = {
        "delay_days": max(0, (payment_date - due_date).days),
        "prescription_status": "NIEPRZEDAWNIONE",
        "prescribed_days": 0,
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
        result["total_pln"] = Decimal("0")
        return result

    # Odsetki z ewentualnym kroczącym przedawnieniem
    adjusted_start = interest_start
    if lawsuit_date:
        earliest = find_earliest_non_prescribed_interest_date(interest_start, lawsuit_date)
        if earliest is None:
            adjusted_start = payment_date + timedelta(days=1)  # force zero
        elif earliest > interest_start:
            adjusted_start = earliest
            result["prescription_status"] = "CZESCIOWE_PRZEDAWNIENIE"
            result["prescribed_days"] = (earliest - interest_start).days

    result["interest_detailed"] = calculate_interest_detailed(
        gross, due_date, payment_date, interest_start_override=adjusted_start
    )
    result["interest"] = result["interest_detailed"]["total"]

    result["total_pln"] = result["compensation"]["comp_pln"] + result["interest"]

    return result


def calculate_batch(
    invoices: list[dict],
    lawsuit_date: Optional[date] = None,
) -> dict:
    """
    Kalkulacja batcha faktur.

    Args:
        invoices: lista dict z kluczami:
            - gross (Decimal): kwota brutto
            - due_date (date): termin płatności
            - payment_date (date): data zapłaty
            - invoice_number (str, optional): numer faktury
        lawsuit_date: planowana data pozwu

    Returns:
        {
            "invoices": [calculate_invoice result per invoice + invoice_number],
            "total_compensation_eur": Decimal,
            "total_compensation_pln": Decimal,
            "total_interest_pln": Decimal,
            "total_claim_pln": Decimal,
            "wps": Decimal,
            "court_fee": Decimal,
            "legal_representation_cost": Decimal,
            "invoice_count": int,
            "prescribed_count": int,
            "partial_prescribed_count": int,
            "tiers": set[str],
        }
    """
    results = []
    total_comp_eur = Decimal("0")
    total_comp_pln = Decimal("0")
    total_interest = Decimal("0")
    prescribed_count = 0
    partial_count = 0
    tiers = set()

    for inv in invoices:
        calc = calculate_invoice(
            gross=Decimal(str(inv["gross"])),
            due_date=inv["due_date"],
            payment_date=inv["payment_date"],
            lawsuit_date=lawsuit_date,
        )
        calc["invoice_number"] = inv.get("invoice_number", "")

        if calc["prescription_status"] == "PRZEDAWNIONE":
            prescribed_count += 1
        else:
            total_comp_eur += calc["compensation"]["comp_eur"]
            total_comp_pln += calc["compensation"]["comp_pln"]
            total_interest += calc["interest"]
            tiers.add(calc["compensation"]["tier"])

            if calc["prescription_status"] == "CZESCIOWE_PRZEDAWNIENIE":
                partial_count += 1

        results.append(calc)

    total_claim = total_comp_pln + total_interest
    wps = total_claim

    return {
        "invoices": results,
        "total_compensation_eur": total_comp_eur,
        "total_compensation_pln": total_comp_pln.quantize(Decimal("0.01")),
        "total_interest_pln": total_interest.quantize(Decimal("0.01")),
        "total_claim_pln": total_claim.quantize(Decimal("0.01")),
        "wps": wps.quantize(Decimal("0.01")),
        "court_fee": court_fee(wps),
        "legal_representation_cost": legal_representation_cost(wps),
        "invoice_count": len(invoices) - prescribed_count,
        "prescribed_count": prescribed_count,
        "partial_prescribed_count": partial_count,
        "tiers": tiers,
    }
