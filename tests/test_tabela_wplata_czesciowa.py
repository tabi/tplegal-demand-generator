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
    """Teksty komórek wiersz po wierszu; pusta komórka scalona w pionie pomijana."""
    return [
        [t for t in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tr) if t]
        for tr in re.findall(r"<w:tr>.*?</w:tr>", xml, flags=re.S)
    ]


def _tr_xml(xml: str) -> list[str]:
    return re.findall(r"<w:tr>.*?</w:tr>", xml, flags=re.S)


def _blocks(xml: str) -> list[list[str]]:
    """Wiersze danych zgrupowane per faktura (od 0.8.3 faktura z wpłatami
    zajmuje kilka wierszy; kolejne mają komórki faktury scalone w pionie)."""
    blocks: list[list[str]] = []
    for tr, texts in zip(_tr_xml(xml)[1:], _rows(xml)[1:]):
        first_cell = re.search(r"<w:tc>.*?</w:tc>", tr, flags=re.S).group(0)
        if "<w:vMerge/>" in first_cell:
            blocks[-1].extend(texts)
        else:
            blocks.append(list(texts))
    return blocks


class TestKolumnaDoZaplaty:
    def test_naglowek_ma_kolumne_do_zaplaty(self):
        header = _rows(build_invoice_table_xml(_detail()))[0]
        assert header[2:4] == ["Kwota brutto", "Do zapłaty"]

    def test_wiersz_czesciowy_pokazuje_brutto_reszte_i_wplate(self):
        fv = next(b for b in _blocks(build_invoice_table_xml(_detail())) if "FV/2026/B" in b)
        assert _zl("300 000,00") in fv
        assert _zl("90 000,00") in fv
        assert "częściowo" in fv and "26.07.2026" in fv and _zl("210 000,00") in fv
        assert "—" not in fv

    def test_suma_kolumny_do_zaplaty_rowna_swiadczeniu_glownemu(self):
        rows = _blocks(build_invoice_table_xml(_detail()))
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
        row = _blocks(build_invoice_table_xml([
            {"invoice_number": "C", "gross_amount": 10000.0, "outstanding_pln": 0,
             "payments": [{"date": "2026-05-10", "amount": 4000}, {"date": "2026-03-10", "amount": 6000}],
             "due_date": "2026-02-10", "payment_date": "2026-05-10", "delay_days": 89,
             "interest_pln": 200.99, "compensation_pln": 301},
        ]))[0]
        assert "częściowo" not in row
        assert row.index("10.03.2026") < row.index("10.05.2026")  # chronologicznie


def _wiele_wplat():
    """Kształt z pisma, które sprowokowało 0.8.3: 13 wpłat na jednej fakturze."""
    dates = ["2026-07-08", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13",
             "2026-07-15", "2026-07-17", "2026-07-23", "2026-07-27", "2026-09-16",
             "2026-09-17", "2026-09-18", "2026-09-22"]
    amounts = [50000, 50000, 50000, 50000, 30000, 20000, 14000, 20000, 10000,
               20000, 10000, 10000, 10000]
    return [
        {"invoice_number": "FV/2026/X", "gross_amount": 489888.00, "outstanding_pln": 145888.00,
         "payments": [{"date": d, "amount": a} for d, a in zip(dates, amounts)],
         "due_date": "2026-07-16", "payment_date": None, "delay_days": 71,
         "interest_pln": 5203.16, "compensation_pln": 429.63},
        {"invoice_number": "FV/2026/Y", "gross_amount": 70200.00, "outstanding_pln": 70200.00,
         "due_date": "2026-08-26", "payment_date": None, "delay_days": 30,
         "interest_pln": 793.36, "compensation_pln": 431.28},
    ]


def _grid_span(tc: str) -> int:
    m = re.search(r'<w:gridSpan w:val="(\d+)"/>', tc)
    return int(m.group(1)) if m else 1


class TestWplatyWierszami:
    """0.8.3: wpłata = osobny wiersz (data | kwota), nie lista w jednej komórce."""

    def test_kazda_wplata_w_osobnym_wierszu(self):
        rows = _rows(build_invoice_table_xml(_wiele_wplat()))[1:]
        # „częściowo" + 13 wpłat + faktura bez wpłat
        assert len(rows) == 1 + 13 + 1
        assert rows[1] == ["08.07.2026", _zl("50 000,00")]
        assert rows[13] == ["22.09.2026", _zl("10 000,00")]

    def test_komorka_bez_lamania_linii(self):
        assert "<w:br/>" not in build_invoice_table_xml(_wiele_wplat())

    def test_komorki_faktury_scalone_w_pionie(self):
        trs = _tr_xml(build_invoice_table_xml(_wiele_wplat()))[1:]
        assert trs[0].count('<w:vMerge w:val="restart"/>') == 8
        assert all(tr.count("<w:vMerge/>") == 8 for tr in trs[1:14])
        assert "vMerge" not in trs[14]  # faktura bez wpłat = jeden zwykły wiersz

    def test_kazdy_wiersz_wypelnia_cala_siatke(self):
        xml = build_invoice_table_xml(_wiele_wplat())
        grid = [int(w) for w in re.findall(r'<w:gridCol w:w="(\d+)"/>', xml)]
        assert len(grid) == 10 and sum(grid) <= 9066
        for tr in _tr_xml(xml):
            assert sum(_grid_span(tc) for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, flags=re.S)) == 10

    def test_suma_wplat_plus_reszta_rowna_brutto(self):
        rows = _rows(build_invoice_table_xml(_wiele_wplat()))[2:15]
        wplaty = sum(float(r[1].replace(NBSP, "").replace(" zł", "").replace(",", ".")) for r in rows)
        assert round(wplaty + 145888.00, 2) == 489888.00


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
