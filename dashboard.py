#!/usr/bin/env python3
"""
VoIP.ms dashboard — single-file stdlib-only HTTP server.

Renders one server-side HTML page with:
  - current account balance
  - list of owned DIDs with SMS / MMS / phone / fax status
  - Call Detail Records for the last 60 days

Authentication is enforced by the application (HTTP Basic Auth), not by the
reverse proxy. The dashboard never renders without valid credentials.

Required env:
  VOIPMS_USER    -> api_username (login email)
  VOIPMS_PASS    -> api_password (the API password, NOT the portal login password)
  DASHBOARD_AUTH -> username:password in plaintext (password may contain colons)
"""

import base64
import hmac
import http.server
import json
import html
import os
import socketserver
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

API_URL = "https://voip.ms/api/v1/rest.php"
CDR_DAYS = 60
TIMEZONE = "0"  # UTC; getCDR offset is numeric -12..13
HTTP_TIMEOUT = 30  # seconds per API call


def api_call(method, extra=None):
    """Call one VoIP.ms REST method. Returns parsed dict or an error dict."""
    user = os.environ.get("VOIPMS_USER")
    pw = os.environ.get("VOIPMS_PASS")
    if not user or not pw:
        return {"status": "config_error",
                "message": "VOIPMS_USER / VOIPMS_PASS not set in environment"}

    params = {
        "api_username": user,
        "api_password": pw,
        "method": method,
        "content_type": "json",
    }
    if extra:
        params.update(extra)

    url = API_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # network / timeout / HTTP error
        return {"status": "request_error", "message": str(exc)}

    try:
        return json.loads(raw)
    except ValueError:
        return {"status": "parse_error", "message": raw[:500]}


def e(value):
    """HTML-escape any value as a string."""
    return html.escape("" if value is None else str(value))


def yesno(flag):
    """Render a 1/0/'yes' style flag as a labelled cell."""
    s = str(flag).strip().lower()
    on = s in ("1", "yes", "true", "y")
    label = "Yes" if on else "No"
    cls = "on" if on else "off"
    return '<span class="badge {0}">{1}</span>'.format(cls, label)


def render_balance(data):
    if data.get("status") != "success":
        return '<p class="err">Balance error: {0}</p>'.format(
            e(data.get("message") or data.get("status")))
    bal = data.get("balance") or {}
    current = bal.get("current_balance", "?")
    return '<p class="balance">Current balance: <strong>${0}</strong></p>'.format(e(current))


def render_dids(data):
    if data.get("status") != "success":
        return '<p class="err">DIDs error: {0}</p>'.format(
            e(data.get("message") or data.get("status")))

    dids = data.get("dids") or []
    if not dids:
        return "<p>No DIDs found.</p>"

    rows = []
    for d in dids:
        # "phone" status: a voice DID is always voice-capable; show enabled-ish state.
        # We treat the DID itself as the phone line (always Yes for an owned voice DID).
        # SMS:  sms_available + sms_enabled
        # MMS:  mms_available
        # Fax:  this getDIDsInfo payload has no fax flag; fax numbers come from
        #       getFaxNumbersInfo. For a standard voice DID, fax-over-this-record is No.
        rows.append(
            "<tr>"
            "<td class=\"num\">{did}</td>"
            "<td>{desc}</td>"
            "<td>{phone}</td>"
            "<td>{sms}</td>"
            "<td>{mms}</td>"
            "<td>{fax}</td>"
            "</tr>".format(
                did=e(d.get("did")),
                desc=e(d.get("description")),
                phone=yesno(1),  # owned voice DID
                sms=yesno(1 if (str(d.get("sms_available")) == "1"
                                and str(d.get("sms_enabled")).lower() in ("1", "yes"))
                          else 0),
                mms=yesno(d.get("mms_available")),
                fax=yesno(0),
            )
        )

    return (
        "<table class=\"grid\">"
        "<thead><tr>"
        "<th>DID</th><th>Description</th><th>Phone</th>"
        "<th>SMS</th><th>MMS</th><th>Fax</th>"
        "</tr></thead>"
        "<tbody>{0}</tbody></table>".format("".join(rows))
    )


_ANONYMOUS = {"", "anonymous", "unknown", "restricted", "unavailable", "blocked"}


def call_quality(c):
    disp = str(c.get("disposition", "")).upper().replace(" ", "")
    if disp == "NOANSWER":
        return '<span class="badge off">Missed</span>'
    if disp == "BUSY":
        return '<span class="badge off">Busy</span>'
    if disp == "FAILED":
        return '<span class="badge off">Failed</span>'
    callerid = str(c.get("callerid", "")).strip().lower()
    if callerid in _ANONYMOUS:
        return '<span class="badge warn">Unknown</span>'
    try:
        secs = int(c.get("seconds") or 0)
    except (ValueError, TypeError):
        secs = 0
    if secs < 6:
        return '<span class="badge warn">Spam?</span>'
    return '<span class="badge on">Real</span>'


