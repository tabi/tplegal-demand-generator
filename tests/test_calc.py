#!/usr/bin/env python3
"""
Unit testy dla demand_generator.calc — kalkulator rekompensat i odsetek handlowych.
Pokrywa: odsetki, rekompensaty, przedawnienie, opłaty sądowe, KZP, batch.

Uruchomienie:
    python -m pytest tests/test_calc.py -v
"""

import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from demand_generator.calc import (
    INTEREST_RATES,
    DebtorType,
    UnknownRatePeriodError,
    compute_effective_due_date,
    next_business_day,
    last_business_day_of_month,
    get_interest_rate,
    calculate_interest,
    calculate_interest_detailed,
    get_nbp_eur_rate,
    get_compensation_eur_rate_date,
    get_compensation_tier,
    calculate_compensation,
    prescription_expiry_date,
    is_fully_prescribed,
    find_earliest_non_prescribed_interest_date,
    check_near_expiry,
    court_fee,
    legal_representation_cost,
    calculate_invoice,
    calculate_batch,
    calculate_civil_interest_for_invoice,
)


# ═══════════════════════════════════════════════════════════════════════
# DNI ROBOCZE
# ═══════════════════════════════════════════════════════════════════════

class TestNextBusinessDay:
    def test_weekday_unchanged(self):
        """Poniedziałek -> poniedziałek."""
        d = date(2025, 3, 17)  # Monday
        assert next_business_day(d) == d

    def test_saturday_to_monday(self):
        """Sobota -> poniedziałek."""
        d = date(2025, 3, 15)  # Saturday
        assert next_business_day(d) == date(2025, 3, 17)

    def test_sunday_to_monday(self):
        """Niedziela -> poniedziałek."""
        d = date(2025, 3, 16)  # Sunday
        assert next_business_day(d) == date(2025, 3, 17)

    def test_holiday_skipped(self):
        """1 stycznia (święto) -> pierwszy dzień roboczy po."""
        d = date(2025, 1, 1)  # Nowy Rok — środa
        result = next_business_day(d)
        assert result == date(2025, 1, 2)

    def test_holiday_on_monday(self):
        """Poniedziałek Wielkanocny -> wtorek."""
        # 2025: Poniedziałek Wielkanocny = 21.04
        d = date(2025, 4, 21)
        result = next_business_day(d)
        assert result == date(2025, 4, 22)  # wtorek


class TestLastBusinessDayOfMonth:
    def test_regular_month(self):
        """Marzec 2025 — 31 to poniedziałek, więc 31."""
        result = last_business_day_of_month(2025, 3)
        assert result == date(2025, 3, 31)

    def test_month_ending_weekend(self):
        """Luty 2025 — 28 to piątek."""
        result = last_business_day_of_month(2025, 2)
        assert result == date(2025, 2, 28)

    def test_december(self):
        """Grudzień 2025 — 31 to środa."""
        result = last_business_day_of_month(2025, 12)
        assert result == date(2025, 12, 31)


# ═══════════════════════════════════════════════════════════════════════
# ODSETKI
# ═══════════════════════════════════════════════════════════════════════

class TestGetInterestRate:
    def test_known_rate_2024(self):
        assert get_interest_rate(date(2024, 6, 15)) == 15.75

    def test_known_rate_2026(self):
        assert get_interest_rate(date(2026, 3, 1)) == 14.00

    def test_known_rate_h2_2026(self):
        """M.P. 2026 poz. 642 — 13,75% dla całego II półrocza 2026."""
        assert get_interest_rate(date(2026, 7, 1)) == 13.75
        assert get_interest_rate(date(2026, 12, 31)) == 13.75

    def test_rate_frozen_across_h1_2026(self):
        """Art. 11b: obniżka stopy NBP 5.03.2026 NIE zmienia stawki w półroczu."""
        assert get_interest_rate(date(2026, 3, 4)) == 14.00
        assert get_interest_rate(date(2026, 3, 5)) == 14.00
        assert get_interest_rate(date(2026, 6, 30)) == 14.00

    def test_date_after_table_raises(self):
        """Data po ostatnim okresie tabeli -> głośny błąd, nie cicha stawka.

        Poprzednia wersja tego testu asertowała tylko `rate > 0` i tym samym
        utrwalała cichy fallback, przez który brak wiersza na II półrocze 2026
        przeszedł niezauważony.
        """
        with pytest.raises(UnknownRatePeriodError) as exc:
            get_interest_rate(date(2030, 1, 1))
        msg = str(exc.value)
        assert "2030-01-01" in msg
        assert INTEREST_RATES[-1]["to"] in msg
        assert "INTEREST_RATES" in msg

    def test_last_known_day_still_resolves(self):
        """Ostatni dzień ostatniego okresu nadal się rozwiązuje (brak off-by-one)."""
        last = INTEREST_RATES[-1]
        assert get_interest_rate(last["to_d"]) == last["rate"]


