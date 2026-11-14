---
description: "Så visar, exporterar och stänger du momsperioder i SaldoVibe – underlaget till momsdeklarationen hos Skatteverket."
---

# 3. Momsrapport

Momsrapportering styrs per företag av inställningen **Momsperiod** (`Inställningar → Företag`).
Är den satt till "Ingen" visas inte **Momsrapport** i menyn alls, och alla momsvyer avvisar med
"Momsrapportering är avstängd för aktivt företag."

## Visa momsrapport

1. Gå till **Bokföring → Momsrapport**.
2. Välj räkenskapsår och period (perioderna följer företagets valda momsperiod, t.ex. månad/kvartal/år).
3. Rapporten visar Skatteverkets momsrutor i blankettens avsnitt A–I (05–08, 10–12, 20–24,
   30–32, 35–42, 48, 49, 50 och 60–62) beräknade från bokförda verifikationer i perioden, med
   möjlighet att klicka in på varje rutas underliggande verifikationer. Listan visar för varje
   verifikation det belopp som ingår i just den rutan (för ruta 49 verifikationens bidrag till
   nettomomsen, negativt när moms ska fås tillbaka).
4. Momsberäkningen tar hänsyn till företagets **momsstartdatum** – transaktioner före det datumet
   räknas inte in även om de ligger inom vald period.
5. Innan export/stängning valideras rapporten och eventuella fel eller varningar visas direkt i
   vyn (t.ex. saknade momsfältkoder på konton).

### Vilken ruta ett konto hamnar i

Varje konto har ett **momsfält** (`Bokföring → Kontoplan`) som avgör vilken ruta beloppen räknas
in i. BAS-kontoplanen som läggs upp för nya företag har fälten förifyllda:

- Försäljning: 3001–3003 → 05, 3004 (momsfri) → 42, 3108 (varor till annat EU-land) → 35, 3105
  (varor utanför EU) → 36, 3308 (tjänster till annat EU-land) → 39, 3305 (tjänster utanför EU)
  → 40, 3231 (byggtjänster med omvänd skattskyldighet) → 41, 3913 (frivilligt momspliktig hyra)
  → 08.
- Inköp med omvänd skattskyldighet: 4075–4077 och 4515–4517 → 20, 4535–4537 → 21, 4531–4533 → 22,
  4065–4067 och 4415–4417 → 23, 4425–4427 → 24. Huvudkontona 4070, 4510, 4060, 4410 och 4420 har
  samma ruta som sina underkonton. Den beräknade momsen bokförs på 2614/2624/2634 (ruta 30–32)
  mot 2645 (ruta 48).
- Import av varor: 4085–4087 och 4545–4547 → 50 (beskattningsunderlaget) och importmomsen på
  2615/2625/2635 → 60–62 mot 2645 (ruta 48).
- Inhemska inköp har ingen ruta. Bara momsen på 2641 (ruta 48) ingår i deklarationen.

Konton utan momsfält räknas inte in, med undantag för 30xx och 261x–264x som faller tillbaka på
standardrutorna 05, 10–12 och 48.

## Exportera till Skatteverket

**Momsrapport → Exportera** genererar en XML-fil (eSKD-format) för vald period. Export blockeras
om valideringen hittar fel – då måste underliggande bokföring rättas innan export kan göras.

Både export och periodstängning kräver behörigheten `vat.close_period` (se rollmatrisen i
`docs/compliance/role-matrix.md`); själva momsrapporten kan alla i företaget se.

## Stänga en momsperiod

Att stänga perioden bokför momsen mot avräkningskontot **2650** och låser perioden mot dubbel
stängning:

1. Klicka **Stäng period** på vald period.
2. Kontot 2650 måste finnas och vara aktivt i kontoplanen – annars avbryts stängningen med ett
   felmeddelande.
3. Systemet skapar en stängningsverifikation daterad periodens slutdatum, med momsrutorna bokförda
   mot 2650. 2650 får exakt det deklarerade beloppet i hela kronor (ruta 49) – samma belopp som
   Skatteverket drar på skattekontot – och öresskillnaden mot momskontona bokförs på **3740**
   (öres- och kronutjämning). Finns det en öresskillnad måste 3740 finnas och vara aktivt.
4. En redan stängd period går inte att stänga igen ("Momsperioden är redan stängd.").
5. Är periodens slutdatum redan periodlåst avbryts stängningen ("Perioden för stängningsdatumet
   är låst. Lås upp perioden innan momsperioden stängs.").
6. Finns inga momsbalanser att stänga för perioden görs ingen bokning.

Stängningen skapar samtidigt ett internt bevis (källfingeravtryck + lista på källverifikationer)
som används för spårbarhet – se `docs/compliance/skatteverket-controls.md` för detaljerna kring
detta. Om perioden inte redan är periodlåst visas efter stängningen ett tips om att låsa den
under **Inställningar → Periodlåsning**, så att underlaget för momsdeklarationen inte kan ändras
i efterhand; ändras bokföringen i en stängd men olåst period flaggas det i
[Compliance-översikten](10-rapporter.md#compliance-översikt).

## Nästa steg

Fortsätt till [4. Bilagor](04-bilagor.md).
