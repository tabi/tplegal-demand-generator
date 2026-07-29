#!/usr/bin/env python3
"""Unit testy dla demand_generator.check_rates.

Wszystkie testy wstrzykują `today`, żeby nie zależeć od daty uruchomienia —
inaczej suite zmieniałby wynik z upływem czasu (dokładnie ta klasa problemu,
którą check-rates ma wykrywać).
"""

import io
from datetime import date, timedelta

from demand_generator.calc import INTEREST_RATES
from demand_generator.check_rates import (
    EXIT_EXPIRED,
    EXIT_OK,
    EXIT_WARN,
    HORIZON_DAYS,
    status,
    warn_if_stale,
)
from demand_generator.civil_interest import (
    LAST_VERIFIED_DATE,
    STALENESS_WARNING_DAYS,
)

COMMERCIAL_HORIZON = INTEREST_RATES[-1]["to_d"]
CIVIL_HORIZON = LAST_VERIFIED_DATE + timedelta(days=STALENESS_WARNING_DAYS)
EARLIEST_HORIZON = min(COMMERCIAL_HORIZON, CIVIL_HORIZON)


class TestStatus:
    def test_reports_both_tables(self):
        st = status(today=date(2026, 7, 29))
        assert len(st["tabele"]) == 2
        nazwy = [e["tabela"] for e in st["tabele"]]
        assert any("INTEREST_RATES" in n for n in nazwy)
        assert any("CIVIL_INTEREST_RATES" in n for n in nazwy)

    def test_ok_well_before_horizon(self):
        st = status(today=EARLIEST_HORIZON - timedelta(days=HORIZON_DAYS + 5))
        assert st["exit_code"] == EXIT_OK
        assert all(e["stan"] == "OK" for e in st["tabele"])

    def test_warn_inside_horizon(self):
        st = status(today=EARLIEST_HORIZON - timedelta(days=1))
        assert st["exit_code"] == EXIT_WARN
        assert any(e["stan"] == "UWAGA" for e in st["tabele"])

    def test_expired_after_horizon(self):
        st = status(today=max(COMMERCIAL_HORIZON, CIVIL_HORIZON) + timedelta(days=1))
        assert st["exit_code"] == EXIT_EXPIRED
        assert all(e["stan"] == "PO TERMINIE" for e in st["tabele"])

    def test_expired_wins_over_warn(self):
        """Jedna tabela po terminie, druga jeszcze nie -> exit 2, nie 1."""
        st = status(today=EARLIEST_HORIZON + timedelta(days=1))
        assert st["exit_code"] == EXIT_EXPIRED

    def test_commercial_horizon_is_last_table_period(self):
        st = status(today=date(2026, 7, 29))
        commercial = next(e for e in st["tabele"] if "INTEREST_RATES" in e["tabela"])
        assert commercial["horyzont"] == COMMERCIAL_HORIZON
        assert commercial["stawka"] == f"{INTEREST_RATES[-1]['rate']:.2f}%"

    def test_last_day_of_period_is_not_expired(self):
        """Ostatni dzień okresu jeszcze się liczy (dni_do_konca == 0)."""
        st = status(today=COMMERCIAL_HORIZON)
        commercial = next(e for e in st["tabele"] if "INTEREST_RATES" in e["tabela"])
        assert commercial["dni_do_konca"] == 0
        assert commercial["stan"] == "UWAGA"


class TestWarnIfStale:
    def test_silent_when_ok(self):
        buf = io.StringIO()
        code = warn_if_stale(
            today=EARLIEST_HORIZON - timedelta(days=HORIZON_DAYS + 5), stream=buf
        )
        assert code == EXIT_OK
        assert buf.getvalue() == ""

    def test_warns_before_horizon(self):
        buf = io.StringIO()
        code = warn_if_stale(today=EARLIEST_HORIZON - timedelta(days=1), stream=buf)
        assert code == EXIT_WARN
        out = buf.getvalue()
        assert "UWAGA" in out
        assert "Aktualizacja stawek" in out

    def test_warns_after_horizon(self):
        buf = io.StringIO()
        code = warn_if_stale(
            today=max(COMMERCIAL_HORIZON, CIVIL_HORIZON) + timedelta(days=1), stream=buf
        )
        assert code == EXIT_EXPIRED
        out = buf.getvalue()
        assert "PRZETERMINOWANA" in out
        assert "demand_generator/calc.py" in out