class TestArt115WKalkulatorze:
    """Art. 115 KC nakładany WEWNĄTRZ kalkulatora (decyzja 30.07.2026).

    Zmiana lustrzana do toru A (rekompensa/calculator/calc.py w tplegal-tools).
    Oba tory muszą liczyć tak samo, bo wezwanie ad hoc z Claude Desktop i wezwanie
    z pipeline'u bazodanowego trafiają do tej samej sprawy.
    """

    def test_next_business_day_jest_idempotentny(self):
        """Fundament naprawy: podwójna korekta nie zmienia wyniku."""
        for d in (date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 12), date(2026, 1, 6)):
            once = compute_effective_due_date(d)
            assert compute_effective_due_date(once) == once
            assert once.weekday() < 5

    def test_odsetki_od_soboty_rowne_odsetkom_od_poniedzialku(self):
        sobota, poniedzialek, paid = date(2026, 1, 10), date(2026, 1, 12), date(2026, 7, 20)
        assert calculate_interest(Decimal("100000"), sobota, paid) == calculate_interest(
            Decimal("100000"), poniedzialek, paid
        )

    def test_odsetki_startuja_dzien_po_terminie_efektywnym(self):
        detailed = calculate_interest_detailed(
            Decimal("100000"), date(2026, 1, 10), date(2026, 7, 20)
        )
        assert detailed["start_date"] == date(2026, 1, 13)

    def test_zaplata_w_pierwszy_dzien_roboczy_daje_zero(self):
        assert calculate_interest(
            Decimal("100000"), date(2026, 1, 10), date(2026, 1, 12)
        ) == Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.2267"))
    def test_delay_days_od_terminu_efektywnego(self, _mock_rate):
        inv = calculate_invoice(
            Decimal("100000"), date(2026, 1, 10), date(2026, 7, 20),
            cutoff_date=date(2026, 7, 20),
        )
        assert inv["delay_days"] == 189

    def test_kurs_nbp_z_miesiaca_po_korekcie(self):
        """31.01.2026 to sobota → wymagalność 02.02 → kurs z 30.01.2026."""
        assert get_compensation_eur_rate_date(date(2026, 1, 31)) == date(2026, 1, 30)

    def test_odsetki_kc_od_terminu_efektywnego(self):
        od_soboty = calculate_civil_interest_for_invoice(
            Decimal("1000"), date(2026, 1, 10), date(2026, 7, 20)
        )
        od_poniedzialku = calculate_civil_interest_for_invoice(
            Decimal("1000"), date(2026, 1, 12), date(2026, 7, 20)
        )
        assert od_soboty == od_poniedzialku


