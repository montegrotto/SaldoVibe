import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from django import forms

from bookkeeping.form_utils import normalize_decimal_fields

from .models import BankAccount, BankAccountType, BankProfile, BankTransaction

SWEDISH_BANK_PROFILE_CHOICES = BankProfile.choices


PROFILE_DEFINITIONS = {
    "generic": {
        "date": ["date", "datum"],
        "description": ["description", "text", "beskrivning", "meddelande"],
        "amount": ["amount", "belopp", "summa"],
        "balance": ["balance", "saldo"],
        "external_id": ["external_id", "id", "transaction_id", "transaktionsid", "referens"],
        "inflow": ["insattning", "insättning", "in", "debit"],
        "outflow": ["uttag", "out", "credit"],
    },
    "skatteverket": {
        "date": [
            "bokforingsdag",
            "bokföringsdag",
            "bokforingsdatum",
            "bokföringsdatum",
            "bokfringsdag",
            "bokfringsdatum",
            "datum",
            "transaktionsdatum",
        ],
        "description": ["transaktion", "beskrivning", "text", "handelse", "händelse", "typ", "referens"],
        "amount": ["belopp", "summa", "amount", "insattninguttag", "insättninguttag", "insttninguttag"],
        "balance": [
            "saldo",
            "kontosaldo",
            "balance",
            "bokfortsaldo",
            "bokfört saldo",
            "bokfrtsaldo",
            "aktuelltsaldo",
        ],
        "external_id": ["id", "referens", "transaktionsid", "verifikationsnummer", "händelseid", "referensswish"],
        "inflow": ["insattning", "insättning", "debet"],
        "outflow": ["uttag", "kredit"],
    },
    # Exportformat: "Radnr;Clearingnr;Kontonr;Produkt;Valuta;Bokföringsdag;Transaktionsdag;
    # Valutadag;Referens;Beskrivning;Belopp;Bokfört saldo"
    "swedbank": {
        "date": ["bokforingsdag", "bokföringsdag", "transaktionsdag", "datum"],
        "description": ["beskrivning", "text", "meddelande"],
        "amount": ["belopp"],
        "balance": ["bokfortsaldo", "bokfört saldo", "saldo"],
        "external_id": ["referens", "id", "transaktionsid"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat (sv): "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta"
    # (en): "Booking date;Amount;Sender;Recipient;Name;Title;Balance;Currency"
    "nordea": {
        "date": ["bokforingsdag", "bokföringsdag", "bookingdate", "transactiondate", "datum"],
        "description": ["rubrik", "title", "namn", "name", "text", "message", "beskrivning"],
        "amount": ["amount", "belopp"],
        "balance": ["balance", "saldo"],
        "external_id": ["transactionid", "id", "referens"],
        "inflow": ["insattning", "insättning", "in"],
        "outflow": ["uttag", "out"],
    },
    # Exportformat: "Bokföringsdatum;Valutadatum;Verifikationsnummer;Text/mottagare;Belopp;Saldo"
    "seb": {
        "date": ["bokforingsdatum", "bokföringsdatum", "bokforingsdag", "bokföringsdag", "datum"],
        "description": ["textmottagare", "text/mottagare", "text", "beskrivning", "meddelande"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["verifikationsnummer", "transaktionsid", "id", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat (företagskonto): "Kontohavare;Kontonr;IBAN;BIC;Kontoform;Valuta;
    # Kontoförande kontor;Datum intervall;Kontor;Bokföringsdag;Reskontradag;Valutadag;Referens;
    # Insättning/Uttag;Bokfört saldo;Aktuellt saldo;Valutadagssaldo;Referens Swish;Avsändar-id Swish"
    # Obs: "referens" får inte vara external_id-alias — filen kan ha två identiska legitima rader
    # (samma referenstext) som då skulle dedupliceras bort; radbaserat fallback-id skiljer dem åt.
    "handelsbanken": {
        "date": ["bokforingsdag", "bokföringsdag", "datum"],
        "description": ["text", "beskrivning", "referens"],
        "amount": ["belopp", "insattninguttag", "insättning/uttag"],
        "balance": ["bokfortsaldo", "bokfört saldo", "saldo"],
        "external_id": ["id", "transaktionsid"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat (sv): "Bokföringsdatum;Specifikation;Belopp;Saldo" (dk-rubriker som fallback)
    "danske_bank": {
        "date": ["bokforingsdatum", "bokföringsdatum", "bogforingsdato", "bogføringsdato", "datum"],
        "description": ["specifikation", "tekst", "text", "beskrivning"],
        "amount": ["belopp", "belob", "beløb", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens"],
        "inflow": ["insattning", "insättning", "in"],
        "outflow": ["uttag", "out"],
    },
    # Exportformat: "Bokföringsdatum;Transaktionsdatum;Meddelande;Belopp;Saldo"
    "lansforsakringar": {
        "date": ["bokforingsdatum", "bokföringsdatum", "transaktionsdatum", "datum", "bokforingsdag"],
        "description": ["meddelande", "text", "beskrivning"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens", "transaktionsid"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat: "Bokföringsdag;Valutadag;Verifikationsnummer;Text;Belopp;Saldo"
    "skandiabanken": {
        "date": ["bokforingsdag", "bokföringsdag", "datum", "date"],
        "description": ["text", "beskrivning", "meddelande"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["verifikationsnummer", "id", "referens", "transactionid"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat: "Datum;Text;Typ;Budgetgrupp;Belopp;Saldo"
    "ica_banken": {
        "date": ["datum", "date"],
        "description": ["text", "beskrivning"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat: "Datum;Konto;Typ av transaktion;Värdepapper/beskrivning;Antal;Kurs;
    # Belopp;Courtage;Valuta;ISIN" (kontohändelser saknar saldokolumn)
    "avanza": {
        "date": ["datum", "date"],
        "description": [
            "vardepapperbeskrivning",
            "värdepapper/beskrivning",
            "typavtransaktion",
            "text",
            "beskrivning",
            "typ",
        ],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "orderid", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    # Exportformat (transaktionslista): "Id;Bokföringsdag;Affärsdag;Likviddag;Transaktionstyp;
    # Värdepapper;...;Belopp;Saldo;...;Transaktionstext;...;Verifikationsnummer"
    "nordnet": {
        "date": ["bokforingsdag", "bokföringsdag", "affarsdag", "affärsdag", "datum", "date"],
        "description": ["transaktionstext", "transaktionstyp", "text", "beskrivning", "händelse"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["verifikationsnummer", "id", "referens", "transactionid"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    "svea": {
        "date": ["bokforingsdatum", "bokföringsdatum", "datum", "date"],
        "description": ["text", "meddelande", "beskrivning"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    "resurs": {
        "date": ["bokforingsdatum", "bokföringsdatum", "datum", "date"],
        "description": ["text", "meddelande", "beskrivning"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    "marginalen": {
        "date": ["bokföringsdatum", "datum", "date"],
        "description": ["transaktionstext", "text", "beskrivning"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
    "collector": {
        "date": ["bokforingsdatum", "bokföringsdatum", "datum", "date"],
        "description": ["text", "meddelande", "beskrivning"],
        "amount": ["belopp", "amount"],
        "balance": ["saldo", "balance"],
        "external_id": ["id", "referens"],
        "inflow": ["insattning", "insättning"],
        "outflow": ["uttag"],
    },
}


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = (
            "name",
            "account_number",
            "account_type",
            "default_bank_profile",
            "bookkeeping_account",
            "is_active",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "account_number": forms.TextInput(attrs={"class": "form-control"}),
            "account_type": forms.Select(attrs={"class": "form-select"}),
            "default_bank_profile": forms.Select(attrs={"class": "form-select"}),
            "bookkeeping_account": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        if company is not None:
            self.fields["bookkeeping_account"].queryset = company.accounts.filter(is_active=True).order_by("number")

        account_type_choices = list(self.fields["account_type"].choices)
        if not self.instance.pk:
            self.fields["account_type"].choices = [
                (value, label) for value, label in account_type_choices if value != BankAccountType.TAX
            ]
        elif self.instance.account_type == BankAccountType.TAX:
            self.fields["account_type"].disabled = True
            self.fields["bookkeeping_account"].disabled = True


class BankImportForm(forms.Form):
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(),
        label="Bankkälla",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    csv_file = forms.FileField(
        label="CSV-fil",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".csv,.txt",
                "hidden": True,
                "onchange": "if(this.files.length)this.form.requestSubmit()",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)
        if company is not None:
            self.fields["bank_account"].queryset = company.bank_accounts.filter(is_active=True).order_by("name")


class BankTransactionBookForm(forms.Form):
    counter_account = forms.ModelChoiceField(
        queryset=None,
        label="Motkonto",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    description = forms.CharField(
        label="Beskrivning",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company")
        super().__init__(*args, **kwargs)
        self.fields["counter_account"].queryset = company.accounts.filter(is_active=True).order_by("number")


class BankTransactionManualForm(forms.ModelForm):
    class Meta:
        model = BankTransaction
        fields = ("bank_account", "date", "description", "amount")
        widgets = {
            "bank_account": forms.Select(attrs={"class": "form-select"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        if company is not None:
            self.fields["bank_account"].queryset = company.bank_accounts.filter(is_active=True).order_by("name")


def _normalize_amount(value):
    cleaned = (value or "").strip().replace("\xa0", "").replace(" ", "").replace("\u2212", "-")
    cleaned = re.sub(r"[^0-9,\.\-+]", "", cleaned)

    if cleaned.count(",") > 0 and cleaned.count(".") > 0:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    if not cleaned:
        raise ValueError("Belopp saknas")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Ogiltigt belopp: {value}") from exc


def _parse_date(value):
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Datum saknas")

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Ogiltigt datum: {value}")


def _decode_input(data):
    if isinstance(data, str):
        return data

    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Kunde inte läsa filens teckenkodning")


def _normalize_header(value):
    raw = (value or "").strip().lower()
    replacements = {
        "å": "a",
        "ä": "a",
        "ö": "o",
        "é": "e",
        "ø": "o",
        "æ": "ae",
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    raw = re.sub(r"[^a-z0-9]", "", raw)
    return raw


def _build_column_map(fieldnames):
    result = {}
    for name in fieldnames:
        if not name:
            continue
        result[_normalize_header(name)] = name
    return result


def _header_skeleton(value):
    return re.sub(r"[aeiouy]", "", _normalize_header(value))


def _resolve_column(columns, aliases):
    for alias in aliases:
        key = _normalize_header(alias)
        if key in columns:
            return columns[key]

    alias_skeletons = {_header_skeleton(alias) for alias in aliases}
    for normalized_key, original_name in columns.items():
        if _header_skeleton(normalized_key) in alias_skeletons:
            return original_name
    return None


def _sniff_delimiter(data):
    first_lines = "\n".join(data.splitlines()[:3])
    candidates = [";", ",", "\t", "|"]
    best = ","
    best_score = -1
    for delim in candidates:
        score = first_lines.count(delim)
        if score > best_score:
            best = delim
            best_score = score
    return best


def _get_profile_definition(bank_profile):
    if bank_profile in ("auto", ""):
        return PROFILE_DEFINITIONS["generic"]
    return PROFILE_DEFINITIONS.get(bank_profile, PROFILE_DEFINITIONS["generic"])


def _parse_skatteverket_headerless_csv(data):
    rows = []
    reader = csv.reader(StringIO(data), delimiter=";", quotechar='"')
    for idx, row in enumerate(reader, start=1):
        if len(row) < 4:
            continue

        raw_date = (row[0] or "").strip()
        raw_description = (row[1] or "").strip()
        raw_amount = (row[2] or "").strip()
        raw_balance = (row[3] or "").strip()

        # Header/summary lines in Skatteverkets export often lack a transaction date/amount.
        if not raw_date or not raw_amount:
            continue

        try:
            tx_date = _parse_date(raw_date)
            amount = _normalize_amount(raw_amount)
        except ValueError:
            continue

        balance = _normalize_amount(raw_balance) if raw_balance else None
        description = raw_description or "Saknar text"
        external_id = f"line-{idx}-{tx_date.isoformat()}-{description}-{amount}"
        rows.append(
            {
                "date": tx_date,
                "description": description,
                "amount": amount,
                "balance": balance,
                "external_id": external_id,
            }
        )

    return rows


def parse_bank_csv(file_obj, bank_profile="auto"):
    data = file_obj.read()
    data = _decode_input(data)

    lines = data.splitlines()
    if lines and re.match(r"^\s*sep\s*=\s*.+$", lines[0], re.IGNORECASE):
        data = "\n".join(lines[1:])

    delimiter = _sniff_delimiter(data)
    reader = csv.DictReader(StringIO(data), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV-filen saknar kolumnrubriker")

    columns = _build_column_map(reader.fieldnames)
    profile = _get_profile_definition(bank_profile)

    date_col = _resolve_column(columns, profile["date"])
    desc_col = _resolve_column(columns, profile["description"])
    amount_col = _resolve_column(columns, profile["amount"])
    balance_col = _resolve_column(columns, profile["balance"])
    id_col = _resolve_column(columns, profile["external_id"])
    inflow_col = _resolve_column(columns, profile.get("inflow", []))
    outflow_col = _resolve_column(columns, profile.get("outflow", []))

    if not date_col or not desc_col or (not amount_col and not (inflow_col and outflow_col)):
        if bank_profile == "skatteverket":
            parsed_rows = _parse_skatteverket_headerless_csv(data)
            if parsed_rows:
                return parsed_rows

        found = ", ".join(reader.fieldnames)
        raise ValueError(
            f"CSV måste innehålla kolumnerna date/datum, description/text och amount/belopp. Hittade kolumner: {found}"
        )

    rows = []
    for idx, row in enumerate(reader, start=1):
        raw_date = (row.get(date_col) or "").strip()
        if not raw_date:
            continue

        tx_date = _parse_date(row.get(date_col))
        tx_desc = (row.get(desc_col) or "").strip()

        if amount_col:
            raw_amount = (row.get(amount_col) or "").strip()
            if not raw_amount:
                continue
            amount = _normalize_amount(row.get(amount_col))
        else:
            inflow = _normalize_amount(row.get(inflow_col) or "0")
            outflow = _normalize_amount(row.get(outflow_col) or "0")
            amount = inflow - outflow

        balance_raw = row.get(balance_col) if balance_col else ""
        balance = _normalize_amount(balance_raw) if str(balance_raw or "").strip() else None
        external_id = (row.get(id_col) or "").strip() if id_col else ""
        if not external_id:
            external_id = f"line-{idx}-{tx_date.isoformat()}-{amount}"

        rows.append(
            {
                "date": tx_date,
                "description": tx_desc or "Saknar text",
                "amount": amount,
                "balance": balance,
                "external_id": external_id,
            }
        )

    return rows
