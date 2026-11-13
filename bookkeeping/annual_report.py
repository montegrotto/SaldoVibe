"""Årsredovisning enligt K2 (BFNAR 2016:10) för aktiebolag, byggd ur huvudboken.

Uppställningsformerna följer Bolagsverkets K2-taxonomi för aktiebolag (2024-09-12):
kostnadsslagsindelad resultaträkning och fullständig balansräkning. Varje post är ett
eller flera intervall av BAS-kontonummer - samma indelning som BAS-kontoplanen är byggd
efter. Belopp i hela kronor, jämförelseåret i andra kolumnen. Fritexter och underskrifter
kommer från AnnualReport; allt annat beräknas här.

Teckenkonvention: alla belopp hämtas som kredit - debet och negeras för klass 1, så att
tillgångar, eget kapital, skulder och intäkter är positiva och kostnader negativa.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F, Q, Sum

from .models import AccountingYear, JournalEntry

ZERO = Decimal("0")
# Bolagsskatt 20,6 % => 79,4 % av obeskattade reserver räknas som eget kapital i soliditeten.
EQUITY_SHARE_OF_UNTAXED_RESERVES = Decimal("0.794")
# 8990/8999 är resultatöverföringen (bokslutsverifikation S1) - inte en resultatpost.
RESULT_TRANSFER_RANGE = (8990, 8999)
# Konton som ett aktiebolag inte använder (enskild firma/föreningar) och som därför
# inte finns i uppställningen.
NOT_FOR_AB_RANGE = (2010, 2079)


@dataclass(frozen=True)
class Post:
    label: str
    ranges: tuple = ()
    key: str = ""  # särskild beräkning (se _post_amount) eller notkoppling


@dataclass(frozen=True)
class Group:
    label: str
    children: tuple
    sum_label: str = ""
    key: str = ""


@dataclass(frozen=True)
class Subtotal:
    """Löpande summa av alla poster före denna i trädet (resultaträkningens delresultat)."""

    label: str
    grand: bool = False


def _r(*ranges):
    return tuple(ranges)


INCOME_STATEMENT = (
    Group(
        "Rörelseintäkter, lagerförändringar m.m.",
        (
            Post("Nettoomsättning", _r((3000, 3799))),
            Post(
                "Förändring av lager av produkter i arbete, färdiga varor och pågående arbete för annans räkning",
                _r((4900, 4909), (4930, 4959), (4970, 4979)),
            ),
            Post("Aktiverat arbete för egen räkning", _r((3800, 3899))),
            Post("Övriga rörelseintäkter", _r((3900, 3999))),
        ),
        "Summa rörelseintäkter, lagerförändringar m.m.",
    ),
    Group(
        "Rörelsekostnader",
        (
            Post("Råvaror och förnödenheter", _r((4000, 4899), (4910, 4929))),
            Post("Handelsvaror", _r((4960, 4969), (4980, 4999))),
            Post("Övriga externa kostnader", _r((5000, 6999))),
            Post("Personalkostnader", _r((7000, 7699)), key="personnel"),
            Post("Av- och nedskrivningar av materiella och immateriella anläggningstillgångar", _r((7700, 7899))),
            Post("Övriga rörelsekostnader", _r((7900, 7999))),
        ),
        "Summa rörelsekostnader",
    ),
    Subtotal("Rörelseresultat"),
    Group(
        "Finansiella poster",
        (
            Post("Resultat från andelar i koncernföretag", _r((8000, 8099))),
            Post("Resultat från andelar i intresseföretag och gemensamt styrda företag", _r((8100, 8199))),
            Post("Resultat från övriga finansiella anläggningstillgångar", _r((8200, 8269), (8290, 8299))),
            Post("Övriga ränteintäkter och liknande resultatposter", _r((8300, 8369), (8390, 8399))),
            Post(
                "Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar",
                _r((8270, 8289), (8370, 8389)),
            ),
            Post("Räntekostnader och liknande resultatposter", _r((8400, 8799))),
        ),
        "Summa finansiella poster",
    ),
    Subtotal("Resultat efter finansiella poster"),
    Group(
        "Bokslutsdispositioner",
        (
            Post("Erhållna koncernbidrag", _r((8820, 8829))),
            Post("Lämnade koncernbidrag", _r((8830, 8839))),
            Post("Förändring av periodiseringsfonder", _r((8810, 8819))),
            Post("Förändring av överavskrivningar", _r((8850, 8859))),
            Post("Övriga bokslutsdispositioner", _r((8800, 8809), (8840, 8849), (8860, 8899))),
        ),
        "Summa bokslutsdispositioner",
    ),
    Subtotal("Resultat före skatt"),
    Group(
        "Skatter",
        (
            Post("Skatt på årets resultat", _r((8900, 8979))),
            Post("Övriga skattekostnader", _r((8980, 8989))),
        ),
    ),
    Subtotal("Årets resultat", grand=True),
)

INTANGIBLE_POSTS = (
    Post("Koncessioner, patent, licenser, varumärken samt liknande rättigheter", _r((1000, 1059)), key="fa"),
    Post("Hyresrätter och liknande rättigheter", _r((1060, 1069)), key="fa"),
    Post("Goodwill", _r((1070, 1079)), key="fa"),
    Post("Övriga immateriella anläggningstillgångar", _r((1090, 1099)), key="fa"),
    Post("Pågående förbättringar och förskott avseende immateriella anläggningstillgångar", _r((1080, 1089))),
)
TANGIBLE_POSTS = (
    Post("Byggnader och mark", _r((1100, 1119), (1130, 1179)), key="fa"),
    Post("Maskiner och andra tekniska anläggningar", _r((1210, 1219), (1230, 1249)), key="fa"),
    Post("Inventarier, verktyg och installationer", _r((1220, 1229), (1250, 1269)), key="fa"),
    Post("Förbättringsutgifter på annans fastighet", _r((1120, 1129)), key="fa"),
    Post("Övriga materiella anläggningstillgångar", _r((1200, 1209), (1270, 1279), (1290, 1299)), key="fa"),
    Post(
        "Pågående nyanläggningar och förskott avseende materiella anläggningstillgångar", _r((1180, 1199), (1280, 1289))
    ),
)

BALANCE_SHEET = (
    Group(
        "Tillgångar",
        (
            Group(
                "Anläggningstillgångar",
                (
                    Group(
                        "Immateriella anläggningstillgångar",
                        INTANGIBLE_POSTS,
                        "Summa immateriella anläggningstillgångar",
                    ),
                    Group("Materiella anläggningstillgångar", TANGIBLE_POSTS, "Summa materiella anläggningstillgångar"),
                    Group(
                        "Finansiella anläggningstillgångar",
                        (
                            Post("Andelar i koncernföretag", _r((1300, 1319))),
                            Post("Fordringar hos koncernföretag", _r((1320, 1329))),
                            Post("Andelar i intresseföretag och gemensamt styrda företag", _r((1330, 1339))),
                            Post("Fordringar hos intresseföretag och gemensamt styrda företag", _r((1340, 1349))),
                            Post("Andra långfristiga värdepappersinnehav", _r((1350, 1359))),
                            Post("Lån till delägare eller närstående", _r((1360, 1369))),
                            Post("Andra långfristiga fordringar", _r((1370, 1399))),
                        ),
                        "Summa finansiella anläggningstillgångar",
                    ),
                ),
                "Summa anläggningstillgångar",
            ),
            Group(
                "Omsättningstillgångar",
                (
                    Group(
                        "Varulager m.m.",
                        (
                            Post("Råvaror och förnödenheter", _r((1400, 1429))),
                            Post("Varor under tillverkning", _r((1430, 1449))),
                            Post("Färdiga varor och handelsvaror", _r((1450, 1469))),
                            Post("Pågående arbete för annans räkning", _r((1470, 1479))),
                            Post("Förskott till leverantörer", _r((1480, 1489))),
                            Post("Övriga lagertillgångar", _r((1490, 1499))),
                        ),
                        "Summa varulager m.m.",
                    ),
                    Group(
                        "Kortfristiga fordringar",
                        (
                            Post("Kundfordringar", _r((1500, 1559))),
                            Post("Fordringar hos koncernföretag", _r((1560, 1569), (1660, 1669))),
                            Post(
                                "Fordringar hos intresseföretag och gemensamt styrda företag",
                                _r((1570, 1579), (1670, 1679)),
                            ),
                            Post("Övriga fordringar", _r((1580, 1619), (1630, 1659), (1680, 1699))),
                            Post("Upparbetad men ej fakturerad intäkt", _r((1620, 1629))),
                            Post("Förutbetalda kostnader och upplupna intäkter", _r((1700, 1799))),
                        ),
                        "Summa kortfristiga fordringar",
                    ),
                    Group(
                        "Kortfristiga placeringar",
                        (
                            Post("Andelar i koncernföretag", _r((1860, 1869))),
                            Post("Övriga kortfristiga placeringar", _r((1800, 1859), (1870, 1899))),
                        ),
                        "Summa kortfristiga placeringar",
                    ),
                    Group(
                        "Kassa och bank",
                        (
                            Post("Kassa och bank", _r((1900, 1989)), key="cash"),
                            Post("Redovisningsmedel", _r((1990, 1999))),
                        ),
                        "Summa kassa och bank",
                    ),
                ),
                "Summa omsättningstillgångar",
            ),
        ),
        "Summa tillgångar",
        key="assets",
    ),
    Group(
        "Eget kapital och skulder",
        (
            Group(
                "Eget kapital",
                (
                    Group(
                        "Bundet eget kapital",
                        (
                            Post("Aktiekapital", _r((2080, 2081))),
                            Post("Ej registrerat aktiekapital", _r((2082, 2082))),
                            Post("Uppskrivningsfond", _r((2085, 2085))),
                            Post("Reservfond", _r((2086, 2086))),
                            Post("Bunden överkursfond", _r((2087, 2087))),
                            Post("Övrigt bundet eget kapital", _r((2083, 2084), (2088, 2089))),
                        ),
                        "Summa bundet eget kapital",
                    ),
                    Group(
                        "Fritt eget kapital",
                        (
                            Post("Fri överkursfond", _r((2097, 2097))),
                            Post("Balanserat resultat", _r((2090, 2096), (2098, 2099)), key="retained"),
                            Post("Årets resultat", key="result"),
                        ),
                        "Summa fritt eget kapital",
                    ),
                ),
                "Summa eget kapital",
                key="equity",
            ),
            Group(
                "Obeskattade reserver",
                (
                    Post("Periodiseringsfonder", _r((2110, 2149))),
                    Post("Ackumulerade överavskrivningar", _r((2150, 2159))),
                    Post("Övriga obeskattade reserver", _r((2100, 2109), (2160, 2199))),
                ),
                "Summa obeskattade reserver",
                key="untaxed",
            ),
            Group(
                "Avsättningar",
                (
                    Post(
                        "Avsättningar för pensioner och liknande förpliktelser enligt tryggandelagen", _r((2210, 2219))
                    ),
                    Post("Övriga avsättningar för pensioner och liknande förpliktelser", _r((2230, 2239))),
                    Post("Övriga avsättningar", _r((2200, 2209), (2220, 2229), (2240, 2299))),
                ),
                "Summa avsättningar",
            ),
            Group(
                "Långfristiga skulder",
                (
                    Post("Obligationslån", _r((2300, 2329))),
                    Post("Kontokredit", _r((2330, 2339))),
                    Post("Övriga skulder till kreditinstitut", _r((2340, 2359))),
                    Post("Skulder till koncernföretag", _r((2360, 2369))),
                    Post("Skulder till intresseföretag och gemensamt styrda företag", _r((2370, 2379))),
                    Post("Övriga skulder", _r((2380, 2399))),
                ),
                "Summa långfristiga skulder",
            ),
            Group(
                "Kortfristiga skulder",
                (
                    Post("Kontokredit", _r((2480, 2489))),
                    Post("Övriga skulder till kreditinstitut", _r((2410, 2419))),
                    Post("Förskott från kunder", _r((2420, 2429))),
                    Post("Pågående arbete för annans räkning", _r((2430, 2439))),
                    Post("Fakturerad men ej upparbetad intäkt", _r((2450, 2459))),
                    Post("Leverantörsskulder", _r((2440, 2449))),
                    Post("Skulder till koncernföretag", _r((2460, 2469), (2860, 2869))),
                    Post("Skulder till intresseföretag och gemensamt styrda företag", _r((2470, 2479), (2870, 2879))),
                    Post("Skatteskulder", _r((2500, 2599))),
                    Post("Övriga skulder", _r((2400, 2409), (2490, 2499), (2600, 2859), (2880, 2899))),
                    Post("Upplupna kostnader och förutbetalda intäkter", _r((2900, 2999))),
                ),
                "Summa kortfristiga skulder",
            ),
        ),
        "Summa eget kapital och skulder",
        key="equity_and_liabilities",
    ),
)


def iter_posts(nodes):
    for node in nodes:
        if isinstance(node, Post):
            yield node
        elif isinstance(node, Group):
            yield from iter_posts(node.children)


def kr(amount):
    """Hela kronor, avrundat från öre (halva uppåt, även för negativa belopp)."""
    return int(Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Huvudbokssiffror per räkenskapsår
# ---------------------------------------------------------------------------


@dataclass
class YearFigures:
    """Kontosaldon för ett räkenskapsår: utgående kredit−debet (kumulativt), periodens
    kredit−debet, periodens debet och kredit, allt per kontonummer."""

    start_date: object
    end_date: object
    cumulative: dict = field(default_factory=dict)
    period: dict = field(default_factory=dict)
    period_debit: dict = field(default_factory=dict)
    period_credit: dict = field(default_factory=dict)

    def _sum(self, table, ranges, *, sign=True):
        total = ZERO
        for number, amount in table.items():
            if any(lo <= number <= hi for lo, hi in ranges):
                total += -amount if (sign and number < 2000) else amount
        return total

    def closing(self, *ranges):
        """Utgående balans, tillgångar positiva (debet), skulder/eget kapital positiva (kredit)."""
        return self._sum(self.cumulative, ranges)

    def opening(self, *ranges):
        return self.closing(*ranges) - self.movement(*ranges)

    def movement(self, *ranges):
        """Periodens nettoförändring med samma tecken som closing()."""
        return self._sum(self.period, ranges)

    def debits(self, *ranges):
        return self._sum(self.period_debit, ranges, sign=False)

    def credits(self, *ranges):
        return self._sum(self.period_credit, ranges, sign=False)

    def result(self):
        """Årets resultat: kredit−debet över klass 3-8, exklusive resultatöverföringen 8990-8999."""
        return self.movement((3000, 8989))

    def result_transfer(self):
        """Vad bokslutsverifikationen fört över till 2099 (0 om året inte är avslutat)."""
        return -self.movement(RESULT_TRANSFER_RANGE)

    def has_entries(self):
        return any(self.cumulative.values())


def load_year_figures(company, start_date, end_date):
    rows = (
        JournalEntry.objects.filter(account__company=company, transaction__date__lte=end_date)
        .values("account__number")
        .annotate(
            cumulative=Sum(F("credit") - F("debit")),
            period=Sum(F("credit") - F("debit"), filter=Q(transaction__date__gte=start_date)),
            period_debit=Sum("debit", filter=Q(transaction__date__gte=start_date)),
            period_credit=Sum("credit", filter=Q(transaction__date__gte=start_date)),
        )
    )
    figures = YearFigures(start_date, end_date)
    for row in rows:
        try:
            number = int(row["account__number"])
        except (TypeError, ValueError):
            continue
        figures.cumulative[number] = row["cumulative"] or ZERO
        figures.period[number] = row["period"] or ZERO
        figures.period_debit[number] = row["period_debit"] or ZERO
        figures.period_credit[number] = row["period_credit"] or ZERO
    return figures


# ---------------------------------------------------------------------------
# Uppställningarna
# ---------------------------------------------------------------------------


def _post_amount(post, figures, *, cumulative, result):
    if figures is None:
        return None
    if post.key == "result":
        return result
    amount = figures.closing(*post.ranges) if cumulative else figures.movement(*post.ranges)
    if post.key == "retained":
        # 2099 innehåller årets resultat först när S1 är bokförd - dra bort det så att
        # "Årets resultat" alltid visas på egen rad, avslutat år eller inte.
        amount -= figures.result_transfer()
    return kr(amount)


def _build_rows(nodes, figures, prev_figures, *, cumulative, results, level=0, totals=None):
    """Rader för mallen. Poster utan belopp båda åren utelämnas, liksom tomma grupper."""
    rows = []
    running = [0, 0]
    group_total = [0, 0]
    for node in nodes:
        if isinstance(node, Post):
            cur = _post_amount(node, figures, cumulative=cumulative, result=results[0])
            prev = _post_amount(node, prev_figures, cumulative=cumulative, result=results[1])
            group_total[0] += cur or 0
            group_total[1] += prev or 0
            if cur or prev or node.key == "cash":  # kassaraden bär öresavrundningen
                rows.append(
                    {"kind": "post", "label": node.label, "level": level, "cur": cur, "prev": prev, "key": node.key}
                )
        elif isinstance(node, Group):
            sub_rows, sub_total = _build_rows(
                node.children,
                figures,
                prev_figures,
                cumulative=cumulative,
                results=results,
                level=level + 1,
                totals=totals,
            )
            group_total[0] += sub_total[0]
            group_total[1] += sub_total[1]
            if totals is not None and node.key:
                totals[node.key] = sub_total
            if not sub_rows:
                continue
            rows.append({"kind": "heading", "label": node.label, "level": level, "cur": None, "prev": None})
            rows.extend(sub_rows)
            if node.sum_label:
                rows.append(
                    {
                        "kind": "sum",
                        "label": node.sum_label,
                        "level": level,
                        "cur": sub_total[0] if figures else None,
                        "prev": sub_total[1] if prev_figures else None,
                    }
                )
        elif isinstance(node, Subtotal):
            running[0] += group_total[0]
            running[1] += group_total[1]
            group_total = [0, 0]
            rows.append(
                {
                    "kind": "total" if node.grand else "subtotal",
                    "label": node.label,
                    "level": level,
                    "cur": running[0] if figures else None,
                    "prev": running[1] if prev_figures else None,
                }
            )
    return rows, [running[0] + group_total[0], running[1] + group_total[1]]


def income_statement_rows(figures, prev_figures):
    rows, totals = _build_rows(INCOME_STATEMENT, figures, prev_figures, cumulative=False, results=(None, None))
    return rows, totals


def balance_sheet_rows(figures, prev_figures, results):
    totals = {}
    rows, _ = _build_rows(BALANCE_SHEET, figures, prev_figures, cumulative=True, results=results, totals=totals)
    # ponytail: öresavrundning per post gör att sidorna kan skilja några kronor; differensen
    # läggs på "Kassa och bank" (raden finns alltid) i stället för en egen avrundningsrad.
    for column, col in ((0, "cur"), (1, "prev")):
        if column == 1 and prev_figures is None:
            continue
        diff = totals["assets"][column] - totals["equity_and_liabilities"][column]
        if not diff:
            continue
        for row in rows:
            if row.get("key") == "cash" or (
                row["kind"] == "sum"
                and row["label"] in {"Summa kassa och bank", "Summa omsättningstillgångar", "Summa tillgångar"}
            ):
                row[col] = (row[col] or 0) - diff
        totals["assets"][column] -= diff
    return rows, totals


# ---------------------------------------------------------------------------
# Förvaltningsberättelsens tabeller och noterna
# ---------------------------------------------------------------------------


def previous_years(company, year, count):
    return list(
        AccountingYear.objects.filter(company=company, end_date__lt=year.start_date).order_by("-end_date")[:count]
    )


def multi_year_overview(company, year, figures):
    """Flerårsöversikt: nettoomsättning, resultat efter finansiella poster och soliditet,
    innevarande år plus upp till tre tidigare."""
    columns = []
    for item, item_figures in [(year, figures)] + [
        (prev, load_year_figures(company, prev.start_date, prev.end_date)) for prev in previous_years(company, year, 3)
    ]:
        if not item_figures.has_entries():
            continue
        result = item_figures.result()
        equity = item_figures.closing((2080, 2099)) - item_figures.result_transfer() + result
        untaxed = item_figures.closing((2100, 2199))
        total_assets = item_figures.closing((1000, 1999))
        solidity = (equity + untaxed * EQUITY_SHARE_OF_UNTAXED_RESERVES) / total_assets * 100 if total_assets else None
        columns.append(
            {
                "year": item,
                "net_sales": kr(item_figures.movement((3000, 3799))),
                "result_after_financial": kr(item_figures.movement((3000, 8799))),
                "solidity": solidity.quantize(Decimal("0.1")) if solidity is not None else None,
            }
        )
    return columns


EQUITY_COLUMNS = (
    ("Aktiekapital", ((2080, 2084),)),
    ("Övrigt bundet eget kapital", ((2085, 2089),)),
    ("Fritt eget kapital", ((2090, 2098),)),
)


def equity_changes(figures, retained_closing, result):
    """Förändringar i eget kapital (förvaltningsberättelsen). Kolumner: bundet, fritt,
    årets resultat, totalt. ponytail: utdelning särredovisas inte utan ingår i
    'Disposition enligt årsstämmans beslut' - lägg till en rad om det efterfrågas."""
    opening = [kr(figures.opening(*ranges)) for _, ranges in EQUITY_COLUMNS] + [kr(figures.opening((2099, 2099)))]
    closing = [kr(figures.closing(*ranges)) for _, ranges in EQUITY_COLUMNS[:2]] + [retained_closing, result]
    disposition = [closing[i] - opening[i] for i in range(3)] + [-opening[3]]
    year_result = [0, 0, 0, result]
    labels = [label for label, _ in EQUITY_COLUMNS] + ["Årets resultat"]

    def with_total(values):
        return values + [sum(values)]

    return {
        "columns": labels + ["Totalt"],
        "rows": [
            ("Belopp vid årets ingång", with_total(opening)),
            ("Disposition enligt årsstämmans beslut", with_total(disposition)),
            ("Årets resultat", with_total(year_result)),
            ("Belopp vid årets utgång", with_total(closing)),
        ],
    }


def average_employees(company, year):
    """Medelantal anställda: sysselsättningsgrad viktad med anställningstid under året.
    ponytail: avslutade anställningar saknar slutdatum i löneregistret och räknas inte."""
    from payroll.models import Employee

    days_in_year = (year.end_date - year.start_date).days + 1
    total = ZERO
    for employee in Employee.objects.filter(company=company, is_active=True):
        start = max(employee.start_date or year.start_date, year.start_date)
        if start > year.end_date:
            continue
        days = (year.end_date - start).days + 1
        total += Decimal(employee.employment_rate) / 100 * days / days_in_year
    return kr(total)


def depreciation_policies(company):
    """Avskrivningstider per K2-post från anläggningsregistret, t.ex. 'Inventarier ... 5 år'."""
    from fixed_assets.models import FixedAsset, FixedAssetType

    account_by_type = {
        asset_type.key: int(asset_type.asset_account.number)
        for asset_type in FixedAssetType.objects.filter(company=company).select_related("asset_account")
        if asset_type.asset_account_id
    }
    lives = {}
    for asset_type, months in (
        FixedAsset.objects.filter(company=company).values_list("asset_type", "useful_life_months").distinct()
    ):
        number = account_by_type.get(asset_type)
        if number is None:
            continue
        for post in INTANGIBLE_POSTS + TANGIBLE_POSTS:
            if any(lo <= number <= hi for lo, hi in post.ranges):
                lives.setdefault(post.label, set()).add(months)
    policies = []
    for post in INTANGIBLE_POSTS + TANGIBLE_POSTS:
        if post.label in lives:
            years = sorted(Decimal(m) / 12 for m in lives[post.label])
            policies.append((post.label, ", ".join(f"{y.normalize():f} år".replace(".", ",") for y in years)))
    return policies


def fixed_asset_notes(figures, prev_figures):
    """Anläggningsnoter per post: anskaffningsvärden, avskrivningar, nedskrivningar och
    redovisat värde. BAS-konvention: xxx9 = ackumulerade avskrivningar, xxx8 = nedskrivningar."""
    notes = []
    for post in INTANGIBLE_POSTS + TANGIBLE_POSTS:
        if post.key != "fa":
            continue
        acquisition = tuple((lo, hi) for lo, hi in post.ranges)
        numbers = [n for n in figures.cumulative if any(lo <= n <= hi for lo, hi in acquisition)]
        if prev_figures:
            numbers += [n for n in prev_figures.cumulative if any(lo <= n <= hi for lo, hi in acquisition)]
        if not numbers:
            continue
        acq = tuple((n, n) for n in set(numbers) if n % 10 not in (8, 9))
        dep = tuple((n, n) for n in set(numbers) if n % 10 == 9)
        imp = tuple((n, n) for n in set(numbers) if n % 10 == 8)

        def block(fig):
            if fig is None:
                return None
            acq_open = kr(fig.opening(*acq)) if acq else 0
            acq_close = kr(fig.closing(*acq)) if acq else 0
            dep_open = kr(fig.opening(*dep)) if dep else 0
            dep_close = kr(fig.closing(*dep)) if dep else 0
            imp_open = kr(fig.opening(*imp)) if imp else 0
            imp_close = kr(fig.closing(*imp)) if imp else 0
            return {
                "acq_open": acq_open,
                "purchases": kr(fig.debits(*acq)) if acq else 0,
                "disposals": -kr(fig.credits(*acq)) if acq else 0,
                "acq_close": acq_close,
                "dep_open": dep_open,
                "dep_year": dep_close - dep_open,
                "dep_close": dep_close,
                "imp_open": imp_open,
                "imp_year": imp_close - imp_open,
                "imp_close": imp_close,
                "book_value": acq_close + dep_close + imp_close,
            }

        cur = block(figures)
        prev = block(prev_figures)
        if not any(cur.values()) and not (prev and any(prev.values())):
            continue
        notes.append(
            {
                "post": post.label,
                "cur": cur,
                "prev": prev,
                "has_impairment": bool(
                    cur["imp_close"] or cur["imp_open"] or (prev and (prev["imp_close"] or prev["imp_open"]))
                ),
            }
        )
    return notes


def unmapped_accounts(figures):
    """Konton med saldo som inte ingår i någon post (t.ex. enskild firmas 2010-serie)."""
    mapped = [r for post in iter_posts(INCOME_STATEMENT + BALANCE_SHEET) for r in post.ranges]
    skip = (RESULT_TRANSFER_RANGE,)
    return sorted(
        n
        for n, amount in figures.cumulative.items()
        if amount and not any(lo <= n <= hi for lo, hi in mapped + list(skip))
    )


def parse_signatories(text):
    people = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, role = line.partition(",")
        people.append({"name": name.strip(), "role": role.strip()})
    return people


# ---------------------------------------------------------------------------
# Hela kontexten
# ---------------------------------------------------------------------------


def build_annual_report_context(company, year, report):
    figures = load_year_figures(company, year.start_date, year.end_date)
    prev_year = next(iter(previous_years(company, year, 1)), None)
    prev_figures = load_year_figures(company, prev_year.start_date, prev_year.end_date) if prev_year else None

    rr_rows, rr_totals = income_statement_rows(figures, prev_figures)
    results = (rr_totals[0], rr_totals[1] if prev_figures else None)
    br_rows, br_totals = balance_sheet_rows(figures, prev_figures, results)
    retained_closing = next((row["cur"] for row in br_rows if row.get("key") == "retained"), 0) or 0

    result = results[0]
    free_equity = retained_closing + result + kr(figures.closing((2097, 2097)))
    dividend = kr(report.dividend or 0)

    notes = [
        {"title": "Redovisningsprinciper", "kind": "policies", "policies": depreciation_policies(company)},
        {
            "title": "Medelantal anställda",
            "kind": "employees",
            "cur": report.average_employees
            if report.average_employees is not None
            else average_employees(company, year),
            "prev": average_employees(company, prev_year) if prev_year else None,
        },
    ]
    for note in fixed_asset_notes(figures, prev_figures):
        notes.append({"title": note["post"], "kind": "fixed_asset", **note})
    notes.append(
        {
            "title": "Ställda säkerheter och eventualförpliktelser",
            "kind": "text",
            "lines": [
                ("Ställda säkerheter", report.pledged_assets),
                ("Eventualförpliktelser", report.contingent_liabilities),
            ],
        }
    )
    for number, note in enumerate(notes, start=1):
        note["number"] = number
    note_numbers = {note["title"]: note["number"] for note in notes}
    for row in br_rows:
        if row.get("key") == "fa":
            row["note"] = note_numbers.get(row["label"])
    for row in rr_rows:
        if row.get("key") == "personnel":
            row["note"] = note_numbers["Medelantal anställda"]

    is_closed = figures.result_transfer() != 0 or not figures.has_entries()
    warnings = []
    if not is_closed:
        warnings.append(
            "Räkenskapsåret är inte avslutat – siffrorna är preliminära tills bokslutsverifikationen är bokförd."
        )
    if prev_year is None:
        warnings.append("Inget föregående räkenskapsår finns – jämförelsekolumnen utelämnas.")
    unmapped = unmapped_accounts(figures)
    if unmapped:
        warnings.append(
            "Konton med saldo utanför uppställningen: "
            + ", ".join(str(n) for n in unmapped)
            + ". Flytta saldona till konton som ett aktiebolag använder."
        )
    if not parse_signatories(report.signatories):
        warnings.append("Ange vilka som skriver under årsredovisningen (styrelsen och eventuell VD).")

    if not report.adoption_date:
        warnings.append(
            "Ange datum då årsredovisningen gjordes klar – Bolagsverket kräver en egen datering utöver underskriftsdatumet."
        )

    signatories = parse_signatories(report.signatories)
    return {
        "company": company,
        "year": year,
        "prev_year": prev_year,
        "report": report,
        "has_ceo": any(p["role"].lower() in {"vd", "verkställande direktör"} for p in signatories),
        "is_closed": is_closed,
        "warnings": warnings,
        "rr_rows": rr_rows,
        "br_rows": br_rows,
        "result": result,
        "multi_year": multi_year_overview(company, year, figures),
        "equity_changes": equity_changes(figures, retained_closing, result),
        "disposition": {
            "retained": retained_closing + kr(figures.closing((2097, 2097))),
            "result": result,
            "total": free_equity,
            "dividend": dividend,
            "carried_forward": free_equity - dividend,
        },
        "notes": notes,
        "signatories": signatories,
        "certifier": parse_signatories(report.certifier)[:1],
    }
