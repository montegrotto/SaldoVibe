#!/usr/bin/env python3
"""MCP-server (stdio) som låter Claude Desktop och andra agenter läsa och bokföra i SaldoVibe
via /api/v1/. Bara standardbiblioteket: kör med valfri python3 >= 3.10, inget att installera.

Miljövariabler (API-nyckeln skapas på sidan Mobilappen i SaldoVibe):
  SALDOVIBE_URL         t.ex. https://saldovibe.example.se
  SALDOVIBE_TOKEN       API-nyckeln
  SALDOVIBE_COMPANY_ID  standardföretag; varje verktyg tar även foretag_id

Protokollet är MCP över stdio: ett JSON-RPC-meddelande per rad. Bara verktyg (tools),
inga resurser eller promptar.
"""

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

INSTRUCTIONS = """\
SaldoVibe är ett svenskt bokföringsprogram (BAS-kontoplan, dubbel bokföring, Bokföringslagen).
- Slå upp konton med sok_konton i stället för att gissa kontonummer.
- Kör alltid forhandsgranska_verifikation först, visa förslaget och eventuella varningar för
  användaren och bokför med bokfor_verifikation först när användaren godkänt.
- En bokförd verifikation kan inte ändras eller tas bort, bara rättas med en
  korrigeringsverifikation. Bokför därför aldrig på chans.
- Varje verifikation ska ha underlag: ladda upp kvitto/faktura med ladda_upp_bilaga och skicka
  med bilagans id i bilagor.
- Betalningar av kund- och leverantörsfakturor och poster på bankkonton som har en bankkoppling
  hör hemma i SaldoVibes webbgränssnitt; följ varningarna i svaret."""

COMPANY = {"foretag_id": {"type": "integer", "description": "Företagets id (standard: SALDOVIBE_COMPANY_ID)."}}
DATE = {"type": "string", "description": "ÅÅÅÅ-MM-DD"}
VOUCHER = {
    "type": "object",
    "properties": {
        "datum": DATE,
        "beskrivning": {"type": "string"},
        "rader": {
            "type": "array",
            "minItems": 2,
            "description": "Konteringsrader; summa debet måste vara lika med summa kredit.",
            "items": {
                "type": "object",
                "properties": {
                    "konto": {"type": "string", "description": "Kontonummer, t.ex. 6540."},
                    "debet": {"type": "string", "description": "Belopp, t.ex. 199.00"},
                    "kredit": {"type": "string"},
                },
                "required": ["konto"],
            },
        },
        "bilagor": {"type": "array", "items": {"type": "integer"}, "description": "Id från ladda_upp_bilaga."},
        **COMPANY,
    },
    "required": ["datum", "beskrivning", "rader"],
}


def schema(required=(), **properties):
    return {"type": "object", "properties": {**properties, **COMPANY}, "required": list(required)}


