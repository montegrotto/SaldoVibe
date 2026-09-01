# 3. Momsrapport

Momsrapportering styrs per företag av inställningen **Momsperiod** (`Inställningar → Företag`).
Är den satt till "Ingen" visas inte **Momsrapport** i menyn alls, och alla momsvyer avvisar med
"Momsrapportering är avstängd för aktivt företag."

## Visa momsrapport

1. Gå till **Bokföring → Momsrapport**.
2. Välj räkenskapsår och period (perioderna följer företagets valda momsperiod, t.ex. månad/kvartal/år).
3. Rapporten visar Skatteverkets momsrutor (05, 06, 10, 11, 12, 30–39, 48, 49, 50 m.fl.) beräknade
   från bokförda verifikationer i perioden, med möjlighet att klicka in på varje rutas
   underliggande verifikationer.
4. Momsberäkningen tar hänsyn till företagets **momsstartdatum** – transaktioner före det datumet
   räknas inte in även om de ligger inom vald period.
5. Innan export/stängning valideras rapporten och eventuella fel eller varningar visas direkt i
   vyn (t.ex. saknade momsfältkoder på konton).

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
   mot 2650.
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
