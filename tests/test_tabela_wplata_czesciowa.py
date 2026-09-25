#!/usr/bin/env python3
"""
Tabela wezwania przy wpłacie częściowej — kolumna „Do zapłaty" + wpłaty w „Dacie zapłaty".

Zgłoszenie 25.09.2026 (pismo z 0.8.1): faktura zapłacona częściowo miała w tabeli
pełną „Kwotę brutto" i „Data zapłaty —", czyli wyglądała na w ogóle niezapłaconą,
a suma kolumny kwot nie zgadzała się ze świadczeniem głównym z pkt 1 pisma.

Dane SYNTETYCZNE (repo publiczne): 4 faktury, brutto razem 650 000,00 zł,
na FV/2026/B wpłacono 210 000,00 zł, świadczenie główne = 440 000,00 zł.
Test sprawdza układ tabeli, nie odsetki.
"""

import re
import zipfile

import pytest

from demand_generator import DEFAULT_TEMPLATE
from demand_generator.generator import build_invoice_table_xml, fill_template_from_dict

NBSP = " "


def _zl(s: str) -> str:
    return s.replace(" ", NBSP) + " zł"


def _detail():
    return [
        {"invoice_number": "FV/2026/A", "gross_amount": 100000.00, "outstanding_pln": 100000.00,
         "due_date": "2026-07-13", "payment_date": None, "delay_days": 74,
         "interest_pln": 2787.67, "compensation_pln": 429.63},
        {"invoice_number": "FV/2026/B", "gross_amount": 300000.00, "outstanding_pln": 90000.00,
         "payments": [{"date": "2026-07-26", "amount": 210000.00}],
         "due_date": "2026-07-16", "payment_date": None, "delay_days": 71,
         "interest_pln": 3198.35, "compensation_pln": 429.63},
        {"invoice_number": "FV/2026/C", "gross_amount": 200000.00, "outstanding_pln": 200000.00,
         "due_date": "2026-08-26", "payment_date": None, "delay_days": 30,
         "interest_pln": 2260.27, "compensation_pln": 431.28},
        {"invoice_number": "FV/2026/D", "gross_amount": 50000.00, "outstanding_pln": 50000.00,
         "due_date": "2026-08-26", "payment_date": None, "delay_days": 30,
         "interest_pln": 565.07, "compensation_pln": 431.28},
    ]


def _data(principal=440000.00):
    return {
        "creditor_name": "WIERZYCIEL TEST",
        "debtor_name": "DŁUŻNIK TEST SP. Z O.O.",
        "total_principal_pln": principal,
        "total_compensation_pln": 1721.82,
        "total_interest_pln": 8811.36,
        "total_civil_interest_pln": 21.47,
        "invoices_detail": _detail(),
    }


def _rows(xml: str) -> list[list[str]]:
    return [
        re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tr)
        for tr in re.findall(r"<w:tr>.*?</w:tr>", xml, flags=re.S)
    ]


class TestKolumnaDoZaplaty:
    def test_naglowek_ma_kolumne_do_zaplaty(self):
        header = _rows(build_invoice_table_xml(_detail()))[0]
        assert header[2:4] == ["Kwota brutto", "Do zapłaty"]

    def test_wiersz_czesciowy_pokazuje_brutto_reszte_i_wplate(self):
        fv = next(r for r in _rows(build_invoice_table_xml(_detail())) if "FV/2026/B" in r)
        assert _zl("300 000,00") in fv
        assert _zl("90 000,00") in fv
        assert "częściowo" in fv and "26.07.2026" in fv and _zl("210 000,00") in fv
        assert "—" not in fv

    def test_suma_kolumny_do_zaplaty_rowna_swiadczeniu_glownemu(self):
        rows = _rows(build_invoice_table_xml(_detail()))[1:]
        suma = sum(float(r[3].replace(NBSP, "").replace(" zł", "").replace(",", ".")) for r in rows)
        assert round(suma, 2) == 440000.00

    def test_stary_kontrakt_bez_outstanding(self):
        """Wiersz sprzed 0.8.2: payment_date → 0 do zapłaty, brak → całe brutto."""
        rows = _rows(build_invoice_table_xml([
            {"invoice_number": "A", "gross_amount": 1000.0, "due_date": "2025-01-15",
             "payment_date": "2025-02-15", "delay_days": 31, "interest_pln": 10, "compensation_pln": 172},
            {"invoice_number": "B", "gross_amount": 5000.0, "due_date": "2025-02-15",
             "payment_date": None, "delay_days": 42, "interest_pln": 30, "compensation_pln": 172},
        ]))[1:]
        assert rows[0][3] == _zl("0,00") and rows[0][5] == "15.02.2025"
        assert rows[1][3] == _zl("5 000,00") and rows[1][5] == "—"

    def test_zaplata_calosci_w_dwoch_wplatach_wypisuje_obie(self):
        row = _rows(build_invoice_table_xml([
            {"invoice_number": "C", "gross_amount": 10000.0, "outstanding_pln": 0,
             "payments": [{"date": "2026-05-10", "amount": 4000}, {"date": "2026-03-10", "amount": 6000}],
             "due_date": "2026-02-10", "payment_date": "2026-05-10", "delay_days": 89,
             "interest_pln": 200.99, "compensation_pln": 301},
        ]))[1]
        assert "częściowo" not in row
        assert row.index("10.03.2026") < row.index("10.05.2026")  # chronologicznie


class TestPismo:
    """Ścieżka produkcyjna: wbudowany template → document.xml."""

    def test_pismo_z_wplata_czesciowa(self, tmp_path):
        out = tmp_path / "w.docx"
        fill_template_from_dict(DEFAULT_TEMPLATE, out, _data())
        with zipfile.ZipFile(out) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        assert "Do zapłaty" in xml and "częściowo" in xml

    def test_suma_tabeli_niezgodna_z_pkt_1_to_blad(self, tmp_path):
        out = tmp_path / "w.docx"
        with pytest.raises(ValueError, match="Do zapłaty"):
            fill_template_from_dict(DEFAULT_TEMPLATE, out, _data(principal=650000.00))
        assert not out.exists()
