#!/usr/bin/env python3
"""
Wpłaty częściowe — odsetki od malejącego salda, należność główna = reszta.

Do 0.7.0 faktura miała jedną `payment_date`. Faktura spłacona w kilku ratach
dawała w sandboxie Desktopu jedno z trzech złych obejść: rozbicie na pozycje
(N rekompensat zamiast jednej), pełną kwotę do ostatniej wpłaty (odsetki od
spłaconej już części) albo samą resztę jako brutto (zły próg art. 10 i brak
odsetek od spłaconej części).

Reguła (decyzja 24.09.2026, odwraca „bez waterfall" z 08.04.2026):
- każda wpłata niesie odsetki od SWOJEJ kwoty do SWOJEJ daty,
- niespłacona reszta niesie odsetki do dnia naliczania (payment_date),
- rekompensata JEDNA, próg od pełnej kwoty brutto faktury,
- należność główna w wezwaniu = brutto − suma wpłat.

Wpłata zaliczana na należność główną (tak brzmi decyzja) — nie na odsetki.

Kwoty oczekiwane są policzone RĘCZNIE, nie przez kalkulator:
    termin 10.02.2026 (wtorek, dzień roboczy), stawka H1 2026 = 14,00%
    6 000 × 14% × 28/365 = 64,44   (11.02–10.03 włącznie = 28 dni)
    4 000 × 14% × 89/365 = 136,55  (11.02–10.05 włącznie = 18+31+30+10 = 89 dni)
"""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from demand_generator.calc import calculate_batch, calculate_invoice

EUR = Decimal("4.30")
DUE = date(2026, 2, 10)
W1 = {"date": date(2026, 3, 10), "amount": Decimal("6000")}
W2 = {"date": date(2026, 5, 10), "amount": Decimal("4000")}


@patch("demand_generator.calc.get_nbp_eur_rate", return_value=EUR)
class TestCalculateInvoiceWplaty:
    def test_dwie_wplaty_splacaja_calosc(self, _):
        r = calculate_invoice(
            Decimal("10000"), DUE, date(2026, 5, 10), payments=[W1, W2]
        )
        assert r["interest"] == Decimal("200.99")  # 64,44 + 136,55
        assert r["paid_pln"] == Decimal("10000")
        assert r["outstanding_pln"] == Decimal("0")
        assert r["delay_days"] == 89

    def test_jedna_rekompensata_z_progu_pelnej_kwoty(self, _):
        """Transze 3 000 + 3 000 to NIE dwa razy próg 40 EUR — faktura 6 000 = 70 EUR."""
        r = calculate_invoice(
            Decimal("6000"), DUE, date(2026, 5, 10),
            payments=[
                {"date": date(2026, 3, 10), "amount": Decimal("3000")},
                {"date": date(2026, 5, 10), "amount": Decimal("3000")},
            ],
        )
        assert r["compensation"]["tier"] == "EUR_70"
        assert r["compensation"]["comp_pln"] == Decimal("301.00")

    def test_reszta_niezaplacona_niesie_odsetki_do_dnia_naliczania(self, _):
        r = calculate_invoice(
            Decimal("10000"), DUE, date(2026, 5, 10), payments=[W1]
        )
        # 6 000 do 10.03 + reszta 4 000 do 10.05 — ta sama arytmetyka co wyżej
        assert r["interest"] == Decimal("200.99")
        assert r["paid_pln"] == Decimal("6000")
        assert r["outstanding_pln"] == Decimal("4000")
        assert r["delay_days"] == 89

    def test_pusta_lista_wplat_to_faktura_niezaplacona(self, _):
        r = calculate_invoice(Decimal("4000"), DUE, date(2026, 5, 10), payments=[])
        assert r["interest"] == Decimal("136.55")
        assert r["paid_pln"] == Decimal("0")
        assert r["outstanding_pln"] == Decimal("4000")

    def test_wplata_w_terminie_nie_niesie_odsetek(self, _):
        r = calculate_invoice(
            Decimal("10000"), DUE, date(2026, 5, 10),
            payments=[{"date": date(2026, 2, 5), "amount": Decimal("6000")}],
        )
        assert r["interest"] == Decimal("136.55")  # tylko reszta 4 000
        assert r["outstanding_pln"] == Decimal("4000")

    def test_bez_payments_zachowanie_bez_zmian(self, _):
        """Stary kontrakt: payment_date = zapłata CAŁOŚCI tego dnia."""
        r = calculate_invoice(Decimal("4000"), DUE, date(2026, 5, 10))
        assert r["interest"] == Decimal("136.55")
        assert r["paid_pln"] == Decimal("4000")
        assert r["outstanding_pln"] == Decimal("0")

    def test_wplaty_ponad_kwote_faktury_to_blad(self, _):
        with pytest.raises(ValueError, match="przekracza"):
            calculate_invoice(
                Decimal("5000"), DUE, date(2026, 5, 10), payments=[W1]
            )

    def test_wplata_niedodatnia_to_blad(self, _):
        with pytest.raises(ValueError, match="dodatni"):
            calculate_invoice(
                Decimal("5000"), DUE, date(2026, 5, 10),
                payments=[{"date": date(2026, 3, 10), "amount": Decimal("0")}],
            )

    def test_z_data_pozwu_kazda_transza_liczy_sie_jak_osobna_zaplata(self, _):
        """Ścieżka z lawsuit_date (filtr przedawnienia) nie gubi transz."""
        due = date(2024, 12, 20)
        lawsuit = date(2026, 3, 1)
        pay = [
            {"date": date(2025, 1, 10), "amount": Decimal("1000")},
            {"date": date(2025, 2, 10), "amount": Decimal("1000")},
        ]
        r = calculate_invoice(
            Decimal("2000"), due, date(2023, 2, 10), payments=pay,
            lawsuit_date=lawsuit,
        )
        solo = [
            calculate_invoice(
                Decimal("1000"), due, p["date"], lawsuit_date=lawsuit
            )["interest"]
            for p in pay
        ]
        assert r["interest"] == sum(solo)


