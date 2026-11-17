# OSS Field Work public HTTPS boundary

The `sd.oss-ab.net:443` Caddy site treats only TCP peers in `172.17.7.0/24`
as management clients, excluding gateway `172.17.7.1` because public ingress
may be SNATed to that address. This is the OSS MGMT network; Caddy does not use
`X-Forwarded-For` or `X-Real-IP` to grant management access. If ingress is
changed to another reverse proxy or NAT that rewrites the source address,
verify the source address at Caddy before changing this allowlist.

Other clients can open `/field-work` or `/field-work/`, load `/assets/*`, and
call `/api/v1/field-work/miniapp/*`. All other paths return 403, including the
normal SPA fallback and installer administration API. The Mini App API still
requires signed Telegram `initData` and its existing installer permissions.
Port 8443 is bound to the OSS MGMT address `172.17.7.2` only.

Before opening external TCP 443, probe from a client outside the MGMT network:
the Mini App entry must return 200, its unauthenticated API must return 401,
and `/`, `/tickets`, `/users`, `/settings`, `/api/v1/users`, and
`/api/v1/field-work/installers` must return 403. Check HTTPS certificate
validation and repeat from a MGMT client to confirm the OCC remains available.
The Proxmox firewall is managed separately by the infrastructure owner.
