"""Management command: load_bas_accounts
Loads the official BAS 2026 chart of accounts into the database.
Safe to run multiple times – it upserts accounts by company and number.
"""

import io
import re
import ssl
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import certifi
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bookkeeping.models import Account, Company

BAS_2026_XLSX_URL = "https://www.bas.se/wp-content/uploads/2026/04/BAS_kontoplan_2026_v2.xlsx"

BAS_ACCOUNTS = [
    # ── Klass 1: Tillgångar ──────────────────────────────────────────────────
    ("1010", "Balanserade utgifter för forskning och utveckling", "1"),
    ("1011", "Balanserade utgifter för programvaror", "1"),
    ("1100", "Byggnader och markanläggningar", "1"),
    ("1110", "Byggnader", "1"),
    ("1130", "Markanläggningar", "1"),
    ("1150", "Nyttjanderätt till mark", "1"),
    ("1200", "Maskiner och andra tekniska anläggningar", "1"),
    ("1210", "Maskiner och tekniska anläggningar", "1"),
    ("1220", "Inventarier och verktyg", "1"),
    ("1230", "Datorer och kringutrustning", "1"),
    ("1240", "Bilar och andra transportmedel", "1"),
    ("1250", "Leasade tillgångar", "1"),
    ("1300", "Finansiella anläggningstillgångar", "1"),
    ("1310", "Andelar i koncernföretag", "1"),
    ("1320", "Fordringar hos koncernföretag", "1"),
    ("1380", "Andra långfristiga fordringar", "1"),
    ("1400", "Lager och pågående arbeten", "1"),
    ("1410", "Råvaror och förnödenheter", "1"),
    ("1420", "Varor under tillverkning", "1"),
    ("1430", "Färdiga varor och handelsvaror", "1"),
    ("1440", "Pågående arbeten, nedlagda kostnader", "1"),
    ("1460", "Förskott till leverantörer", "1"),
    ("1500", "Kundfordringar", "1"),
    ("1510", "Kundfordringar", "1"),
    ("1511", "Ej förfallna kundfordringar", "1"),
    ("1515", "Tvistiga kundfordringar", "1"),
    ("1519", "Nedskrivning av kundfordringar", "1"),
    ("1560", "Fordringar hos koncernföretag", "1"),
    ("1610", "Fordringar hos koncernföretag (kortfristiga)", "1"),
    ("1630", "Skattekonto", "1"),
    ("1640", "Skattefordringar", "1"),
    ("1650", "Momsfordran", "1"),
    ("1680", "Andra kortfristiga fordringar", "1"),
    ("1710", "Förutbetalda kostnader och upplupna intäkter", "1"),
    ("1720", "Förutbetalda hyreskostnader", "1"),
    ("1730", "Förutbetalda försäkringspremier", "1"),
    ("1790", "Övriga förutbetalda kostnader och upplupna intäkter", "1"),
    ("1800", "Kortfristiga placeringar", "1"),
    ("1810", "Aktier och andelar", "1"),
    ("1900", "Kassa och bank", "1"),
    ("1910", "Kassa", "1"),
    ("1920", "PlusGiro", "1"),
    ("1930", "Företagskonto / checkkonto / affärskonto", "1"),
    ("1940", "Övriga bankkonton", "1"),
    ("1960", "Lån via kontokredit bank", "1"),
    # ── Klass 2: Eget kapital och skulder ───────────────────────────────────
    ("2010", "Aktiekapital", "2"),
    ("2020", "Överkursfond", "2"),
    ("2030", "Kapitalandelsfond", "2"),
    ("2040", "Reservfond", "2"),
    ("2060", "Balanserat resultat", "2"),
    ("2070", "Erhållen/lämnad utdelning (fria fonder)", "2"),
    ("2080", "Årets resultat", "2"),
    ("2085", "Ägarens kapitalkonto (enskild firma)", "2"),
    ("2091", "Mottagna aktieägartillskott", "2"),
    ("2098", "Vinst eller förlust från föregående år", "2"),
    ("2099", "Årets resultat", "2"),
    ("2110", "Periodiseringsfond", "2"),
    ("2120", "Ackumulerade avskrivningar utöver plan", "2"),
    ("2130", "Ersättningsfond", "2"),
    ("2210", "Avsättningar för pensioner", "2"),
    ("2220", "Avsättningar för skatter", "2"),
    ("2230", "Övriga avsättningar", "2"),
    ("2310", "Checkräkningskredit", "2"),
    ("2350", "Skulder till kreditinstitut", "2"),
    ("2360", "Lån från aktieägare", "2"),
    ("2399", "Övriga långfristiga skulder", "2"),
    ("2410", "Leverantörsskulder (utländska)", "2"),
    ("2440", "Leverantörsskulder", "2"),
    ("2450", "Fakturerade inte betalda", "2"),
    ("2460", "Förskott från kunder", "2"),
    ("2490", "Övriga kortfristiga skulder", "2"),
    ("2510", "Skatteskulder", "2"),
    ("2511", "Debiterad preliminärskatt", "2"),
    ("2512", "Slutlig skatt", "2"),
    ("2610", "Utgående moms 25 %", "2"),
    ("2611", "Utgående moms 25 % (SE)", "2"),
    ("2620", "Utgående moms 12 %", "2"),
    ("2630", "Utgående moms 6 %", "2"),
    ("2640", "Ingående moms", "2"),
    ("2645", "Beräknad ingående moms på förvärv", "2"),
    ("2650", "Redovisningskonto för moms", "2"),
    ("2710", "Personalskatt", "2"),
    ("2730", "Lagstadgade sociala avgifter", "2"),
    ("2731", "Avräkning lagstadgade sociala avgifter", "2"),
    ("2732", "Avräkning arbetsgivaravgifter", "2"),
    ("2890", "Övriga kortfristiga skulder", "2"),
    ("2920", "Upplupna löner", "2"),
    ("2930", "Upplupna semesterlöner", "2"),
    ("2940", "Upplupna räntekostnader", "2"),
    ("2960", "Upplupna kostnader och förutbetalda intäkter", "2"),
    ("2990", "Övriga upplupna kostnader och förutbetalda intäkter", "2"),
    # ── Klass 3: Rörelsens intäkter ─────────────────────────────────────────
    ("3010", "Försäljning inom Sverige 25 % moms", "3"),
    ("3020", "Försäljning inom Sverige 12 % moms", "3"),
    ("3030", "Försäljning inom Sverige 6 % moms", "3"),
    ("3040", "Försäljning inom Sverige, momsfri", "3"),
    ("3041", "Försäljning, reducerad moms", "3"),
    ("3100", "Försäljning varor", "3"),
    ("3105", "Försäljning varor, 25 % moms", "3"),
    ("3108", "Försäljning varor, 12 % moms", "3"),
    ("3200", "Försäljning tjänster", "3"),
    ("3210", "Utförda tjänster 25 % moms", "3"),
    ("3211", "Utförda tjänster 12 % moms", "3"),
    ("3212", "Utförda tjänster 6 % moms", "3"),
    ("3300", "Hyresintäkter", "3"),
    ("3310", "Hyra av lokaler", "3"),
    ("3320", "Hyra av inventarier", "3"),
    ("3400", "Royalties och licensintäkter", "3"),
    ("3500", "Provisionsintäkter", "3"),
    ("3590", "Övriga provisionsintäkter", "3"),
    ("3600", "Övriga rörelseintäkter", "3"),
    ("3610", "Valutakursvinster", "3"),
    ("3740", "Öres- och kronutjämning", "3"),
    ("3750", "Lämnade rabatter", "3"),
    ("3960", "Erhållna bidrag", "3"),
    ("3970", "Vinst vid avyttring av anläggningstillgångar", "3"),
    ("3990", "Övriga rörelseintäkter", "3"),
    # ── Klass 4: Varuinköp ──────────────────────────────────────────────────
    ("4000", "Inköp av varor", "4"),
    ("4010", "Inköp av råvaror och förnödenheter", "4"),
    ("4020", "Inköp av handelsvaror", "4"),
    ("4100", "Frakt och transportkostnader vid inköp", "4"),
    ("4200", "Tull och spedition", "4"),
    ("4300", "Inköp av halvfabrikat", "4"),
    ("4400", "Förändring av lager av råvaror", "4"),
    ("4490", "Förändring av lager – handelsvaror", "4"),
    ("4500", "Förändring av lager – pågående arbeten", "4"),
    ("4600", "Förändring av lager – färdiga varor", "4"),
    ("4700", "Underentreprenörer", "4"),
    ("4900", "Övriga rörelsekostnader, varuinköp", "4"),
    # ── Klass 5: Övriga externa kostnader ───────────────────────────────────
    ("5000", "Lokalkostnader", "5"),
    ("5010", "Hyra av lokaler", "5"),
    ("5020", "El, gas och vatten", "5"),
    ("5030", "Uppvärmning", "5"),
    ("5040", "El för belysning", "5"),
    ("5060", "Städning och renhållning", "5"),
    ("5090", "Övriga lokalkostnader", "5"),
    ("5100", "Fastighetskostnader", "5"),
    ("5400", "Förbrukningsinventarier och förbrukningsmaterial", "5"),
    ("5410", "Förbrukningsinventarier", "5"),
    ("5420", "Förbrukningsmaterial", "5"),
    ("5460", "Förbrukningsmaterial, kontorsmaterial", "5"),
    ("5500", "Reparation och underhåll", "5"),
    ("5510", "Reparation och underhåll av maskiner", "5"),
    ("5550", "Reparation och underhåll av inventarier", "5"),
    ("5600", "Transportmedel", "5"),
    ("5610", "Personbilar", "5"),
    ("5611", "Drivmedel personbilar", "5"),
    ("5612", "Försäkring och skatt personbilar", "5"),
    ("5615", "Leasing personbilar", "5"),
    ("5620", "Lastbilar och skåpbilar", "5"),
    ("5630", "Truckar och andra maskiner för godshantering", "5"),
    ("5700", "Frakter och transportkostnader", "5"),
    ("5800", "Resekostnader", "5"),
    ("5810", "Biljetter", "5"),
    ("5820", "Hotell och logi", "5"),
    ("5830", "Traktamente, Sverige", "5"),
    ("5831", "Traktamente, utlandet", "5"),
    ("5890", "Övriga resekostnader", "5"),
    ("5900", "Reklam och PR", "5"),
    ("5910", "Annonsering", "5"),
    ("5920", "Utomhusreklam", "5"),
    ("5960", "Sponsring", "5"),
    ("5990", "Övriga reklamkostnader", "5"),
    # ── Klass 6: Övriga externa kostnader (forts.) ──────────────────────────
    ("6000", "Övriga försäljningskostnader", "6"),
    ("6010", "Kataloger och prislistor", "6"),
    ("6020", "Kundtidningar och egna trycksaker", "6"),
    ("6060", "Facklitteratur", "6"),
    ("6070", "Tidningar och tidskrifter", "6"),
    ("6100", "Kontorsmaterial och trycksaker", "6"),
    ("6110", "Kontorsmaterial", "6"),
    ("6150", "Trycksaker", "6"),
    ("6200", "Tele och post", "6"),
    ("6210", "Fast telefoni", "6"),
    ("6211", "Mobiltelefon", "6"),
    ("6212", "IP-telefoni", "6"),
    ("6220", "Datatrafik", "6"),
    ("6230", "Datakommunikation", "6"),
    ("6250", "Porto", "6"),
    ("6290", "Övriga tele- och postkostnader", "6"),
    ("6300", "IT-kostnader", "6"),
    ("6310", "Förbrukningsmaterial för datorer", "6"),
    ("6320", "Programvaror", "6"),
    ("6330", "Molntjänster och SaaS", "6"),
    ("6350", "Hosting och serverdrift", "6"),
    ("6390", "Övriga IT-kostnader", "6"),
    ("6400", "Företagsförsäkringar och riskkostnader", "6"),
    ("6410", "Företagsförsäkringar", "6"),
    ("6420", "Självrisker vid skador", "6"),
    ("6500", "Övriga externa tjänster", "6"),
    ("6530", "Redovisningstjänster", "6"),
    ("6540", "IT-konsulter", "6"),
    ("6550", "Juridiska tjänster", "6"),
    ("6560", "Revisionstjänster", "6"),
    ("6570", "Bankkostnader", "6"),
    ("6590", "Övriga externa tjänster", "6"),
    ("6700", "Representation", "6"),
    ("6710", "Representation, avdragsgill", "6"),
    ("6720", "Representation, ej avdragsgill", "6"),
    ("6800", "Avskrivningar och nedskrivningar", "6"),
    ("6810", "Avskrivningar på immateriella anläggningstillgångar", "6"),
    ("6820", "Avskrivningar på byggnader", "6"),
    ("6830", "Avskrivningar på maskiner och inventarier", "6"),
    ("6840", "Avskrivningar på bilar och transportmedel", "6"),
    ("6900", "Övriga rörelsekostnader", "6"),
    ("6910", "Licensavgifter och royalties", "6"),
    ("6920", "Böter och sanktionsavgifter", "6"),
    ("6940", "Förlust vid avyttring av anläggningstillgångar", "6"),
    ("6970", "Valutakursförluster", "6"),
    ("6980", "Erhållna bidrag (kostnadsreducerande)", "6"),
    ("6990", "Övriga rörelsekostnader", "6"),
    # ── Klass 7: Anställda och personalkostnader ────────────────────────────
    ("7000", "Löner och arvoden", "7"),
    ("7010", "Löner till tjänstemän", "7"),
    ("7020", "Löner till kollektivanställda", "7"),
    ("7030", "Löner till delägare och närstående", "7"),
    ("7080", "Löner, timanställda", "7"),
    ("7090", "Övriga löner och ersättningar", "7"),
    ("7200", "Sociala och andra avgifter enligt lag och avtal", "7"),
    ("7210", "Arbetsgivaravgifter", "7"),
    ("7220", "Avtalsenliga arbetsgivaravgifter", "7"),
    ("7290", "Övriga lagstadgade sociala kostnader", "7"),
    ("7300", "Pensionskostnader", "7"),
    ("7310", "Avtalspension SAF-LO", "7"),
    ("7320", "ITP-pension", "7"),
    ("7330", "Övriga pensionskostnader", "7"),
    ("7400", "Kostnadsersättningar och förmåner", "7"),
    ("7410", "Traktamenten, Sverige", "7"),
    ("7411", "Traktamenten, utlandet", "7"),
    ("7420", "Bilersättningar", "7"),
    ("7440", "Skattefria kostnadsersättningar", "7"),
    ("7450", "Skattepliktiga kostnadsersättningar", "7"),
    ("7460", "Fri kost och fria måltider", "7"),
    ("7490", "Övriga kostnadsersättningar", "7"),
    ("7500", "Pensionskostnader (bokslut)", "7"),
    ("7510", "Sjuklöner", "7"),
    ("7519", "Sjukförsäkringsavgifter", "7"),
    ("7520", "Arbetsskadeförsäkring", "7"),
    ("7570", "Kollektivavtalad personförsäkring", "7"),
    ("7600", "Personalomkostnader", "7"),
    ("7610", "Utbildning och personalutveckling", "7"),
    ("7620", "Hälsovård och sjukvård", "7"),
    ("7630", "Personalrepresentation", "7"),
    ("7640", "Friskvårdsbidrag", "7"),
    ("7690", "Övriga personalomkostnader", "7"),
    ("7700", "Nedskrivningar av fordringar", "7"),
    ("7800", "Avskrivningar (personal-relaterade)", "7"),
    ("7900", "Erhållna bidrag och stöd för personal", "7"),
    # ── Klass 8: Finansiella och andra poster ───────────────────────────────
    ("8000", "Finansiella intäkter", "8"),
    ("8010", "Erhållen utdelning", "8"),
    ("8020", "Utdelning på andelar i koncernföretag", "8"),
    ("8100", "Ränteintäkter och liknande", "8"),
    ("8110", "Ränteintäkter från bank", "8"),
    ("8120", "Ränteintäkter från fordringar", "8"),
    ("8150", "Valutakursvinster på finansiella poster", "8"),
    ("8200", "Räntekostnader och liknande", "8"),
    ("8210", "Räntekostnader för kortfristiga lån", "8"),
    ("8220", "Räntekostnader för långfristiga lån", "8"),
    ("8230", "Övriga räntekostnader", "8"),
    ("8250", "Valutakursförluster på finansiella poster", "8"),
    ("8300", "Resultat från övriga finansiella tillgångar", "8"),
    ("8310", "Nedskrivningar av finansiella anläggningstillgångar", "8"),
    ("8400", "Bokslutsdispositioner", "8"),
    ("8410", "Förändring av periodiseringsfonder", "8"),
    ("8420", "Förändring av överavskrivningar", "8"),
    ("8700", "Skatt på årets resultat", "8"),
    ("8710", "Inkomstskatt", "8"),
    ("8720", "Uppskjuten skatt", "8"),
    ("8800", "Extraordinära intäkter", "8"),
    ("8900", "Årets resultat", "8"),
]