class TestDebtorType:
    """Dłużnik publiczny: art. 8 ust. 1 + stawka +8 p.p. dla podmiotu leczniczego."""

    def test_rate_medical_o_dwa_punkty_nizsza(self):
        """Inwariant tabeli: +8 p.p. to zawsze +10 p.p. minus 2 p.p."""
        for r in INTEREST_RATES:
            assert round(r["rate"] - r["rate_medical"], 2) == 2.00, r["from"]

    def test_h2_2026_wprost_z_obwieszczenia(self):
        """M.P. 2026 poz. 642 podaje 13,75% i 11,75%."""
        assert get_interest_rate(date(2026, 7, 1), DebtorType.PRIVATE) == 13.75
        assert get_interest_rate(date(2026, 7, 1), DebtorType.PUBLIC_MEDICAL) == 11.75

    def test_publiczny_niemedyczny_ma_stawke_standardowa(self):
        """Art. 4 pkt 3 lit. b — gmina: +10 p.p., jak prywatny."""
        assert get_interest_rate(
            date(2026, 7, 1), DebtorType.PUBLIC_NON_MEDICAL
        ) == get_interest_rate(date(2026, 7, 1), DebtorType.PRIVATE)

    def test_domyslnie_prywatny(self):
        assert get_interest_rate(date(2026, 7, 1)) == 13.75

    @pytest.mark.parametrize(
        "debtor_type",
        [DebtorType.PUBLIC_NON_MEDICAL, DebtorType.PUBLIC_MEDICAL],
    )
    def test_dluznik_publiczny_rzuca_not_implemented(self, debtor_type):
        """Reżim art. 8 niekompletny — brak limitu terminu z art. 8 ust. 2/4a."""
        for fn in (calculate_interest, calculate_interest_detailed, calculate_invoice):
            with pytest.raises(NotImplementedError) as exc:
                fn(
                    Decimal("1000"), date(2026, 1, 9), date(2026, 3, 1),
                    debtor_type=debtor_type,
                )
            msg = str(exc.value)
            assert "art. 8 ust. 2/4a" in msg
            assert "doręczenia" in msg

    def test_batch_publiczny_rzuca_not_implemented(self):
        with pytest.raises(NotImplementedError):
            calculate_batch(
                [{
                    "gross": Decimal("1000"),
                    "due_date": date(2026, 1, 9),
                    "payment_date": date(2026, 3, 1),
                }],
                debtor_type=DebtorType.PUBLIC_MEDICAL,
            )

    def test_prywatny_liczy_normalnie(self):
        assert calculate_interest(
            Decimal("100000"), date(2026, 1, 9), date(2026, 7, 20),
            debtor_type=DebtorType.PRIVATE,
        ) > Decimal("0")


class TestCalculateInterest:
    def test_no_delay(self):
        """Zapłata w terminie -> odsetki 0."""
        result = calculate_interest(
            Decimal("10000"), date(2024, 6, 1), date(2024, 6, 1)
        )
        assert result == Decimal("0")

    def test_early_payment(self):
        """Zapłata przed terminem -> odsetki 0."""
        result = calculate_interest(
            Decimal("10000"), date(2024, 6, 15), date(2024, 6, 1)
        )
        assert result == Decimal("0")

    def test_one_day_late(self):
        """1 dzień opóźnienia -> minimalne odsetki.

        Termin 03.06.2024 (poniedziałek) — poprzednio fixture miał 01.06.2024,
        czyli SOBOTĘ, więc po wejściu art. 115 KC do kalkulatora zapłata 02.06
        przestała być opóźnieniem. Fixture był niepoprawny, semantyka nie.
        """
        result = calculate_interest(
            Decimal("10000"), date(2024, 6, 3), date(2024, 6, 4)
        )
        assert result > Decimal("0")
        # 10000 * 15.75% / 365 * 1 ≈ 4.32
        assert Decimal("4") < result < Decimal("5")

    def test_30_days_late(self):
        """30 dni opóźnienia, stała stawka."""
        result = calculate_interest(
            Decimal("50000"), date(2024, 3, 1), date(2024, 3, 31)
        )
        # 50000 * 15.75% / 365 * 30 ≈ 647.26
        assert Decimal("600") < result < Decimal("700")

    def test_cross_rate_boundary(self):
        """Odsetki przez granicę półrocza — asercja WARTOŚCI, nie samo "> 0".

        Test kontrolny: 100 000 zł, due_date 2026-01-12, payment_date 2026-07-20.
        Art. 11b dzieli okres na 30.06/01.07, więc muszą wyjść DWA podokresy:
            169 dni x 14,00% (M.P. 2025 poz. 1257) = 6 482,19
             20 dni x 13,75% (M.P. 2026 poz. 642)  =   753,42
        Przed naprawą tabeli drugi podokres leciał po 14,00%, i żaden test tego
        nie łapał.

        Fixture miał wcześniej due_date 2026-01-10 — SOBOTĘ. Po wejściu art. 115 KC
        do kalkulatora taki termin nie może być terminem efektywnym, więc fixture
        był niepoprawny; 12.01.2026 to dokładnie next_business_day(2026-01-10),
        czyli ten sam przypadek liczony od daty, na którą wskazuje ustawa.
        """
        detailed = calculate_interest_detailed(
            Decimal("100000"), date(2026, 1, 12), date(2026, 7, 20)
        )
        assert [
            (p["from"], p["to"], p["days"], p["rate"]) for p in detailed["periods"]
        ] == [
            (date(2026, 1, 13), date(2026, 6, 30), 169, 14.00),
            (date(2026, 7, 1), date(2026, 7, 20), 20, 13.75),
        ]
        assert [p["amount"] for p in detailed["periods"]] == [
            Decimal("6482.19"),
            Decimal("753.42"),
        ]
        # Kanon produkcyjny: calculate_batch -> calculate_interest_detailed,
        # czyli suma zaokrąglonych podokresów. Tabela w wezwaniu musi się spinać.
        assert detailed["total"] == Decimal("7235.61")
        assert sum(p["amount"] for p in detailed["periods"]) == detailed["total"]

    def test_rounding_divergence_between_both_functions(self):
        """UWAGA — rozjazd WEWNĄTRZ modułu, świadomie zapięty testem.

        calculate_interest() sumuje kwoty surowe i zaokrągla RAZ na końcu, a
        calculate_interest_detailed() sumuje kwoty już zaokrąglone per podokres.
        Na tym samym wejściu daje to różnicę jednego grosza. Ścieżka produkcyjna
        (calc-rekompensa -> calculate_batch -> ..._detailed) używa wariantu
        sumującego zaokrąglone, więc to ON jest kanonem.

        Ten test istnieje, żeby rozjazd był WIDOCZNY, a nie żeby go błogosławić —
        do ujednolicenia razem z rekompensa-tools.
        """
        args = (Decimal("100000"), date(2026, 1, 12), date(2026, 7, 20))
        assert calculate_interest(*args) == Decimal("7235.62")
        assert calculate_interest_detailed(*args)["total"] == Decimal("7235.61")

    def test_interest_start_override(self):
        """Override startu odsetek (kroczące przedawnienie)."""
        full = calculate_interest(
            Decimal("10000"), date(2024, 1, 1), date(2024, 6, 30)
        )
        partial = calculate_interest(
            Decimal("10000"), date(2024, 1, 1), date(2024, 6, 30),
            interest_start_override=date(2024, 4, 1),
        )
        assert partial < full
        assert partial > Decimal("0")

    def test_override_after_payment(self):
        """Override po dacie zapłaty -> 0."""
        result = calculate_interest(
            Decimal("10000"), date(2024, 1, 1), date(2024, 3, 1),
            interest_start_override=date(2024, 6, 1),
        )
        assert result == Decimal("0")


