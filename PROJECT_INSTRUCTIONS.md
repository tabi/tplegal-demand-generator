## Generator wezwań do zapłaty — Rekompensa.pl

### Instalacja (wykonaj na starcie konwersacji)
pip install git+https://github.com/tabi/tplegal-demand-generator.git requests holidays --break-system-packages

### Template
Template wezwania znajduje się w Knowledge tego Projectu: `wezwanie_template.docx`
Po instalacji skopiuj go: `cp /mnt/user-data/uploads/wezwanie_template.docx /home/claude/template.docx`

### Workflow: generowanie wezwania

1. Użytkownik podaje dane sprawy (tekst, tabela lub CSV)
2. Zbuduj JSON z danymi (schemat poniżej)
3. Uruchom kalkulator: `calc-rekompensa --json /home/claude/invoices.json > /home/claude/calc_result.json`
4. Zbuduj input JSON dla generatora (dane stron + wyniki kalkulacji)
5. Uruchom generator: `generate-demand --json /home/claude/demand_input.json --template /home/claude/template.docx --output /mnt/user-data/outputs/wezwanie.docx`
6. Pokaż podsumowanie i oddaj plik

### Schemat JSON — kalkulator (invoices.json)
```json
{
  "invoices": [
    {
      "invoice_number": "FV/2024/001",
      "gross": 12500.00,
      "due_date": "2024-03-15",
      "payment_date": "2024-06-20"
    }
  ],
  "lawsuit_date": "2026-04-15"
}
```

### Schemat JSON — generator (demand_input.json)
```json
{
  "creditor_name": "Firma ABC Sp. z o.o.",
  "cr_street": "ul. Skarbowa 2/5",
  "cr_city": "Leszno",
  "cr_zip": "64-100",
  "cr_bank": "PL 12 3456 7890 1234 5678 9012 3456",
  "debtor_name": "Dłużnik XYZ S.A.",
  "d_street": "ul. Poznańska 10",
  "d_city": "Poznań",
  "d_zip": "60-001",
  "assigned_to": "Bartłomieja Przyniczkę",
  "total_compensation_pln": 1234.56,
  "total_interest_pln": 567.89,
  "invoice_numbers": ["FV/2024/001", "FV/2024/002"],
  "invoice_tiers": ["EUR_40", "EUR_70"]
}
```

### Strategia tonalna
Domyślna: standard_collect. Dostępne: soft_collect, standard_collect, hard_collect, pre_litigation.
Zapytaj użytkownika jeśli nie sprecyzował.

### Ważne
- Rekompensata = per faktura (nie per dłużnika)
- Kurs EUR/PLN z NBP — kalkulator pobiera automatycznie
- Przedawnienie: 3 lata + koniec roku — kalkulator filtruje automatycznie
- bank_account może być "___" jeśli wierzyciel go nie podał
