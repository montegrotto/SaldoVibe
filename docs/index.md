---
title: Bokföringsprogram med öppen källkod för svenska företag
description: "SaldoVibe är ett gratis, självhostat bokföringsprogram med öppen källkod för svenska aktiebolag och enskilda firmor – BAS-kontoplan, SIE, moms, fakturering, löner med AGI."
---

SaldoVibe är ett bokföringsprogram med öppen källkod (MIT-licens) som du kör på egen server. Det är
byggt för svenska aktiebolag och enskilda firmor och följer Bokföringslagen och BFNAR 2013:2:
BAS-kontoplan, SIE-import och SIE-export, momsdeklaration, kundfakturor med Peppol e-faktura,
leverantörsfakturor, löner med AGI-rapportering till Skatteverket och en hash-kedjad, oföränderlig
verifikationslogg. Gratis att använda, granska och vidareutveckla.

[Användarhandboken](user-guide/README.md) · [Installation och drift](ops/README.md) ·
[Källkod på GitHub](https://github.com/montegrotto/SaldoVibe)

## Funktioner

- **Löpande bokföring** – verifikationer med dubbel bokföring, mallar, korrigeringar och
  periodlåsning. BAS-kontoplanen ingår.
- **SIE-import och SIE-export** – flytta bokföringen från ett annat bokföringsprogram, eller lämna
  underlag till revisor, som SIE 4-fil.
- **Momsrapport** – momsdeklarationsunderlag per period, export och stängning av momsperioder.
- **Kundfakturor** – kunder, artiklar, fakturering, påminnelser, kreditfakturor och e-faktura i
  Peppol BIS Billing 3.0.
- **Leverantörsfakturor och bilagor** – registrering med lokal OCR-tolkning av underlaget, betalning,
  QR-kod och e-postimport av bilagor.
- **Bank och skattekonto** – bankkällor, import av kontoutdrag och snabbbokföring, även för
  skattekontot.
- **Löner** – anställda, lönekörning och arbetsgivardeklaration på individnivå (AGI) direkt till
  Skatteverkets API.
- **Anläggningstillgångar** – tillgångstyper och avskrivningar.
- **Rapporter** – balansräkning, resultaträkning, huvudbok, SRU-underlag och bokslut.
- **Efterlevnad** – hash-kedjad revisionslogg med extern RFC 3161-tidsstämpling, periodlåsning,
  händelselogg och systemdokumentation enligt Bokföringslagen och BFNAR 2013:2.

## Varför öppen källkod på egen server?

- **Gratis, utan abonnemang.** MIT-licens, inga användaravgifter och inga betalda integrationer i
  drift: OCR körs lokalt och tidsstämplingen använder en fri tjänst.
- **Din bokföring stannar hos dig.** Databas, bilagor och personuppgifter ligger på din egen server,
  inte hos en tredje part.
- **Granska koden.** Allt är öppet på GitHub, där buggar rapporteras som issues.

## Kom igång

Färdiga Docker-imager för amd64 och arm64 finns på Docker Hub. Med `docker-compose.yml` och en
`.env`-fil startar du appen tillsammans med PostgreSQL och nginx.
[Driftdokumentationen](ops/README.md) beskriver installation, backup och uppgraderingar, och
[användarhandboken](user-guide/README.md) tar dig från registrering till första verifikationen.

## För vem?

SaldoVibe riktar sig till dig som redan kan bokföring och vill sköta den själv för ett aktiebolag
eller en enskild firma. Appen ger struktur, kontroller och efterlevnad men automatiserar inte bort
bokföringskunskapen. Den används i verklig bokföring, men testningen är inte komplett: granska
alltid rapporter och deklarationsunderlag innan de lämnas in.