class TestCalculateInterestDetailed:
    def test_returns_periods(self):
        result = calculate_interest_detailed(
            Decimal("10000"), date(2024, 1, 1), date(2024, 3, 1)
        )
        assert "total" in result
        assert "periods" in result
        assert len(result["periods"]) >= 1
        assert result["total"] > Decimal("0")

    def test_period_amounts_sum_to_total(self):
        result = calculate_interest_detailed(
            Decimal("50000"), date(2024, 1, 1), date(2024, 12, 31)
        )
        period_sum = sum(p["amount"] for p in result["periods"])
        # Dopuszczam 1 grosz różnicy z zaokrągleń
        assert abs(period_sum - result["total"]) <= Decimal("0.01")


# ═══════════════════════════════════════════════════════════════════════
# KURSY NBP
# ═══════════════════════════════════════════════════════════════════════

class TestNBPRates:
    def test_compensation_eur_rate_date_regular(self):
        """Art. 10 ust. 1a: kurs z ostatniego dnia roboczego POPRZEDNIEGO miesiąca."""
        # Wymagalność w marcu -> kurs z lutego
        rate_date = get_compensation_eur_rate_date(date(2025, 3, 15))
        assert rate_date.month == 2
        assert rate_date.year == 2025

    def test_compensation_eur_rate_date_january(self):
        """Wymagalność w styczniu -> kurs z grudnia poprzedniego roku."""
        rate_date = get_compensation_eur_rate_date(date(2025, 1, 10))
        assert rate_date.month == 12
        assert rate_date.year == 2024

    @patch("demand_generator.calc.requests.get")
    def test_nbp_fallback_on_error(self, mock_get):
        """Jeśli API NBP niedostępne -> fallback 4.30."""
        from demand_generator.calc import _nbp_cache
        # Wyczyść cache dla daty testowej
        test_date = date(2099, 1, 15)
        for offset in range(6):
            key = (test_date - timedelta(days=offset)).isoformat()
            _nbp_cache.pop(key, None)

        mock_get.side_effect = Exception("Network error")
        rate = get_nbp_eur_rate(test_date)
        assert rate == Decimal("4.30")