def _read_cell_text(cell, shared_strings, namespace):
    value = cell.findtext("a:v", default="", namespaces=namespace)
    cell_type = cell.attrib.get("t")
    if cell_type == "s" and value:
        return shared_strings[int(value)]
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
    return value or ""


def _download_bas_workbook(source):
    source_path = Path(source).expanduser()
    if source_path.exists():
        return source_path.read_bytes()

    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(source, context=context, timeout=60) as response:
        return response.read()


def _parse_bas_workbook(workbook_bytes):
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    accounts = {}

    with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for shared_item in shared_root.findall("a:si", namespace):
                shared_strings.append(
                    "".join(node.text or "" for node in shared_item.iter() if node.tag.endswith("}t"))
                )

        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for row in sheet.findall(".//a:sheetData/a:row", namespace):
            values = []
            for cell in row.findall("a:c", namespace):
                value = _read_cell_text(cell, shared_strings, namespace)
                value = " ".join(value.split())
                if value:
                    values.append(value)

            index = 0
            while index < len(values):
                token = values[index]
                if re.fullmatch(r"\d{4}(?:#)?", token):
                    number = token[:4]
                    if index + 1 < len(values):
                        name = values[index + 1].strip()
                        if name:
                            accounts[number] = {
                                "name": name,
                                "account_class": number[0],
                            }
                            index += 2
                            continue
                index += 1

    return accounts


