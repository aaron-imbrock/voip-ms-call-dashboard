# VoIP.ms Dashboard

Single-page dashboard showing account balance, DIDs, and 60-day CDR. Stdlib only, no dependencies.

## Deploy (Coolify)

Set these environment variables in the Coolify UI:

| Variable | Description |
|---|---|
| `VOIPMS_USER` | VoIP.ms account email |
| `VOIPMS_PASS` | VoIP.ms API password (not portal password) |
| `DASHBOARD_AUTH` | Traefik basicAuth hash — generate with `htpasswd -nb user password` |

Deploy as a **Docker Compose** application. Traefik handles SSL and basic auth automatically.

## Local testing

```bash
cp .env.example .env  # fill in real values
export $(grep -v '^#' .env | xargs) && python3 dashboard.py
```

Open `http://localhost:8000`.