# ═══════════════════════════════════════════════════════════════════════
# REKOMPENSATY (art. 10)
# ═══════════════════════════════════════════════════════════════════════

class TestCompensationTier:
    def test_tier_40_below_5000(self):
        amount, tier = get_compensation_tier(Decimal("4999.99"))
        assert amount == Decimal("40")
        assert tier == "EUR_40"

    def test_tier_40_exactly_5000(self):
        amount, tier = get_compensation_tier(Decimal("5000"))
        assert amount == Decimal("40")
        assert tier == "EUR_40"

    def test_tier_70_above_5000(self):
        amount, tier = get_compensation_tier(Decimal("5000.01"))
        assert amount == Decimal("70")
        assert tier == "EUR_70"

    def test_tier_70_below_50000(self):
        amount, tier = get_compensation_tier(Decimal("49999.99"))
        assert amount == Decimal("70")
        assert tier == "EUR_70"

    def test_tier_100_exactly_50000(self):
        """Art. 10 ust. 1 pkt 3: >= 50000 -> 100 EUR."""
        amount, tier = get_compensation_tier(Decimal("50000"))
        assert amount == Decimal("100")
        assert tier == "EUR_100"

    def test_tier_100_large_amount(self):
        amount, tier = get_compensation_tier(Decimal("500000"))
        assert amount == Decimal("100")
        assert tier == "EUR_100"

    def test_tier_40_small_invoice(self):
        amount, tier = get_compensation_tier(Decimal("100"))
        assert amount == Decimal("40")
        assert tier == "EUR_40"


class TestCalculateCompensation:
    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_basic_compensation(self, mock_rate):
        result = calculate_compensation(Decimal("3000"), date(2024, 6, 15))
        assert result["comp_eur"] == Decimal("40")
        assert result["tier"] == "EUR_40"
        assert result["comp_pln"] == Decimal("172.00")  # 40 * 4.30

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_tier_70_compensation(self, mock_rate):
        result = calculate_compensation(Decimal("25000"), date(2024, 6, 15))
        assert result["comp_eur"] == Decimal("70")
        assert result["comp_pln"] == Decimal("301.00")  # 70 * 4.30


# ═══════════════════════════════════════════════════════════════════════
# PRZEDAWNIENIE (art. 118 KC)
# ═══════════════════════════════════════════════════════════════════════

class TestPrescription:
    def test_expiry_date_basic(self):
        """3 lata + koniec roku."""
        expiry = prescription_expiry_date(date(2022, 3, 15))
        assert expiry == date(2025, 12, 31)

    def test_expiry_date_january(self):
        expiry = prescription_expiry_date(date(2022, 1, 1))
        assert expiry == date(2025, 12, 31)

    def test_expiry_date_december(self):
        expiry = prescription_expiry_date(date(2022, 12, 31))
        assert expiry == date(2025, 12, 31)

    def test_not_prescribed(self):
        """Faktura z 2023, pozew w 2025 -> nie przedawniona."""
        assert not is_fully_prescribed(date(2023, 6, 1), date(2025, 6, 1))

    def test_fully_prescribed(self):
        """Faktura z 2020, pozew w 2025 -> przedawniona."""
        # dies a quo = 2020-06-02, expiry = 2023-12-31
        assert is_fully_prescribed(date(2020, 6, 1), date(2024, 1, 1))

    def test_prescribed_boundary_last_day(self):
        """Pozew w ostatnim dniu -> NIE przedawnione."""
        # dies a quo = 2022-01-02, expiry = 2025-12-31
        assert not is_fully_prescribed(date(2022, 1, 1), date(2025, 12, 31))

    def test_prescribed_boundary_next_day(self):
        """Pozew dzień po -> przedawnione."""
        assert is_fully_prescribed(date(2022, 1, 1), date(2026, 1, 1))


