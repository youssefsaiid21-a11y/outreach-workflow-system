#!/bin/bash
# Health check — restarts outreach service if the server is unreachable.
# Any HTTP response (even 401/403) means the server is alive and healthy.
# Only restart when the server fails to respond at all (timeout/connection refused).
STATUS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:3004/api/health/ 2>/dev/null)
if [ -z "$STATUS" ] || [ "$STATUS" = "000" ]; then
    echo "$(date): Health check FAILED (status=$STATUS) — restarting" >> /var/log/outreach/healthcheck.log
    systemctl restart outreach
else
    echo "$(date): OK ($STATUS)" >> /var/log/outreach/healthcheck.log
fi