def _http(method, url, headers, body):
    """(status, response bytes). The tests swap this for Django's test client."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def api(method, path, args, *, query=None, body=None, upload=None):
    url = os.environ["SALDOVIBE_URL"].rstrip("/") + "/api/v1/" + path
    query = {key: value for key, value in (query or {}).items() if value not in (None, "")}
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {
        "Authorization": f"Bearer {os.environ['SALDOVIBE_TOKEN']}",
        "X-Company-Id": str(args.get("foretag_id") or os.environ.get("SALDOVIBE_COMPANY_ID", "")),
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif upload is not None:
        boundary = uuid.uuid4().hex
        content_type = mimetypes.guess_type(upload.name)[0] or "application/octet-stream"
        data = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{upload.name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + upload.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    status, raw = _http(method, url, headers, data)
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = {"error": f"Oväntat svar från servern (HTTP {status})."}
    return status, payload


def _voucher_body(args, dry_run):
    return {
        "date": args.get("datum"),
        "description": args.get("beskrivning"),
        "rows": [
            {"account": row.get("konto"), "debit": row.get("debet"), "credit": row.get("kredit")}
            for row in args.get("rader") or []
        ],
        "attachment_ids": args.get("bilagor") or [],
        "dry_run": dry_run,
    }


# name -> (description, input schema, handler(args) -> (status, payload))
TOOLS = {
    "lista_foretag": (
        "Företagen du har tillgång till (id, namn, momsregistrerat, läsbehörighet).",
        schema(),
        lambda a: api("GET", "me/", a),
    ),
    "oversikt": (
        "Läget i företaget: saldon, att göra, förfallna fakturor.",
        schema(),
        lambda a: api("GET", "oversikt/", a),
    ),
    "sok_konton": (
        "Aktiva konton i kontoplanen. sok matchar början av kontonumret eller del av namnet.",
        schema(sok={"type": "string"}),
        lambda a: api("GET", "konton/", a, query={"q": a.get("sok")}),
    ),
    "lista_verifikationer": (
        "Bokförda verifikationer med konteringsrader, nyast först (högst 200).",
        schema(
            fran=DATE,
            till=DATE,
            konto={"type": "string", "description": "Bara verifikationer med rad på kontot."},
            sok={"type": "string", "description": "Del av beskrivningen."},
        ),
        lambda a: api(
            "GET",
            "verifikationer/",
            a,
            query={"from": a.get("fran"), "to": a.get("till"), "account": a.get("konto"), "q": a.get("sok")},
        ),
    ),
    "hamta_verifikation": (
        "En verifikation med rader, bilagor och eventuella korrigeringar.",
        schema(["id"], id={"type": "integer"}),
        lambda a: api("GET", f"verifikationer/{int(a['id'])}/", a),
    ),
    "forhandsgranska_verifikation": (
        "Kontrollerar en verifikation (konton, balans, räkenskapsår, låsta perioder, bilagor) och visar "
        "hur den skulle bokföras, utan att spara något. Kör alltid före bokfor_verifikation.",
        VOUCHER,
        lambda a: api("POST", "verifikationer/", a, body=_voucher_body(a, dry_run=True)),
    ),
    "bokfor_verifikation": (
        "Bokför en verifikation i serien för manuella verifikationer. Går inte att ångra - bara rätta "
        "med korrigeringsverifikation. Bara efter forhandsgranska_verifikation och användarens godkännande.",
        VOUCHER,
        lambda a: api("POST", "verifikationer/", a, body=_voucher_body(a, dry_run=False)),
    ),
    "lista_bilagor": (
        "Uppladdade bilagor som ännu inte är kopplade till något, med tolkade fält (datum, belopp, leverantör).",
        schema(),
        lambda a: api("GET", "bilagor/", a),
    ),
    "ladda_upp_bilaga": (
        "Laddar upp ett kvitto eller en faktura (PDF, PNG eller JPEG) från en lokal sökväg. Svaret har "
        "bilagans id och tolkade fält.",
        schema(["sokvag"], sokvag={"type": "string", "description": "Absolut sökväg till filen."}),
        lambda a: api("POST", "bilagor/", a, upload=Path(a["sokvag"]).expanduser()),
    ),
    "resultatrakning": (
        "Resultaträkningen. ar = räkenskapsårets id (standard: innevarande), fran_manad/till_manad = 1-12.",
        schema(ar={"type": "integer"}, fran_manad={"type": "integer"}, till_manad={"type": "integer"}),
        lambda a: api(
            "GET",
            "resultatrakning/",
            a,
            query={"year": a.get("ar"), "from_month": a.get("fran_manad"), "to_month": a.get("till_manad")},
        ),
    ),
    "balansrakning": (
        "Balansräkningen per räkenskapsårets sista dag. ar = räkenskapsårets id (standard: innevarande).",
        schema(ar={"type": "integer"}),
        lambda a: api("GET", "balansrakning/", a, query={"year": a.get("ar")}),
    ),
}


def call_tool(name, args):
    if name not in TOOLS:
        return {"content": [{"type": "text", "text": f"Okänt verktyg: {name}"}], "isError": True}
    try:
        status, payload = TOOLS[name][2](args)
    except (OSError, KeyError, ValueError) as exc:  # no file, server down, missing env/argument
        status, payload = 0, {"error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": text}], "isError": not 200 <= status < 300}


def handle(message):
    """The JSON-RPC response to one message, or None for a notification."""
    method, message_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "saldovibe", "version": "1.0"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": name, "description": description, "inputSchema": input_schema}
                for name, (description, input_schema, _) in TOOLS.items()
            ]
        }
    elif method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    elif message_id is None:
        return None
    else:
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": f"Okänd metod: {method}"}}
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def main():
    for line in sys.stdin:
        if line.strip():
            response = handle(json.loads(line))
            if response is not None:
                print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
