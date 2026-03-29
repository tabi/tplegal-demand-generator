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

    def test_fallback_future_date(self):
        """Data poza zakresem -> zwraca ostatnią znaną stawkę."""
        rate = get_interest_rate(date(2030, 1, 1))
        assert isinstance(rate, (int, float))
        assert rate > 0


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
        """1 dzień opóźnienia -> minimalne odsetki."""
        result = calculate_interest(
            Decimal("10000"), date(2024, 6, 1), date(2024, 6, 2)
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
        """Odsetki przechodzące przez zmianę stawki."""
        # 2025-06-30 -> 2025-07-01: 15.75 -> 15.25
        result = calculate_interest(
            Decimal("100000"), date(2025, 6, 15), date(2025, 7, 15)
        )
        assert result > Decimal("0")

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