class TestRollingPrescription:
    def test_no_prescription(self):
        """Wszystko w terminie -> zwraca oryginalny start."""
        result = find_earliest_non_prescribed_interest_date(
            date(2024, 1, 2), date(2025, 6, 1)
        )
        assert result == date(2024, 1, 2)

    def test_partial_prescription(self):
        """Część odsetek przedawniona -> zwraca późniejszą datę."""
        # interest_start = 2021-01-02, lawsuit = 2025-06-01
        # 2021: expiry = 2024-12-31 -> lawsuit > expiry -> 2021 prescribed
        # 2022: expiry = 2025-12-31 -> lawsuit <= expiry -> 2022 not prescribed
        result = find_earliest_non_prescribed_interest_date(
            date(2021, 1, 2), date(2025, 6, 1)
        )
        assert result == date(2022, 1, 1)

    def test_all_prescribed(self):
        """Wszystko przedawnione -> None."""
        result = find_earliest_non_prescribed_interest_date(
            date(2015, 1, 2), date(2025, 6, 1)
        )
        assert result is None


class TestNearExpiry:
    def test_near_expiry_true(self):
        """Faktura przedawnia się za 3 miesiące -> True."""
        # due_date = 2022-06-01, dies a quo = 2022-06-02, expiry = 2025-12-31
        assert check_near_expiry(date(2022, 6, 1), date(2025, 9, 1))

    def test_near_expiry_false(self):
        """Faktura przedawnia się za 2 lata -> False."""
        assert not check_near_expiry(date(2023, 6, 1), date(2025, 1, 1))

    def test_already_expired(self):
        """Już przedawniona -> False (0 < days_left jest fałszywe)."""
        assert not check_near_expiry(date(2020, 1, 1), date(2025, 1, 1))


# ═══════════════════════════════════════════════════════════════════════
# OPŁATA SĄDOWA + KZP
# ═══════════════════════════════════════════════════════════════════════

class TestCourtFee:
    def test_small_claim(self):
        assert court_fee(Decimal("300")) == Decimal("30")

    def test_bracket_500(self):
        assert court_fee(Decimal("500")) == Decimal("30")

    def test_bracket_1500(self):
        assert court_fee(Decimal("1500")) == Decimal("100")

    def test_bracket_4000(self):
        assert court_fee(Decimal("4000")) == Decimal("200")

    def test_bracket_20000(self):
        assert court_fee(Decimal("20000")) == Decimal("1000")

    def test_above_20000_five_percent(self):
        """> 20000: 5% zaokrąglone w górę."""
        fee = court_fee(Decimal("100000"))
        assert fee == Decimal("5000")

    def test_max_200000(self):
        """Max opłata = 200 000 PLN."""
        fee = court_fee(Decimal("10000000"))
        assert fee == Decimal("200000")


class TestLegalRepresentationCost:
    def test_small_claim(self):
        assert legal_representation_cost(Decimal("300")) == Decimal("90")

    def test_mid_claim(self):
        assert legal_representation_cost(Decimal("8000")) == Decimal("1800")

    def test_large_claim(self):
        assert legal_representation_cost(Decimal("100000")) == Decimal("5400")

    def test_very_large_claim(self):
        assert legal_representation_cost(Decimal("10000000")) == Decimal("25000")


# ═══════════════════════════════════════════════════════════════════════
# KALKULACJA ŁĄCZNA
# ═══════════════════════════════════════════════════════════════════════

class TestCalculateInvoice:
    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_basic_invoice(self, mock_rate):
        result = calculate_invoice(
            Decimal("10000"), date(2024, 3, 1), date(2024, 4, 1)
        )
        assert result["delay_days"] == 31
        assert result["prescription_status"] == "NIEPRZEDAWNIONE"
        assert result["compensation"]["comp_eur"] == Decimal("70")  # 10000 > 5000 -> tier 70
        assert result["interest"] > Decimal("0")
        assert result["total_pln"] == result["compensation"]["comp_pln"] + result["interest"]

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_prescribed_invoice(self, mock_rate):
        result = calculate_invoice(
            Decimal("10000"), date(2020, 1, 1), date(2020, 6, 1),
            lawsuit_date=date(2025, 6, 1),
        )
        assert result["prescription_status"] == "PRZEDAWNIONE"
        assert result["interest"] == Decimal("0")
        assert result["compensation"]["comp_pln"] == Decimal("0")
        assert result["total_pln"] == Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_no_lawsuit_date_no_prescription(self, mock_rate):
        """Bez lawsuit_date -> nie sprawdza przedawnienia."""
        result = calculate_invoice(
            Decimal("10000"), date(2020, 1, 1), date(2020, 6, 1)
        )
        assert result["prescription_status"] == "NIEPRZEDAWNIONE"
        assert result["interest"] > Decimal("0")