def render_cdr(data, date_from, date_to):
    header = "<h2>Call Detail Records <span class=\"sub\">({0} \u2192 {1}, UTC)</span></h2>".format(
        e(date_from), e(date_to))

    if data.get("status") == "no_cdr":
        return header + "<p>No call records in this period.</p>"
    if data.get("status") != "success":
        return header + '<p class="err">CDR error: {0}</p>'.format(
            e(data.get("message") or data.get("status")))

    cdr = data.get("cdr") or []
    if not cdr:
        return header + "<p>No call records in this period.</p>"

    rows = []
    for c in cdr:
        rows.append(
            "<tr>"
            "<td class=\"nowrap\">{date}</td>"
            "<td>{callerid}</td>"
            "<td class=\"num\">{dst}</td>"
            "<td>{desc}</td>"
            "<td>{account}</td>"
            "<td>{disp}</td>"
            "<td class=\"nowrap\">{dur}</td>"
            "<td class=\"r\">{total}</td>"
            "<td>{quality}</td>"
            "</tr>".format(
                date=e(c.get("date")),
                callerid=e(c.get("callerid")),
                dst=e(c.get("destination")),
                desc=e(c.get("description")),
                account=e(c.get("account")),
                disp=e(c.get("disposition")),
                dur=e(c.get("duration")),
                total=e(c.get("total")),
                quality=call_quality(c),
            )
        )

    return (
        header +
        "<p class=\"sub\">{0} record(s)</p>".format(len(cdr)) +
        "<table class=\"grid\">"
        "<thead><tr>"
        "<th>Date</th><th>Caller ID</th><th>Destination</th><th>Description</th>"
        "<th>Account</th><th>Disposition</th><th>Duration</th><th>Total</th><th>Quality</th>"
        "</tr></thead>"
        "<tbody>{0}</tbody></table>".format("".join(rows))
    )


PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoIP.ms Dashboard</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.45 system-ui, sans-serif; margin: 2rem; max-width: 1100px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
  h2 {{ font-size: 1.1rem; margin: 2rem 0 .5rem; }}
  .sub {{ color: #888; font-weight: normal; font-size: .85em; }}
  .balance {{ font-size: 1.1rem; }}
  table.grid {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  table.grid th, table.grid td {{
    border: 1px solid #ccc; padding: .35rem .55rem; text-align: left; vertical-align: top;
  }}
  table.grid th {{ background: rgba(127,127,127,.12); }}
  table.grid tr:nth-child(even) td {{ background: rgba(127,127,127,.06); }}
  td.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td.r {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.nowrap {{ white-space: nowrap; }}
  .badge {{ display: inline-block; min-width: 2.2em; text-align: center;
            padding: 0 .4em; border-radius: .4em; font-size: .85em; }}
  .badge.on {{ background: #2e7d32; color: #fff; }}
  .badge.off {{ background: #999; color: #fff; }}
  .badge.warn {{ background: #e65100; color: #fff; }}
  .err {{ color: #c0392b; }}
  footer {{ margin-top: 2rem; color: #999; font-size: .8em; }}
</style>
</head>
<body>
<h1>VoIP.ms Dashboard</h1>
{balance}

<h2>Numbers</h2>
{dids}

{cdr}

<footer>Generated live from the VoIP.ms API.</footer>
</body>
</html>
"""


def main():
    today = date.today()
    date_from = (today - timedelta(days=CDR_DAYS)).isoformat()
    date_to = today.isoformat()

    balance = api_call("getBalance")
    dids = api_call("getDIDsInfo")
    cdr = api_call("getCDR", {
        "date_from": date_from,
        "date_to": date_to,
        "timezone": TIMEZONE,
        "answered": "1",
        "noanswer": "1",
        "busy": "1",
        "failed": "1",
    })

    return PAGE.format(
        balance=render_balance(balance),
        dids=render_dids(dids),
        cdr=render_cdr(cdr, date_from, date_to),
    )


class Handler(http.server.BaseHTTPRequestHandler):
    def check_auth(self) -> bool:
        raw = os.environ.get("DASHBOARD_AUTH", "")
        if not raw or ":" not in raw:
            print("DASHBOARD_AUTH not configured or missing colon", file=sys.stderr)
            msg = b"Authentication not configured"
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return False

        cfg_user, cfg_pass = raw.split(":", 1)

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="voip-dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            if ":" not in decoded:
                raise ValueError("no colon in decoded credentials")
            req_user, req_pass = decoded.split(":", 1)
            ok = (
                hmac.compare_digest(req_user.encode(), cfg_user.encode())
                and hmac.compare_digest(req_pass.encode(), cfg_pass.encode())
            )
        except Exception:
            ok = False

        if not ok:
            print("Auth failure from {0}".format(self.address_string()), file=sys.stderr)
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="voip-dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        return True

    def do_GET(self):
        if not self.check_auth():
            return
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        try:
            body = main()
        except Exception as exc:
            body = "<h1>Dashboard error</h1><p>{0}</p>".format(html.escape(str(exc)))
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()
