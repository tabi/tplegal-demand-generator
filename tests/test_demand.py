#!/usr/bin/env python3
"""
Unit testy dla demand_generator.generator — generator wezwań do zapłaty.
Pokrywa: fill_template_from_dict, walidacja pól, kwota_slownie, helpers.

Uruchomienie:
    python -m pytest tests/test_demand.py -v
"""

import json
import zipfile
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from demand_generator.generator import (
    fill_template_from_dict,
    kwota_slownie,
    format_pln,
    format_pln_zl,
    format_date_pl,
    art_10_reference,
    build_invoice_table_xml,
    _format_date,
    _join_address,
    _join_zip_city,
    _xml_escape,
)
from demand_generator.utils import normalize_entity_name
from demand_generator import DEFAULT_TEMPLATE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_template(tmp_path):
    """Tworzy minimalny mock template .docx z placeholderami."""
    template_path = tmp_path / "template.docx"
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:r><w:t>{{DATA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{WIERZYCIEL_NAZWA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{WIERZYCIEL_ADRES}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{RADCA_IMIE_NAZWISKO}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{DLUZNIK_NAZWA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{DLUZNIK_ADRES_ULICA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{DLUZNIK_ADRES_MIASTO}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_LACZNIE_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_GLOWNA_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_REKOMPENSATY_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_ODSETKI_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{LISTA_FAKTUR}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{NUMER_RACHUNKU}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{TERMIN_DNI}}</w:t></w:r></w:p>
</w:body>
</w:document>"""

    with zipfile.ZipFile(template_path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')

    return template_path


@pytest.fixture
def sample_data():
    """Pełny zestaw danych do fill_template_from_dict."""
    return {
        "creditor_name": "FIRMA ABC SP. Z O.O.",
        "cr_street": "ul. Skarbowa 2/5",
        "cr_city": "Leszno",
        "cr_zip": "64-100",
        "cr_bank": "PL 12 3456 7890 1234 5678 9012 3456",
        "debtor_name": "DŁUŻNIK XYZ S.A.",
        "d_street": "ul. Poznańska 10",
        "d_city": "Poznań",
        "d_zip": "60-001",
        "assigned_to": "Bartłomieja Przyniczkę",
        "total_compensation_pln": 1234.56,
        "total_interest_pln": 567.89,
        "invoice_numbers": ["FV/2024/001", "FV/2024/002"],
        "invoice_tiers": {"EUR_40", "EUR_70"},
    }


# ---------------------------------------------------------------------------
# fill_template_from_dict
# ---------------------------------------------------------------------------

class TestFillTemplateFromDict:
    def test_generates_docx(self, mock_template, sample_data, tmp_path):
        """Generuje poprawny plik .docx."""
        output = tmp_path / "output.docx"
        result = fill_template_from_dict(mock_template, output, sample_data)
        assert result == output
        assert output.exists()
        # Sprawdź że to poprawny ZIP
        with zipfile.ZipFile(output, "r") as zf:
            assert "word/document.xml" in zf.namelist()

    def test_placeholders_replaced(self, mock_template, sample_data, tmp_path):
        """Placeholdery są zamienione na dane."""
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, sample_data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # Nie powinno być żadnych placeholderów
        assert "{{" not in content
        assert "}}" not in content
        # Powinny być dane
        assert "Firma Abc Sp. z o.o." in content
        assert "FV/2024/001" in content

    def test_missing_required_field_raises(self, mock_template, tmp_path):
        """Brak wymaganego pola -> ValueError."""
        data = {"debtor_name": "Test", "total_compensation_pln": 100}
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="creditor_name"):
            fill_template_from_dict(mock_template, output, data)

    def test_missing_multiple_fields_raises(self, mock_template, tmp_path):
        """Brak wielu pól -> ValueError z listą."""
        data = {}
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="Missing required fields"):
            fill_template_from_dict(mock_template, output, data)

    def test_unknown_strategy_raises(self, mock_template, sample_data, tmp_path):
        """Nieznana strategia -> ValueError."""
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="Unknown strategy"):
            fill_template_from_dict(mock_template, output, sample_data, strategy="nonexistent")

    def test_template_not_found_raises(self, sample_data, tmp_path):
        """Nieistniejący template -> FileNotFoundError."""
        output = tmp_path / "output.docx"
        with pytest.raises(FileNotFoundError, match="Template not found"):
            fill_template_from_dict(Path("/nonexistent/template.docx"), output, sample_data)

    def test_missing_bank_account_placeholder(self, mock_template, sample_data, tmp_path):
        """Brak bank_account -> placeholder '___'."""
        del sample_data["cr_bank"]
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, sample_data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")
        assert "___" in content

    def test_all_strategies(self, mock_template, sample_data, tmp_path):
        """Wszystkie strategie generują plik bez błędu."""
        for strategy in ["soft_collect", "standard_collect", "hard_collect", "pre_litigation"]:
            output = tmp_path / f"output_{strategy}.docx"
            fill_template_from_dict(mock_template, output, sample_data, strategy=strategy)
            assert output.exists()

    def test_zero_amounts_raises(self, mock_template, tmp_path):
        """Brak jakichkolwiek kwot -> ValueError."""
        data = {
            "creditor_name": "Test Sp. z o.o.",
            "debtor_name": "Dłużnik S.A.",
        }
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="Brak kwot do wezwania"):
            fill_template_from_dict(mock_template, output, data)


# ---------------------------------------------------------------------------
# Principal support (total_principal_pln)
# ---------------------------------------------------------------------------

class TestPrincipalSupport:
    def test_principal_included_in_combined(self, mock_template, tmp_path):
        """JSON z total_principal_pln → kwota łączna = principal + comp + interest."""
        data = {
            "creditor_name": "Wierzyciel Sp. z o.o.",
            "debtor_name": "Dłużnik S.A.",
            "total_principal_pln": 10000,
            "total_compensation_pln": 500,
            "total_interest_pln": 200,
            "invoice_numbers": ["FV/1"],
        }
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # combined = 10000 + 500 + 200 = 10700 → "10 700,00"
        assert "10\u00a0700,00" in content

    def test_no_principal_backward_compatible(self, mock_template, tmp_path):
        """JSON bez total_principal_pln → backward compatible (łączna = comp + interest)."""
        data = {
            "creditor_name": "Wierzyciel Sp. z o.o.",
            "debtor_name": "Dłużnik S.A.",
            "total_compensation_pln": 500,
            "total_interest_pln": 200,
            "invoice_numbers": ["FV/1"],
        }
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # combined = 0 + 500 + 200 = 700 → "700,00"
        assert "700,00" in content

    def test_principal_only_no_comp(self, mock_template, tmp_path):
        """Principal=10000, comp=0, interest=500 → łączna = 10500."""
        data = {
            "creditor_name": "Wierzyciel Sp. z o.o.",
            "debtor_name": "Dłużnik S.A.",
            "total_principal_pln": 10000,
            "total_compensation_pln": 0,
            "total_interest_pln": 500,
            "invoice_numbers": ["FV/1"],
        }
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # combined = 10000 + 0 + 500 = 10500 → "10 500,00"
        assert "10\u00a0500,00" in content

    def test_principal_placeholder_present(self, mock_template, tmp_path):
        """KWOTA_GLOWNA_PLN placeholder jest wypełniony."""
        data = {
            "creditor_name": "Wierzyciel Sp. z o.o.",
            "debtor_name": "Dłużnik S.A.",
            "total_principal_pln": 15000,
            "total_compensation_pln": 300,
            "total_interest_pln": 100,
            "invoice_numbers": ["FV/1"],
        }
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # principal = 15000 → "15 000,00"
        assert "15\u00a0000,00" in content
        assert "{{KWOTA_GLOWNA_PLN}}" not in content


# ---------------------------------------------------------------------------
# DEFAULT_TEMPLATE
# ---------------------------------------------------------------------------

class TestDefaultTemplate:
    def test_default_template_exists(self):
        """Bundled template istnieje na dysku."""
        assert DEFAULT_TEMPLATE.exists(), f"Template not found: {DEFAULT_TEMPLATE}"

    def test_default_template_is_valid_docx(self):
        """Bundled template jest poprawnym plikiem ZIP/DOCX."""
        with zipfile.ZipFile(DEFAULT_TEMPLATE, "r") as zf:
            assert "word/document.xml" in zf.namelist()


# ---------------------------------------------------------------------------
# kwota_slownie
# ---------------------------------------------------------------------------

class TestKwotaSlownie:
    def test_zero(self):
        assert kwota_slownie(0) == "zero złotych 00/100"

    def test_one(self):
        assert kwota_slownie(1) == "jeden złoty 00/100"

    def test_two(self):
        assert kwota_slownie(2) == "dwa złote 00/100"

    def test_five(self):
        assert kwota_slownie(5) == "pięć złotych 00/100"

    def test_twelve(self):
        result = kwota_slownie(12)
        assert result == "dwanaście złotych 00/100"

    def test_thousand(self):
        result = kwota_slownie(1000)
        assert result == "tysiąc złotych 00/100"

    def test_complex_amount(self):
        result = kwota_slownie(157720.00)
        assert "sto pięćdziesiąt siedem tysięcy siedemset dwadzieścia złotych 00/100" == result

    def test_with_grosze(self):
        result = kwota_slownie(1234.56)
        assert result.endswith("56/100")
        assert "tysiąc" in result

    def test_decimal_input(self):
        result = kwota_slownie(Decimal("99.99"))
        assert "dziewięćdziesiąt dziewięć" in result
        assert "99/100" in result


# ---------------------------------------------------------------------------
# format_pln
# ---------------------------------------------------------------------------

class TestFormatPln:
    def test_basic(self):
        assert format_pln(1234.56) == "1\u00a0234,56"

    def test_zero(self):
        assert format_pln(0) == "0,00"

    def test_large_amount(self):
        assert format_pln(1000000) == "1\u00a0000\u00a0000,00"


# ---------------------------------------------------------------------------
# art_10_reference
# ---------------------------------------------------------------------------

class TestArt10Reference:
    def test_single_tier(self):
        assert art_10_reference({"EUR_40"}) == "10 ust. 1 pkt 1"

    def test_two_tiers(self):
        assert art_10_reference({"EUR_40", "EUR_100"}) == "10 ust. 1 pkt 1 i 3"

    def test_all_tiers(self):
        assert art_10_reference({"EUR_40", "EUR_70", "EUR_100"}) == "10 ust. 1 pkt 1, 2 i 3"

    def test_empty(self):
        assert art_10_reference(set()) == "10 ust. 1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_format_date(self):
        result = _format_date(date(2026, 3, 28))
        assert result == "28 marca 2026 r."

    def test_join_address_full(self):
        assert _join_address("ul. Skarbowa 2", "64-100", "Leszno") == "ul. Skarbowa 2, 64-100 Leszno"

    def test_join_address_missing(self):
        assert _join_address(None, None, None) == "___"

    def test_join_zip_city_full(self):
        assert _join_zip_city("64-100", "Leszno") == "64-100 Leszno"

    def test_join_zip_city_missing(self):
        assert _join_zip_city(None, None) == "___"

    def test_xml_escape(self):
        assert _xml_escape('a & b < c > d "e" \'f\'') == "a &amp; b &lt; c &gt; d &quot;e&quot; &apos;f&apos;"


# ---------------------------------------------------------------------------
# Legal form abbreviation fixes (normalize_entity_name)
# ---------------------------------------------------------------------------

class TestLegalFormFixes:
    def test_sp_z_oo(self):
        assert normalize_entity_name("PARK HOME SP. Z O.O.") == "Park Home Sp. z o.o."

    def test_sp_z_oo_nukka(self):
        assert normalize_entity_name("NUKKA SP. Z O.O.") == "Nukka Sp. z o.o."

    def test_sp_k(self):
        assert normalize_entity_name("FIRMA ABC SP.K.") == "Firma Abc Sp.k."

    def test_sp_j(self):
        assert normalize_entity_name("KANCELARIA TABERT PRZYNICZKA SP.J.") == "Kancelaria Tabert Przyniczka Sp.j."

    def test_sa(self):
        assert normalize_entity_name("WIELKA FIRMA S.A.") == "Wielka Firma S.A."

    def test_sc(self):
        assert normalize_entity_name("JAN KOWALSKI S.C.") == "Jan Kowalski s.c."

    def test_full_form_spolka_zoo(self):
        """Pełna forma 'Spółka z ograniczoną odpowiedzialnością' — bez zmian."""
        assert normalize_entity_name("FIRMA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ") == "Firma Spółka z ograniczoną odpowiedzialnością"

    def test_full_form_jawna(self):
        """Pełna forma 'Spółka jawna' — bez zmian."""
        assert normalize_entity_name("FIRMA SPÓŁKA JAWNA") == "Firma Spółka jawna"


# ---------------------------------------------------------------------------
# Invoice table generation
# ---------------------------------------------------------------------------

class TestInvoiceTable:
    def test_build_table_xml_structure(self):
        """build_invoice_table_xml generates valid XML with w:tbl."""
        detail = [
            {
                "invoice_number": "FV/1",
                "gross_amount": 2725.00,
                "due_date": "2025-01-31",
                "payment_date": "2025-03-15",
                "delay_days": 43,
                "interest_pln": 50.12,
                "compensation_pln": 169.68,
            }
        ]
        xml = build_invoice_table_xml(detail)
        assert "<w:tbl>" in xml
        assert "</w:tbl>" in xml
        assert "FV/1" in xml
        assert "2\u00a0725,00 zł" in xml  # gross formatted
        assert "31.01.2025" in xml  # due_date
        assert "15.03.2025" in xml  # payment_date

    def test_table_unpaid_invoice_em_dash(self):
        """Unpaid invoice (payment_date=null) shows em dash."""
        detail = [
            {
                "invoice_number": "FV/2",
                "gross_amount": 1000.00,
                "due_date": "2025-02-28",
                "payment_date": None,
                "delay_days": 30,
                "interest_pln": 12.95,
                "compensation_pln": 172.00,
            }
        ]
        xml = build_invoice_table_xml(detail)
        assert "\u2014" in xml  # em dash

    def test_table_multiple_rows(self):
        """Multiple invoices produce multiple rows."""
        detail = [
            {
                "invoice_number": "FV/1",
                "gross_amount": 1000.00,
                "due_date": "2025-01-15",
                "payment_date": "2025-02-15",
                "delay_days": 31,
                "interest_pln": 10.00,
                "compensation_pln": 172.00,
            },
            {
                "invoice_number": "FV/2",
                "gross_amount": 5000.00,
                "due_date": "2025-02-15",
                "payment_date": None,
                "delay_days": 42,
                "interest_pln": 30.00,
                "compensation_pln": 172.00,
            },
        ]
        xml = build_invoice_table_xml(detail)
        assert "FV/1" in xml
        assert "FV/2" in xml
        # Header + 2 data rows = 3 <w:tr>
        assert xml.count("<w:tr>") == 3

    def test_fill_template_with_invoices_detail(self, mock_template, tmp_path):
        """invoices_detail → table replaces {{LISTA_FAKTUR}} paragraph."""
        data = {
            "creditor_name": "Wierzyciel Sp. z o.o.",
            "debtor_name": "Dłużnik S.A.",
            "total_compensation_pln": 169.68,
            "total_interest_pln": 50.12,
            "invoices_detail": [
                {
                    "invoice_number": "FV/GLO/1/7/25",
                    "gross_amount": 2725.00,
                    "due_date": "2025-01-31",
                    "payment_date": None,
                    "delay_days": 57,
                    "interest_pln": 50.12,
                    "compensation_pln": 169.68,
                }
            ],
        }
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # Table should be present
        assert "<w:tbl>" in content
        assert "FV/GLO/1/7/25" in content
        # Original placeholder paragraph should be gone
        assert "{{LISTA_FAKTUR}}" not in content

    def test_fill_template_without_invoices_detail_backward_compat(self, mock_template, sample_data, tmp_path):
        """Without invoices_detail, LISTA_FAKTUR is plain text (backward compat)."""
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, sample_data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # No table
        assert "<w:tbl>" not in content
        # Invoice numbers as text
        assert "FV/2024/001" in content
        assert "FV/2024/002" in content


# ---------------------------------------------------------------------------
# Format helpers for table
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    def test_format_pln_zl(self):
        assert format_pln_zl(1234.56) == "1\u00a0234,56 zł"

    def test_format_pln_zl_zero(self):
        assert format_pln_zl(0) == "0,00 zł"

    def test_format_date_pl_string(self):
        assert format_date_pl("2025-01-31") == "31.01.2025"

    def test_format_date_pl_date(self):
        assert format_date_pl(date(2025, 3, 15)) == "15.03.2025"
