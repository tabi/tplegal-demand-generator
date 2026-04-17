"""Testy enrichment z GUS REGON w generate-demand.

Mockuje lookup_by_nip — nie wywołuje prawdziwego SOAP.
"""

from unittest.mock import patch

import pytest

from demand_generator.generator import _enrich_from_gus, MISSING_DATA_MARKER
from demand_generator.gus_lookup import (
    GUSLookupError,
    _format_gus_address,
    _parse_xml_response,
)


# ---------------------------------------------------------------------------
# _format_gus_address — formatowanie adresu z GUS
# ---------------------------------------------------------------------------

def test_format_address_with_street():
    gus = {
        "Ulica": "ul. Skarbowa",
        "NrNieruchomosci": "2",
        "NrLokalu": "5",
        "KodPocztowy": "64-100",
        "Miejscowosc": "Leszno",
    }
    street, city, zip_code = _format_gus_address(gus)
    assert street == "ul. Skarbowa 2/5"
    assert city == "Leszno"
    assert zip_code == "64-100"


def test_format_address_adds_ul_prefix():
    gus = {"Ulica": "Skarbowa", "NrNieruchomosci": "2",
           "Miejscowosc": "Leszno", "KodPocztowy": "64-100"}
    street, _, _ = _format_gus_address(gus)
    assert street == "ul. Skarbowa 2"


def test_format_address_bare_village_fallback():
    # Wieś bez nazwanej ulicy — street_part = "Miejscowosc Nr"
    gus = {"Ulica": "", "NrNieruchomosci": "12",
           "Miejscowosc": "Radomin", "KodPocztowy": "87-404"}
    street, city, zip_code = _format_gus_address(gus)
    assert street == "Radomin 12"
    assert city == "Radomin"


def test_format_address_empty_when_no_street_no_nr():
    gus = {"Miejscowosc": "Warszawa", "KodPocztowy": "00-001"}
    street, city, zip_code = _format_gus_address(gus)
    assert street == ""
    assert city == "Warszawa"


# ---------------------------------------------------------------------------
# _parse_xml_response
# ---------------------------------------------------------------------------

def test_parse_xml_single_entity():
    xml = (
        '<root><dane>'
        '<Nazwa>Firma ABC</Nazwa>'
        '<Nip>1234567890</Nip>'
        '<Miejscowosc>Leszno</Miejscowosc>'
        '</dane></root>'
    )
    result = _parse_xml_response(xml)
    assert len(result) == 1
    assert result[0]["Nazwa"] == "Firma ABC"
    assert result[0]["Nip"] == "1234567890"


def test_parse_xml_empty():
    assert _parse_xml_response("") == []
    assert _parse_xml_response("   ") == []


# ---------------------------------------------------------------------------
# _enrich_from_gus — happy path
# ---------------------------------------------------------------------------

def _gus_response(name, street, city, zip_code):
    return {"name": name, "street": street, "city": city, "zip": zip_code}


def test_enrich_overrides_creditor_and_debtor():
    data = {
        "cr_nip": "1234567890",
        "d_nip": "9876543210",
        "creditor_name": "Stara nazwa",
        "cr_street": "stary adres",
        "debtor_name": "Inna stara",
    }

    def fake_lookup(nip):
        if nip == "1234567890":
            return _gus_response("Firma ABC Sp. z o.o.", "ul. Skarbowa 2/5", "Leszno", "64-100")
        if nip == "9876543210":
            return _gus_response("Dłużnik XYZ S.A.", "ul. Poznańska 10", "Poznań", "60-001")
        return None

    with patch("demand_generator.gus_lookup.lookup_by_nip", side_effect=fake_lookup):
        result = _enrich_from_gus(data)

    assert result["creditor_name"] == "Firma ABC Sp. z o.o."
    assert result["cr_street"] == "ul. Skarbowa 2/5"
    assert result["cr_city"] == "Leszno"
    assert result["cr_zip"] == "64-100"
    assert result["debtor_name"] == "Dłużnik XYZ S.A."
    assert result["d_street"] == "ul. Poznańska 10"