class TestCalculateBatch:
    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_basic_batch(self, mock_rate):
        invoices = [
            {"gross": 3000, "due_date": date(2024, 3, 1), "payment_date": date(2024, 4, 1), "invoice_number": "FV/1"},
            {"gross": 8000, "due_date": date(2024, 4, 1), "payment_date": date(2024, 5, 1), "invoice_number": "FV/2"},
        ]
        result = calculate_batch(invoices)
        assert result["invoice_count"] == 2
        assert result["prescribed_count"] == 0
        assert result["total_compensation_eur"] > Decimal("0")
        assert result["total_interest_pln"] > Decimal("0")
        assert result["total_claim_pln"] == result["total_compensation_pln"] + result["total_interest_pln"]
        assert result["court_fee"] > Decimal("0")
        assert result["legal_representation_cost"] > Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_batch_with_prescribed(self, mock_rate):
        invoices = [
            {"gross": 3000, "due_date": date(2024, 3, 1), "payment_date": date(2024, 4, 1), "invoice_number": "FV/1"},
            {"gross": 5000, "due_date": date(2020, 1, 1), "payment_date": date(2020, 2, 1), "invoice_number": "FV/old"},
        ]
        result = calculate_batch(invoices, lawsuit_date=date(2025, 6, 1))
        assert result["invoice_count"] == 1  # only non-prescribed
        assert result["prescribed_count"] == 1

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_batch_tiers(self, mock_rate):
        invoices = [
            {"gross": 3000, "due_date": date(2024, 3, 1), "payment_date": date(2024, 4, 1)},
            {"gross": 25000, "due_date": date(2024, 3, 1), "payment_date": date(2024, 4, 1)},
            {"gross": 80000, "due_date": date(2024, 3, 1), "payment_date": date(2024, 4, 1)},
        ]
        result = calculate_batch(invoices)
        assert "EUR_40" in result["tiers"]
        assert "EUR_70" in result["tiers"]
        assert "EUR_100" in result["tiers"]

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_empty_batch(self, mock_rate):
        result = calculate_batch([])
        assert result["invoice_count"] == 0
        assert result["total_claim_pln"] == Decimal("0")


# ═══════════════════════════════════════════════════════════════════════
# PAYMENT_DATE OPTIONAL (calc_cli)
# ═══════════════════════════════════════════════════════════════════════

