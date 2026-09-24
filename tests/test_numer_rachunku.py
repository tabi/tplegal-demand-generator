#!/usr/bin/env python3
"""
Numer rachunku w wezwaniu — jeden format, suma kontrolna sprawdzona.

Generator wstawiał `cr_bank` do pisma dosłownie, więc format zależał od tego,
jak model przepisał numer: raz z „PL", raz bez, raz ciągiem cyfr. Decyzja
24.09.2026: zawsze bez „PL", grupy jak w stopce kancelarii
(„60 1140 2004 0000 3802 7707 5123"). Numer z błędną sumą kontrolną (IBAN
mod 97) to twardy błąd — literówka w rachunku kieruje wpłatę dłużnika donikąd.

Numer testowy = rachunek kancelarii ze stopki wzoru pisma (publiczny).
"""

import zipfile

import pytest

from demand_generator import DEFAULT_TEMPLATE
from demand_generator.generator import fill_template_from_dict
from demand_generator.utils import format_bank_account

OK = "60 1140 2004 0000 3802 7707 5123"


class TestFormatBankAccount:
    @pytest.mark.parametrize("raw", [
        "PL 60 1140 2004 0000 3802 7707 5123",
        "PL60114020040000380277075123",
        "pl60 1140 2004 0000 3802 7707 5123",
        "60114020040000380277075123",
        "60-1140-2004-0000-3802-7707-5123",
        " 60 1140 2004 0000 3802 7707 5123 ",
        "60 1140 2004 0000 3802 7707 5123",
    ])
    def test_kazdy_zapis_daje_ten_sam_format_bez_pl(self, raw):
        assert format_bank_account(raw) == OK

    @pytest.mark.parametrize("raw", [None, "", "   ", "___", "_____"])
    def test_brak_numeru_to_placeholder(self, raw):
        assert format_bank_account(raw) == "___"

    def test_przestawione_cyfry_to_blad(self):
        with pytest.raises(ValueError, match="sumę kontrolną"):
            format_bank_account("60 1140 2004 0000 3802 7707 5132")

    def test_za_malo_cyfr_to_blad(self):
        with pytest.raises(ValueError, match="26 cyfr"):
            format_bank_account("60 1140 2004 0000 3802 7707")

    def test_zagraniczny_iban_to_blad(self):
        with pytest.raises(ValueError, match="26 cyfr"):
            format_bank_account("DE89 3704 0044 0532 0130 00")


def _data(cr_bank):
    return {
        "creditor_name": "FIRMA ABC SP. Z O.O.",
        "debtor_name": "DŁUŻNIK XYZ S.A.",
        "cr_bank": cr_bank,
        "total_compensation_pln": 301.00,
        "total_interest_pln": 10.00,
        "invoice_numbers": ["FV/1"],
    }


class TestNumerRachunkuWPismie:
    """Ścieżka produkcyjna: wbudowany template → document.xml."""

    def test_pl_z_wejscia_nie_trafia_do_pisma(self, tmp_path):
        out = tmp_path / "w.docx"
        fill_template_from_dict(DEFAULT_TEMPLATE, out, _data("PL60114020040000380277075123"))
        with zipfile.ZipFile(out) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        # Stopka kancelarii siedzi w footerN.xml, więc tu jest wyłącznie
        # numer z {{NUMER_RACHUNKU}}.
        assert xml.count(OK) == 1
        assert "PL 60 1140" not in xml and "PL60" not in xml

    def test_bledny_numer_nie_generuje_pisma(self, tmp_path):
        out = tmp_path / "w.docx"
        with pytest.raises(ValueError, match="sumę kontrolną"):
            fill_template_from_dict(DEFAULT_TEMPLATE, out, _data("60 1140 2004 0000 3802 7707 5132"))
        assert not out.exists()