@patch("demand_generator.calc.get_nbp_eur_rate", return_value=EUR)
class TestCalculateBatchWplaty:
    def test_suma_naleznosci_glownej(self, _):
        invoices = [
            {"gross": 10000, "due_date": DUE, "payment_date": date(2026, 5, 10),
             "payments": [W1], "invoice_number": "FV/1"},
            {"gross": 3000, "due_date": DUE, "payment_date": date(2026, 5, 10),
             "payments": [], "invoice_number": "FV/2"},
            {"gross": 8000, "due_date": DUE, "payment_date": date(2026, 4, 1),
             "invoice_number": "FV/3"},
        ]
        r = calculate_batch(invoices)
        assert r["total_outstanding_pln"] == Decimal("7000.00")  # 4 000 + 3 000 + 0

    def test_przedawniona_nie_wchodzi_do_naleznosci_glownej(self, _):
        invoices = [
            {"gross": 5000, "due_date": date(2020, 1, 1),
             "payment_date": date(2026, 3, 1), "payments": [],
             "invoice_number": "FV/old"},
        ]
        r = calculate_batch(invoices, lawsuit_date=date(2026, 3, 1))
        assert r["prescribed_count"] == 1
        assert r["total_outstanding_pln"] == Decimal("0.00")


class TestCliWplaty:
    """Ścieżka pracownika: JSON → calc-rekompensa → stdout."""

    def _run(self, tmp_path, monkeypatch, capsys, invoice):
        import demand_generator.calc_cli as calc_cli

        json_file = tmp_path / "invoices.json"
        json_file.write_text(
            json.dumps({"invoices": [invoice], "debtor_type": "private"}),
            encoding="utf-8",
        )
        monkeypatch.setattr("demand_generator.calc.get_nbp_eur_rate", lambda d: EUR)
        monkeypatch.setattr("sys.argv", ["calc-rekompensa", "--json", str(json_file)])
        code = 0
        try:
            calc_cli.main()
        except SystemExit as e:
            code = e.code if e.code is not None else 0
        cap = capsys.readouterr()
        return code, cap.out, cap.err

    def test_wplaty_splacajace_calosc(self, tmp_path, monkeypatch, capsys):
        code, out, _ = self._run(tmp_path, monkeypatch, capsys, {
            "invoice_number": "FV/1", "gross": 10000, "due_date": "2026-02-10",
            "payments": [
                {"date": "2026-03-10", "amount": 6000},
                {"date": "2026-05-10", "amount": 4000},
            ],
        })
        assert code == 0
        r = json.loads(out)
        assert r["total_interest_pln"] == 200.99
        assert r["total_outstanding_pln"] == 0
        assert r["invoices"][0]["delay_days"] == 89

    def test_niezaplacona_faktura_to_cala_naleznosc_glowna(self, tmp_path, monkeypatch, capsys):
        code, out, _ = self._run(tmp_path, monkeypatch, capsys, {
            "invoice_number": "FV/1", "gross": 4000, "due_date": "2026-02-10",
        })
        assert code == 0
        assert json.loads(out)["total_outstanding_pln"] == 4000

    def test_czesciowa_wplata_reszta_to_naleznosc_glowna(self, tmp_path, monkeypatch, capsys):
        code, out, _ = self._run(tmp_path, monkeypatch, capsys, {
            "invoice_number": "FV/1", "gross": 10000, "due_date": "2026-02-10",
            "payments": [{"date": "2026-03-10", "amount": 6000}],
        })
        assert code == 0
        assert json.loads(out)["total_outstanding_pln"] == 4000

    def test_payment_date_razem_z_payments_to_blad(self, tmp_path, monkeypatch, capsys):
        """Dwa źródła daty zapłaty — nie zgadujemy, które mówi prawdę."""
        code, out, err = self._run(tmp_path, monkeypatch, capsys, {
            "invoice_number": "FV/1", "gross": 10000, "due_date": "2026-02-10",
            "payment_date": "2026-05-10",
            "payments": [{"date": "2026-03-10", "amount": 6000}],
        })
        assert code == 1
        assert out == ""
        assert "payments" in err

    def test_wplaty_ponad_kwote_to_blad(self, tmp_path, monkeypatch, capsys):
        code, out, err = self._run(tmp_path, monkeypatch, capsys, {
            "invoice_number": "FV/1", "gross": 5000, "due_date": "2026-02-10",
            "payments": [{"date": "2026-03-10", "amount": 6000}],
        })
        assert code == 1
        assert out == ""
        assert "przekracza" in err

    def test_zla_data_wplaty_to_blad(self, tmp_path, monkeypatch, capsys):
        code, out, err = self._run(tmp_path, monkeypatch, capsys, {
            "invoice_number": "FV/1", "gross": 5000, "due_date": "2026-02-10",
            "payments": [{"date": "10.03.2026", "amount": 1000}],
        })
        assert code == 1
        assert "10.03.2026" in err
