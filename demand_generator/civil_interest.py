"""
Odsetki ustawowe za opoznienie od rekompensaty (art. 481 par. 2 KC).

Modul reusable — niezalezny od calc.py i generator.py.
Podstawa: wyrok SO Bialystok VII Ga 377/25 z 28.11.2025 (sedzia Pawel Hempel).
"""

import warnings
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

# Stawki odsetek ustawowych za opoznienie z art. 481 par. 2 KC
# Wzor: stopa referencyjna NBP + 5,5 pp
# Stawki obowiazuja ex lege od dnia zmiany stopy NBP przez RPP
# Obwieszczenia Ministra Sprawiedliwosci (art. 481 par. 2^4 KC) maja charakter
# deklaratoryjny -- informuja o stawce, nie sa jej zrodlem.
#
# Walidacja LEX: 2026-04-08 (Maciej Tabert) -- 9/10 obwieszczeń zlokalizowanych
CIVIL_INTEREST_RATES = [
    (date(2022, 9, 8),  Decimal('12.25')),  # M.P. 2022 poz. 943  (obwieszczenie z 29.09.2022)
    (date(2023, 9, 7),  Decimal('11.50')),  # M.P. 2023 poz. 1061 (obwieszczenie z 27.09.2023)
    (date(2023, 10, 5), Decimal('11.25')),  # M.P. 2023 poz. 1123 (obwieszczenie z 16.10.2023)
    (date(2025, 5, 8),  Decimal('10.75')),  # M.P. 2025 poz. 540  (obwieszczenie z 30.05.2025)
    (date(2025, 7, 3),  Decimal('10.50')),  # M.P. 2025 poz. 685  (obwieszczenie z 22.07.2025)
    (date(2025, 9, 4),  Decimal('10.25')),  # M.P. 2025 poz. 1015 (obwieszczenie z 24.09.2025)
    (date(2025, 10, 9), Decimal('10.00')),  # M.P. 2025 poz. 1136 (obwieszczenie z 30.10.2025)
    (date(2025, 11, 6), Decimal('9.75')),   # M.P. 2025 poz. 1192 (obwieszczenie z 18.11.2025)
    (date(2025, 12, 4), Decimal('9.50')),   # M.P. 2025 poz. 1308 (obwieszczenie z 23.12.2025)
    (date(2026, 3, 5),  Decimal('9.25')),   # M.P. 2026 poz. 367 (obwieszczenie Ministra
                                            # Sprawiedliwosci z 08.04.2026, ogl. 10.04.2026).
                                            # Stopa ref. NBP 3,75% + 5,5 pp.
                                            # Data wejscia to 05.03.2026, a NIE data
                                            # obwieszczenia: stawka obowiazuje ex lege od dnia
                                            # zmiany stopy NBP (art. 481 par. 2 KC), a
                                            # obwieszczenie jest tylko deklaratoryjne.
]

# Data ostatniej weryfikacji tej tabeli u zrodla (M.P. przez ISAP / LEX).
#
# Tabela jest ZDARZENIOWA, nie okresowa: brak nowego wpisu znaczy "stopa
# referencyjna NBP sie nie zmienila", wiec niesienie ostatniej stawki w
# przyszlosc jest POPRAWNE. To inaczej niz INTEREST_RATES w calc.py, gdzie
# okresy sa domkniete przez art. 11b i luka = blad.
#
# Haczyk: poprawne niesienie jest nieodroznialne od tabeli przeterminowanej.
# Dlatego naliczanie za dzien pozniejszy niz LAST_VERIFIED_DATE +
# STALENESS_WARNING_DAYS ostrzega przez warnings.warn (stderr), zamiast
# rzucac wyjatkiem.
LAST_VERIFIED_DATE = date(2026, 7, 29)
STALENESS_WARNING_DAYS = 180


def _get_rate_for_date(d: date) -> Decimal:
    """Zwraca stawke odsetek KC obowiazujaca w danym dniu."""
    rate = None
    for effective_date, r in CIVIL_INTEREST_RATES:
        if d >= effective_date:
            rate = r
        else:
            break
    if rate is None:
        raise ValueError(
            f"compensation_interest_start_date ({d}) przed zakresem tabeli stawek; "
            f"rozszerz CIVIL_INTEREST_RATES o wczesniejsze stawki"
        )
    if d > LAST_VERIFIED_DATE + timedelta(days=STALENESS_WARNING_DAYS):
        warnings.warn(
            f"Tabela stawek KC (CIVIL_INTEREST_RATES) byla weryfikowana u zrodla "
            f"{LAST_VERIFIED_DATE.isoformat()}, a naliczasz odsetki za dzien "
            f"{d.isoformat()} — ponad {STALENESS_WARNING_DAYS} dni pozniej. "
            f"Uzyta stawka {rate}% moze byc nieaktualna. Sprawdz w Monitorze "
            f"Polskim obwieszczenia Ministra Sprawiedliwosci (art. 481 par. 2^4 KC), "
            f"czy stopa referencyjna NBP sie nie zmienila, i zaktualizuj "
            f"CIVIL_INTEREST_RATES oraz LAST_VERIFIED_DATE.",
            stacklevel=2,
        )
    return rate


def _iter_sub_periods(start_date: date, end_date: date):
    """
    Iteruje sub-okresy naliczania odsetki, dzielac na granicach zmian stawek.
    Yields (sub_start, sub_end, rate) where sub_end is inclusive.
    """
    current = start_date
    while current < end_date:
        rate = _get_rate_for_date(current)
        # Znajdz koniec biezacego sub-okresu: najblizszą zmiane stawki lub end_date
        sub_end = end_date
        for effective_date, _ in CIVIL_INTEREST_RATES:
            if effective_date > current:
                if effective_date < sub_end:
                    sub_end = effective_date
                break
        yield current, sub_end, rate
        current = sub_end


def calculate_civil_interest(
    amount_pln: Decimal,
    start_date: date,
    end_date: date,
) -> Decimal:
    """
    Oblicza odsetki ustawowe za opoznienie (art. 481 par. 2 KC) od kwoty rekompensaty.

    Args:
        amount_pln: kwota rekompensaty w PLN (baza naliczania)
        start_date: dzien rozpoczecia naliczania (invoice_due_date + 1)
        end_date: dzien zakonczenia naliczania (cutoff_date, domyslnie today)

    Returns:
        Decimal zaokraglony do 2 miejsc (ROUND_HALF_UP)
    """
    if amount_pln == Decimal('0'):
        return Decimal('0')
    if start_date >= end_date:
        return Decimal('0')

    # Validate start_date is within rate table range
    _get_rate_for_date(start_date)

    total = Decimal('0')
    for sub_start, sub_end, rate in _iter_sub_periods(start_date, end_date):
        days = (sub_end - sub_start).days
        if days <= 0:
            continue
        # amount_pln * (rate / 100) * days / 365
        sub_interest = amount_pln * rate * Decimal(str(days)) / (Decimal('100') * Decimal('365'))
        total += sub_interest

    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
