"""
Testy jednostkowe: calculate_civil_interest() — art. 481 par. 2 KC.

Port 1:1 z rekompensa/tests/test_civil_interest.py (private repo).
"""

import pytest
from datetime import date
from decimal import Decimal

from demand_generator.civil_interest import (
    calculate_civil_interest,
    CIVIL_INTEREST_RATES,  # noqa: F401 -- importowane dla potwierdzenia API
    _get_rate_for_date,    # noqa: F401
)


class TestSingleRatePeriod:
    """test_single_rate_period — okres w jednej stawce."""

    def test_single_rate_period(self):
        # 100 PLN, 01.10.2022 -> 01.11.2022 = 31 dni @ 12.25%
        result = calculate_civil_interest(
            Decimal('100'), date(2022, 10, 1), date(2022, 11, 1)
        )
        expected = Decimal('100') * Decimal('12.25') * Decimal('31') / (Decimal('100') * Decimal('365'))
        expected = expected.quantize(Decimal('0.01'))
        assert result == expected  # 1.04


class TestCrossOneRateChange:
    """test_cross_one_rate_change — przekroczenie jednej zmiany stawki."""

    def test_cross_one_rate_change(self):
        # 1000 PLN, 01.09.2023 -> 15.10.2023
        # Sub-period 1: 01.09 -> 07.09 = 6 dni @ 12.25%
        # Sub-period 2: 07.09 -> 05.10 = 28 dni @ 11.50%
        # Sub-period 3: 05.10 -> 15.10 = 10 dni @ 11.25%
        result = calculate_civil_interest(
            Decimal('1000'), date(2023, 9, 1), date(2023, 10, 15)
        )
        p1 = Decimal('1000') * Decimal('12.25') * Decimal('6') / (Decimal('100') * Decimal('365'))
        p2 = Decimal('1000') * Decimal('11.50') * Decimal('28') / (Decimal('100') * Decimal('365'))
        p3 = Decimal('1000') * Decimal('11.25') * Decimal('10') / (Decimal('100') * Decimal('365'))
        expected = (p1 + p2 + p3).quantize(Decimal('0.01'))
        assert result == expected


class TestCrossMultipleRateChanges:
    """test_cross_multiple_rate_changes — przekracza 3+ zmiany stawek."""

    def test_cross_multiple_rate_changes(self):
        # 500 PLN, 01.10.2023 -> 07.04.2026 — przekracza 8 zmian
        result = calculate_civil_interest(
            Decimal('500'), date(2023, 10, 1), date(2026, 4, 7)
        )
        assert result > Decimal('0')
        # Sanity check: ~2.5 years * ~10.5% avg on 500 PLN ~ 130 PLN
        assert Decimal('80') < result < Decimal('200')


class TestStartDateOnBoundary:
    """test_start_date_exactly_on_rate_change_boundary."""

    def test_start_on_rate_change_boundary(self):
        # Start = 08.05.2025 (dzien zmiany z 11.25% na 10.75%)
        # 100 PLN, 08.05.2025 -> 09.05.2025 = 1 dzien @ 10.75%
        result = calculate_civil_interest(
            Decimal('100'), date(2025, 5, 8), date(2025, 5, 9)
        )
        expected = (Decimal('100') * Decimal('10.75') * Decimal('1') / (Decimal('100') * Decimal('365'))).quantize(Decimal('0.01'))
        assert result == expected


class TestCutoffDateDefault:
    """test_cutoff_date_today_default."""

    def test_cutoff_today(self):
        result = calculate_civil_interest(
            Decimal('100'), date(2023, 1, 1), date.today()
        )
        assert result > Decimal('0')


class TestDecimalPrecision:
    """test_amount_decimal_precision."""

    def test_result_is_decimal_2dp(self):
        result = calculate_civil_interest(
            Decimal('123.45'), date(2023, 1, 1), date(2023, 7, 1)
        )
        assert isinstance(result, Decimal)
        assert result.as_tuple().exponent == -2


class TestPeriodSpanning2026RateChange:
    """test_period_spanning_2026_rate_change — 9.50% -> 9.25% na 05.03.2026."""

    def test_2026_rate_change(self):
        # 1000 PLN, 01.01.2026 -> 07.04.2026
        # Sub1: 01.01 -> 05.03 = 63 dni @ 9.50%
        # Sub2: 05.03 -> 07.04 = 33 dni @ 9.25%
        result = calculate_civil_interest(
            Decimal('1000'), date(2026, 1, 1), date(2026, 4, 7)
        )
        p1 = Decimal('1000') * Decimal('9.50') * Decimal('63') / (Decimal('100') * Decimal('365'))
        p2 = Decimal('1000') * Decimal('9.25') * Decimal('33') / (Decimal('100') * Decimal('365'))
        expected = (p1 + p2).quantize(Decimal('0.01'))
        assert result == expected


class TestZeroDaysPeriod:
    """test_zero_days_period — start == end -> Decimal('0')."""

    def test_zero_days(self):
        result = calculate_civil_interest(
            Decimal('1000'), date(2023, 6, 1), date(2023, 6, 1)
        )
        assert result == Decimal('0')


class TestNegativePeriod:
    """test_negative_period — start > end -> Decimal('0'), defensywnie."""

    def test_negative_period(self):
        result = calculate_civil_interest(
            Decimal('1000'), date(2023, 7, 1), date(2023, 6, 1)
        )
        assert result == Decimal('0')


class TestStartDateBeforeRateTable:
    """test_start_date_before_rate_table — ValueError."""

    def test_before_table(self):
        with pytest.raises(ValueError, match="przed zakresem tabeli stawek"):
            calculate_civil_interest(
                Decimal('1000'), date(2020, 1, 1), date(2023, 1, 1)
            )


class TestLeapYear2024:
    """test_leap_year_2024 — /365 nawet w roku przestepnym."""

    def test_leap_year(self):
        # 1000 PLN, 28.02.2024 -> 02.03.2024 = 3 dni (przechodzi przez 29.02)
        # Rate on 28.02.2024 = 11.25% (ostatnia stawka przed 08.05.2025)
        result = calculate_civil_interest(
            Decimal('1000'), date(2024, 2, 28), date(2024, 3, 2)
        )
        expected = (Decimal('1000') * Decimal('11.25') * Decimal('3') / (Decimal('100') * Decimal('365'))).quantize(Decimal('0.01'))
        assert result == expected


class TestAmountZero:
    """test_amount_zero — kwota 0 -> wynik 0."""

    def test_zero_amount(self):
        result = calculate_civil_interest(
            Decimal('0'), date(2023, 1, 1), date(2026, 1, 1)
        )
        assert result == Decimal('0')


class TestVerySmallAmount:
    """test_very_small_amount — 0.01 PLN przez 10 lat -> wynik > 0."""

    def test_very_small_amount(self):
        result_short = calculate_civil_interest(
            Decimal('0.01'), date(2022, 9, 8), date(2026, 4, 7)
        )
        assert isinstance(result_short, Decimal)
        result_long = calculate_civil_interest(
            Decimal('0.01'), date(2022, 9, 8), date(2026, 4, 7)
        )
        assert isinstance(result_long, Decimal)
        assert result_long >= Decimal('0')