def _load_bas_accounts(source):
    workbook_bytes = _download_bas_workbook(source)
    accounts = _parse_bas_workbook(workbook_bytes)
    if not accounts:
        raise CommandError("Kunde inte läsa några BAS-konton från källfilen.")
    return accounts


class Command(BaseCommand):
    help = "Ladda svenska BAS-kontoplanen i databasen"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            help="Ladda konton för ett specifikt företag (id)",
        )
        parser.add_argument(
            "--source",
            default=BAS_2026_XLSX_URL,
            help="Sökväg eller URL till BAS-kontoplanen i XLSX-format",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        source = options.get("source") or BAS_2026_XLSX_URL
        bas_accounts = _load_bas_accounts(source)

        if company_id:
            companies = Company.objects.filter(pk=company_id, is_active=True)
            if not companies.exists():
                self.stdout.write(self.style.ERROR("Företaget hittades inte eller är inaktivt."))
                return
        else:
            companies = Company.objects.filter(is_active=True)
            if not companies.exists():
                self.stdout.write(self.style.WARNING("Inga aktiva företag finns att ladda kontoplan för."))
                return

        total_created = 0
        total_updated = 0
        for company in companies:
            created_count = 0
            updated_count = 0
            with transaction.atomic():
                for number, account_data in bas_accounts.items():
                    _, created = Account.objects.update_or_create(
                        company=company,
                        number=number,
                        defaults={
                            "name": account_data["name"],
                            "account_class": account_data["account_class"],
                        },
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

            total_created += created_count
            total_updated += updated_count
            self.stdout.write(
                self.style.SUCCESS(
                    f"{company.name}: {created_count} konton skapades, {updated_count} konton uppdaterades."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Klart! Totalt skapade konton: {total_created}. Totalt uppdaterade konton: {total_updated}."
            )
        )
