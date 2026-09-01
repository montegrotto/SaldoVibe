# Bidra

Buggrapporter och pull requests är välkomna. Svenska eller engelska går lika bra.

## Innan du skickar en PR

- Läs [CLAUDE.md](CLAUDE.md) – den beskriver arkitekturen, konventionerna och vad som är delade
  byggblock. Den är skriven för AI-assistenter men gäller lika mycket för människor.
- Kör kontrollerna som CI kör (se "Utvecklingskontroller" i [README](README.md)):
  `ruff check .`, `ruff format --check .` och `python manage.py test`. Aktivera gärna git-hookarna
  (`git config core.hooksPath .githooks`) så sker det automatiskt.
- Ändrat något som användarhandboken (`docs/user-guide/`) beskriver? Uppdatera kapitlet i samma PR.
- Ändrat `requirements*.txt`? Generera om lock-filerna (se README, "Beroenden").

## Vad som är svårt att få in

- Nya betalda tredjepartsintegrationer. Appen ska vara kostnadsfri i drift.
- Ändringar i revisionskedjan, periodlåsningen eller verifikationsnumreringen utan en tydlig
  koppling till Bokföringslagen/BFNAR – de delarna är efterlevnadskritiska.
- Nya fält i BAS-datafilen som kommer från BAS betalprodukter (se
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

Genom att skicka en PR går du med på att ditt bidrag licensieras under projektets MIT-licens.