def test_enrich_no_nip_keeps_json_values():
    data = {
        "creditor_name": "Firma ABC",
        "cr_street": "ul. Stara 1",
        "cr_city": "Leszno",
        "cr_zip": "64-100",
        "debtor_name": "Dłużnik",
        "d_street": "ul. Nowa 2",
        "d_city": "Poznań",
        "d_zip": "60-001",
    }
    result = _enrich_from_gus(data)
    assert result["creditor_name"] == "Firma ABC"
    assert result["cr_street"] == "ul. Stara 1"
    assert result["debtor_name"] == "Dłużnik"


def test_enrich_no_nip_no_json_inserts_marker():
    data = {"creditor_name": "Znane"}  # brak nic poza tym
    result = _enrich_from_gus(data)
    assert result["creditor_name"] == "Znane"
    assert result["cr_street"] == MISSING_DATA_MARKER
    assert result["cr_city"] == MISSING_DATA_MARKER
    assert result["cr_zip"] == MISSING_DATA_MARKER
    # Dłużnik cały brak → cztery markery
    assert result["debtor_name"] == MISSING_DATA_MARKER
    assert result["d_street"] == MISSING_DATA_MARKER


def test_enrich_gus_unavailable_falls_back_to_json():
    data = {
        "cr_nip": "1234567890",
        "creditor_name": "Fallback Sp. z o.o.",
        "cr_street": "ul. Fallback 1",
        "cr_city": "Warszawa",
        "cr_zip": "00-001",
        "debtor_name": "Dłużnik",
        "d_street": "ul. X 1",
        "d_city": "Kraków",
        "d_zip": "30-001",
    }
    with patch(
        "demand_generator.gus_lookup.lookup_by_nip",
        side_effect=GUSLookupError("GUS niedostępny po 3 próbach"),
    ):
        result = _enrich_from_gus(data)

    # Fallback = dane z JSON nietknięte
    assert result["creditor_name"] == "Fallback Sp. z o.o."
    assert result["cr_street"] == "ul. Fallback 1"


def test_enrich_gus_unavailable_no_json_inserts_marker():
    data = {"cr_nip": "1234567890"}  # żadnych pól z JSON
    with patch(
        "demand_generator.gus_lookup.lookup_by_nip",
        side_effect=GUSLookupError("GUS niedostępny"),
    ):
        result = _enrich_from_gus(data)
    assert result["creditor_name"] == MISSING_DATA_MARKER
    assert result["cr_street"] == MISSING_DATA_MARKER


def test_enrich_nip_not_found_falls_back_to_json():
    data = {
        "cr_nip": "0000000000",
        "creditor_name": "Ręcznie wpisana",
        "cr_street": "ul. Ręczna 1",
        "cr_city": "Leszno",
        "cr_zip": "64-100",
    }
    with patch("demand_generator.gus_lookup.lookup_by_nip", return_value=None):
        result = _enrich_from_gus(data)
    assert result["creditor_name"] == "Ręcznie wpisana"
    assert result["cr_street"] == "ul. Ręczna 1"


def test_enrich_only_creditor_nip_debtor_manual():
    """Mixed scenario: wierzyciel z GUS, dłużnik ręcznie z JSON."""
    data = {
        "cr_nip": "1234567890",
        "debtor_name": "Ręcznie wpisany dłużnik",
        "d_street": "ul. Z tekstu 5",
        "d_city": "Wrocław",
        "d_zip": "50-001",
    }

    def fake(nip):
        return _gus_response("Wierzyciel z GUS", "ul. GUS 1", "Leszno", "64-100")

    with patch("demand_generator.gus_lookup.lookup_by_nip", side_effect=fake):
        result = _enrich_from_gus(data)

    assert result["creditor_name"] == "Wierzyciel z GUS"
    assert result["cr_street"] == "ul. GUS 1"
    assert result["debtor_name"] == "Ręcznie wpisany dłużnik"
    assert result["d_street"] == "ul. Z tekstu 5"