class TestPaymentDateOptional:
    """Testy dla opcjonalnego payment_date w calc_cli."""

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_no_payment_date_uses_today(self, mock_rate):
        """JSON bez payment_date → calc używa today."""
        invoices = [{
            "gross": Decimal("10000"),
            "due_date": date(2024, 6, 1),
            "payment_date": date.today(),
        }]
        result = calculate_batch(invoices)
        assert result["total_interest_pln"] > Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_payment_date_null_uses_today(self, mock_rate):
        """JSON z payment_date = null → calc używa today (tested via calc_cli parsing)."""
        # This tests the calc_cli parsing logic indirectly
        # by verifying calculate_batch works with today's date
        today = date.today()
        invoices = [{
            "gross": Decimal("10000"),
            "due_date": date(2024, 6, 1),
            "payment_date": today,
        }]
        result = calculate_batch(invoices)
        assert result["invoice_count"] == 1
        assert result["total_interest_pln"] > Decimal("0")

    def test_calc_cli_parses_missing_payment_date(self, tmp_path):
        """calc_cli: JSON bez payment_date nie powoduje błędu."""
        import subprocess
        import sys

        json_data = {
            "invoices": [{
                "invoice_number": "FV/TEST/1",
                "gross": 10000,
                "due_date": "2024-06-01"
            }]
        }
        json_file = tmp_path / "test_no_payment.json"
        json_file.write_text(json.dumps(json_data))

        result = subprocess.run(
            [sys.executable, "-m", "demand_generator.calc_cli", "--json", str(json_file)],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["invoice_count"] == 1
        assert output["total_interest_pln"] > 0
        # backwards-compat sanity: total_civil_interest_pln musi być wystawiane
        assert "total_civil_interest_pln" in output

    def test_calc_cli_parses_null_payment_date(self, tmp_path):
        """calc_cli: JSON z payment_date=null nie powoduje błędu."""
        import subprocess
        import sys

        json_data = {
            "invoices": [{
                "invoice_number": "FV/TEST/2",
                "gross": 10000,
                "due_date": "2024-06-01",
                "payment_date": None
            }]
        }
        json_file = tmp_path / "test_null_payment.json"
        json_file.write_text(json.dumps(json_data))

        result = subprocess.run(
            [sys.executable, "-m", "demand_generator.calc_cli", "--json", str(json_file)],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["invoice_count"] == 1
        assert output["total_interest_pln"] > 0


# ═══════════════════════════════════════════════════════════════════════
# ODSETKI KC OD REKOMPENSATY (art. 481 § 2 KC) — integracja w calc.py
# ═══════════════════════════════════════════════════════════════════════

class TestCivilInterestForInvoice:
    def test_zero_when_compensation_zero(self):
        result = calculate_civil_interest_for_invoice(
            Decimal("0"), date(2023, 6, 1), date(2026, 4, 15)
        )
        assert result == Decimal("0")

    def test_positive_when_valid_period(self):
        # 275 PLN rekompensaty, 6+ miesięcy naliczania
        result = calculate_civil_interest_for_invoice(
            Decimal("275"), date(2023, 6, 1), date(2024, 1, 1)
        )
        assert result > Decimal("0")

    def test_default_cutoff_is_today(self):
        # cutoff=None -> date.today()
        result = calculate_civil_interest_for_invoice(
            Decimal("275"), date(2023, 6, 1)
        )
        assert result > Decimal("0")

    def test_zero_when_due_date_in_future(self):
        future = date.today() + timedelta(days=365)
        result = calculate_civil_interest_for_invoice(
            Decimal("275"), future
        )
        assert result == Decimal("0")


class TestCalculateInvoiceCivilInterest:
    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_invoice_result_has_civil_interest_key(self, mock_rate):
        calc = calculate_invoice(
            gross=Decimal("10000"),
            due_date=date(2023, 6, 1),
            payment_date=date(2023, 8, 1),
            cutoff_date=date(2024, 1, 1),
        )
        assert "civil_interest" in calc
        assert isinstance(calc["civil_interest"], Decimal)
        assert calc["civil_interest"] > Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_total_pln_excludes_civil_interest(self, mock_rate):
        # Kontrakt: total_pln = comp + interest handlowe (BEZ civil_interest).
        # Civil_interest jest osobnym polem — caller sumuje ręcznie.
        calc = calculate_invoice(
            gross=Decimal("10000"),
            due_date=date(2023, 6, 1),
            payment_date=date(2023, 8, 1),
            cutoff_date=date(2024, 1, 1),
        )
        assert calc["total_pln"] == calc["compensation"]["comp_pln"] + calc["interest"]
        assert calc["civil_interest"] > Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_prescribed_invoice_zero_civil_interest(self, mock_rate):
        # Faktura sprzed 10 lat + lawsuit_date dziś -> w pełni przedawnione
        calc = calculate_invoice(
            gross=Decimal("10000"),
            due_date=date(2015, 6, 1),
            payment_date=date(2015, 9, 1),
            lawsuit_date=date(2026, 4, 15),
        )
        assert calc["prescription_status"] == "PRZEDAWNIONE"
        assert calc["civil_interest"] == Decimal("0")


class TestCalculateBatchCivilInterest:
    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_batch_has_total_civil_interest_pln(self, mock_rate):
        invoices = [
            {"gross": 10000, "due_date": date(2023, 6, 1), "payment_date": date(2023, 8, 1)},
            {"gross": 60000, "due_date": date(2024, 1, 15), "payment_date": date(2024, 3, 1)},
        ]
        result = calculate_batch(invoices, cutoff_date=date(2026, 4, 15))
        assert "total_civil_interest_pln" in result
        assert result["total_civil_interest_pln"] > Decimal("0")

    @patch("demand_generator.calc.get_nbp_eur_rate", return_value=Decimal("4.30"))
    def test_total_claim_excludes_civil_interest(self, mock_rate):
        # Kontrakt: total_claim_pln = comp + interest handlowe (BEZ civil).
        # total_civil_interest_pln jest osobnym polem — generator sumuje w combined.
        invoices = [
            {"gross": 10000, "due_date": date(2023, 6, 1), "payment_date": date(2023, 8, 1)},
        ]
        result = calculate_batch(invoices, cutoff_date=date(2026, 4, 15))
        assert result["total_claim_pln"] == (
            result["total_compensation_pln"] + result["total_interest_pln"]
        )
        assert result["total_civil_interest_pln"] > Decimal("0")
