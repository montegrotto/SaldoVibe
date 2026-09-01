"""BAS account number -> SRU code mapping for the INK2 form
(Inkomstdeklaration 2 - aktiebolag / ekonomisk förening).

Shared by Account.save() (auto-fill on account creation) and the
populate_sru_codes management command (bulk backfill/repair). Keep this the
single source of truth for the mapping - do not duplicate the ranges.

Source: BAS-intressenternas förening, "SRU-kopplingar INK2" (intervallformat),
https://www.bas.se/kontoplaner/sru/ (INK2_P1_intervall-241119.xlsx). This is
the same table used to fill in bookkeeping/data/bas_2026_accounts.json and
bookkeeping/data/bas_2026_sru_interval_rules_ink2.json - keep all three in
sync if the mapping changes.
"""

# ---------------------------------------------------------------------------
# Mapping: (from_number_inclusive, to_number_inclusive) -> sru_code
#
# Ordered by account number. Where the source table lists both a "netto +"
# and a "netto -" field code for the same range (e.g. 4900-4909 -> 7411/7510),
# only the positive/default code is kept here, matching the single static
# code stored per account in bas_2026_accounts.json.
# ---------------------------------------------------------------------------
SRU_RANGES = [
    (1000, 1087, "7201"),  # Koncessioner, patent, licenser, varumärken, hyresrätter, goodwill och liknande rättigheter
    (1088, 1088, "7202"),  # Förskott avseende immateriella anläggningstillgångar
    (1089, 1099, "7201"),  # Koncessioner, patent, licenser, varumärken, hyresrätter, goodwill och liknande rättigheter
    (1100, 1119, "7214"),  # Byggnader och mark
    (1120, 1129, "7216"),  # Förbättringsutgifter på annans fastighet
    (1130, 1179, "7214"),  # Byggnader och mark
    (1180, 1189, "7217"),  # Pågående nyanläggningar och förskott avseende materiella anläggningstillgångar
    (1190, 1199, "7214"),  # Byggnader och mark
    (1200, 1279, "7215"),  # Maskiner, inventarier och övriga materiella anläggningstillgångar
    (1280, 1289, "7217"),  # Pågående nyanläggningar och förskott avseende materiella anläggningstillgångar
    (1290, 1299, "7215"),  # Maskiner, inventarier och övriga materiella anläggningstillgångar
    (1310, 1319, "7230"),  # Andelar i koncernföretag
    (1320, 1329, "7232"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1330, 1335, "7231"),  # Andelar i intresseföretag och gemensamt styrda företag
    (1336, 1336, "7233"),  # Ägarintresse i övriga företag och andra långfristiga värdepappersinnehav
    (1337, 1337, "7233"),  # Ägarintresse i övriga företag och andra långfristiga värdepappersinnehav
    (1338, 1339, "7231"),  # Andelar i intresseföretag och gemensamt styrda företag
    (1340, 1345, "7232"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (
        1346,
        1346,
        "7235",
    ),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Andra långfristiga fordringar
    (
        1347,
        1347,
        "7235",
    ),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Andra långfristiga fordringar
    (1348, 1349, "7232"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1350, 1359, "7233"),  # Ägarintresse i övriga företag och andra långfristiga värdepappersinnehav
    (1360, 1369, "7234"),  # Lån till delägare eller närstående
    (
        1370,
        1379,
        "7235",
    ),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Andra långfristiga fordringar
    (
        1380,
        1389,
        "7235",
    ),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Andra långfristiga fordringar
    (1410, 1419, "7241"),  # Råvaror och förnödenheter
    (1420, 1429, "7241"),  # Råvaror och förnödenheter
    (1440, 1449, "7242"),  # Varor under tillverkning
    (1450, 1459, "7243"),  # Färdiga varor och handelsvaror
    (1460, 1469, "7243"),  # Färdiga varor och handelsvaror
    (1470, 1479, "7245"),  # Pågående arbeten för annans räkning
    (1480, 1489, "7246"),  # Förskott till leverantörer
    (1490, 1499, "7244"),  # Övriga lagertillgångar
    (1510, 1559, "7251"),  # Kundfordringar
    (1560, 1569, "7252"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1570, 1572, "7252"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1573, 1573, "7261"),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Övriga fordringar
    (1574, 1579, "7252"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1580, 1589, "7251"),  # Kundfordringar
    (1610, 1619, "7261"),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Övriga fordringar
    (1620, 1629, "7262"),  # Upparbetad men ej fakturerad intäkt
    (1630, 1659, "7261"),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Övriga fordringar
    (1660, 1669, "7252"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1670, 1670, "7261"),  # Fordringar hos övriga företag som det finns ett ägarintresse i (samma familj som 1573/1673)
    (1671, 1672, "7252"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1673, 1673, "7261"),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Övriga fordringar
    (1674, 1679, "7252"),  # Fordringar hos koncern-, intresse- och gemensamt styrda företag
    (1680, 1699, "7261"),  # Fordringar hos övriga företag som det finns ett ägarintresse i och Övriga fordringar
    (1700, 1799, "7263"),  # Förutbetalda kostnader och upplupna intäkter
    (1800, 1859, "7271"),  # Övriga kortfristiga placeringar
    (1860, 1869, "7270"),  # Andelar i koncernföretag
    (1870, 1899, "7271"),  # Övriga kortfristiga placeringar
    (1900, 1999, "7281"),  # Kassa, bank och redovisningsmedel
    (2080, 2089, "7301"),  # Bundet eget kapital
    (2090, 2099, "7302"),  # Fritt eget kapital
    (2110, 2139, "7321"),  # Periodiseringsfonder
    (2150, 2159, "7322"),  # Ackumulerade överavskrivningar
    (2160, 2199, "7323"),  # Övriga obeskattade reserver
    (2210, 2219, "7331"),  # Avsättningar för pensioner och liknande förpliktelser enligt tryggandelagen
    (2220, 2229, "7333"),  # Övriga avsättningar
    (2230, 2239, "7332"),  # Övriga avsättningar för pensioner och liknande förpliktelser
    (2240, 2299, "7333"),  # Övriga avsättningar
    (2310, 2329, "7350"),  # Obligationslån
    (2330, 2339, "7351"),  # Checkräkningskredit
    (2340, 2359, "7352"),  # Övriga skulder till kreditinstitut
    (2360, 2372, "7353"),  # Skulder till koncern-, intresse- och gemensamt styrda företag
    (2373, 2373, "7354"),  # Skulder till övriga företag som det finns ett ägarintresse i och övriga skulder
    (2374, 2379, "7353"),  # Skulder till koncern-, intresse- och gemensamt styrda företag
    (2380, 2399, "7354"),  # Skulder till övriga företag som det finns ett ägarintresse i och övriga skulder
    (2410, 2419, "7361"),  # Övriga skulder till kreditinstitut
    (2420, 2429, "7362"),  # Förskott från kunder
    (2430, 2439, "7363"),  # Pågående arbeten för annans räkning
    (2440, 2449, "7365"),  # Leverantörsskulder
    (2450, 2459, "7364"),  # Fakturerad men ej upparbetad intäkt
    (2460, 2472, "7367"),  # Skulder till koncern-, intresse- och gemensamt styrda företag
    (2473, 2473, "7369"),  # Skulder till övriga företag som det finns ett ägarintresse i (samma familj som 2373/2493)
    (2474, 2479, "7367"),  # Skulder till koncern-, intresse- och gemensamt styrda företag
    (2480, 2489, "7360"),  # Checkräkningskredit
    (2490, 2491, "7369"),  # Skulder till övriga företag som det finns ett ägarintresse i och Övriga skulder
    (2492, 2492, "7366"),  # Växelskulder
    (2493, 2499, "7369"),  # Skulder till övriga företag som det finns ett ägarintresse i och Övriga skulder
    (2500, 2599, "7368"),  # Skatteskulder
    (2600, 2859, "7369"),  # Skulder till övriga företag som det finns ett ägarintresse i och Övriga skulder
    (2860, 2863, "7367"),  # Kortfristiga skulder till koncernföretag (samma familj som 2874-2879)
    (2870, 2873, "7369"),  # Kortfristiga skulder, övriga företag med ägarintresse (samma familj som 2473/2493)
    (2874, 2879, "7367"),  # Skulder till koncern-, intresse- och gemensamt styrda företag
    (2880, 2899, "7369"),  # Skulder till övriga företag som det finns ett ägarintresse i och Övriga skulder
    (2900, 2999, "7370"),  # Upplupna kostnader och förutbetalda intäkter
    (3000, 3799, "7410"),  # Nettoomsättning
    (3800, 3899, "7412"),  # Aktiverat arbete för egen räkning
    (3900, 3999, "7413"),  # Övriga rörelseintäkter
    (4800, 4899, "7511"),  # Andra produktionskostnader (Råvaror och förnödenheter)
    (
        4900,
        4909,
        "7411",
    ),  # Förändring av lager av produkter i arbete, färdiga varor och pågående arbete för annans räkning
    (4910, 4920, "7511"),  # Råvaror och förnödenheter
    (
        4930,
        4959,
        "7411",
    ),  # Förändring av lager av produkter i arbete, färdiga varor och pågående arbete för annans räkning
    (4960, 4969, "7512"),  # Handelsvaror
    (
        4970,
        4979,
        "7411",
    ),  # Förändring av lager av produkter i arbete, färdiga varor och pågående arbete för annans räkning
    (4980, 4989, "7512"),  # Handelsvaror
    (
        4990,
        4999,
        "7411",
    ),  # Förändring av lager av produkter i arbete, färdiga varor och pågående arbete för annans räkning
    (5000, 6999, "7513"),  # Övriga externa kostnader
    (7000, 7699, "7514"),  # Personalkostnader
    (7700, 7739, "7515"),  # Av- och nedskrivningar av materiella och immateriella anläggningstillgångar
    (7740, 7749, "7516"),  # Nedskrivningar av omsättningstillgångar utöver normala nedskrivningar
    (7750, 7789, "7515"),  # Av- och nedskrivningar av materiella och immateriella anläggningstillgångar
    (7790, 7799, "7516"),  # Nedskrivningar av omsättningstillgångar utöver normala nedskrivningar
    (7800, 7899, "7515"),  # Av- och nedskrivningar av materiella och immateriella anläggningstillgångar
    (7900, 7999, "7517"),  # Övriga rörelsekostnader
    (8000, 8069, "7414"),  # Resultat från andelar i koncernföretag
    (8070, 8079, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8080, 8089, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8090, 8099, "7414"),  # Resultat från andelar i koncernföretag
    (8100, 8112, "7415"),  # Resultat från andelar i intresseföretag och gemensamt styrda företag
    (8113, 8113, "7423"),  # Resultat från övriga företag som det finns ett ägarintresse i
    (8114, 8117, "7415"),  # Resultat från andelar i intresseföretag och gemensamt styrda företag
    (8118, 8118, "7423"),  # Resultat från övriga företag som det finns ett ägarintresse i
    (8119, 8122, "7415"),  # Resultat från andelar i intresseföretag och gemensamt styrda företag
    (8123, 8123, "7423"),  # Resultat från övriga företag som det finns ett ägarintresse i
    (8124, 8132, "7415"),  # Resultat från andelar i intresseföretag och gemensamt styrda företag
    (8133, 8133, "7423"),  # Resultat från övriga företag som det finns ett ägarintresse i
    (8134, 8169, "7415"),  # Resultat från andelar i intresseföretag och gemensamt styrda företag
    (8170, 8179, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8180, 8189, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8190, 8199, "7415"),  # Resultat från andelar i intresseföretag och gemensamt styrda företag
    (8200, 8269, "7416"),  # Resultat från övriga anläggningstillgångar
    (8270, 8279, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8280, 8289, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8290, 8299, "7416"),  # Resultat från övriga anläggningstillgångar
    (8300, 8369, "7417"),  # Övriga ränteintäkter och liknande resultatposter
    (8370, 8379, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8380, 8389, "7521"),  # Nedskrivningar av finansiella anläggningstillgångar och kortfristiga placeringar
    (8390, 8399, "7417"),  # Övriga ränteintäkter och liknande resultatposter
    (8400, 8499, "7522"),  # Räntekostnader och liknande resultatposter
    (8810, 8810, "7420"),  # Återföring av periodiseringsfond
    (8811, 8811, "7525"),  # Avsättning till periodiseringsfond
    (8819, 8819, "7420"),  # Återföring av periodiseringsfond
    (8820, 8829, "7419"),  # Mottagna koncernbidrag
    (8830, 8839, "7524"),  # Lämnade koncernbidrag
    (8840, 8849, "7527"),  # Övriga bokslutsdispositioner (skattemässiga avdrag)
    (8850, 8859, "7421"),  # Förändring av överavskrivningar
    (8860, 8899, "7422"),  # Övriga bokslutsdispositioner
    (8900, 8989, "7528"),  # Skatt på årets resultat
    (8990, 8999, "7450"),  # Årets resultat, vinst (flyttas till p. 4.1)
]

# 4000-4799 is genuinely ambiguous by number alone: the source table lists the
# whole range under both 7511 (Råvaror och förnödenheter) and 7512
# (Handelsvaror). BAS distinguishes them by account name instead.
HANDELSVAROR_RANGE = (4000, 4799)


def resolve_sru_code(number, name):
    """Return the SRU code for a BAS account, or '' if no match."""
    try:
        n = int(str(number)[:4])
    except (ValueError, TypeError):
        return ""

    if HANDELSVAROR_RANGE[0] <= n <= HANDELSVAROR_RANGE[1]:
        low_name = (name or "").lower()
        return "7512" if ("handelsvar" in low_name or "vmb" in low_name) else "7511"

    for lo, hi, code in SRU_RANGES:
        if lo <= n <= hi:
            return code
    return ""
