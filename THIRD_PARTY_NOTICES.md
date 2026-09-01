# Tredjepartsmaterial

Koden i SaldoVibe är MIT-licensierad (se [LICENSE](LICENSE)). Följande material i repot kommer från
andra parter och omfattas **inte** av den licensen.

## BAS-kontoplanen

Kontonummer och kontonamn i `bookkeeping/data/bas_2026_accounts.json` kommer från BAS-kontoplanen 2026
v1.1, publicerad fritt av BAS-intressenternas Förening på [bas.se](https://www.bas.se/kontoplaner/).
SRU-koderna kommer från BAS och Skatteverkets kopplingstabeller. Momskodsmappningen
(`vat_field_code`) är SaldoVibes egen.

Filen innehåller enbart det BAS publicerar gratis. Konteringsinstruktioner, engelska kontonamn,
normalsaldon, motkonton och andra uppgifter ur BAS betalprodukter ingår inte och ska inte läggas till.

## FreeTSA

`auditlog/data/freetsa_cacert.pem` är rotcertifikatet för den fria RFC 3161-tidsstämplingstjänsten
[freetsa.org](https://freetsa.org), som används för extern förankring av revisionskedjan.

## Python- och npm-beroenden

Installerade paket (`requirements*.lock`, `package-lock.json`) distribueras under sina egna licenser
(MIT, BSD, Apache-2.0, PSF m.fl.). Ingen är copyleft.
