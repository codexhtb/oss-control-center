# OSS Control Center and Field Work HTTPS boundary

The normal OCC portal remains at `https://sd.oss-ab.net` on TCP 443. Caddy
serves the complete SPA and `/api/v1/*` there. Caddy does not maintain an IP
allowlist: access to TCP 443 is restricted exclusively by the owner-managed
Proxmox Firewall IPSET `allowed` for VM 102.

The public Telegram Mini App is at
`https://sd.oss-ab.net:8443/field-work`. TCP 8443 is published on all IPv4
interfaces of the VM. Caddy allows only `/field-work`, `/field-work/`,
`/assets/*`, and `/api/v1/field-work/miniapp/*` on that port. Every other route,
including the OCC SPA fallback and administrative APIs, returns 403. Telegram
`initData` authentication remains mandatory for Mini App API calls.

The Proxmox firewall must therefore apply the existing `allowed` IPSET policy
to TCP 443 and permit TCP 8443 from the networks used by installers. There is no
duplicate IP list in Caddy. The repository does not change Proxmox rules, an
upstream firewall, or DNAT.

After a WEB deployment, verify:

- `https://sd.oss-ab.net/`, `/tickets`, `/users`, `/settings`, and the OCC API
  work from an address in `allowed`;
- TCP 443 is rejected by Proxmox from an address outside `allowed`;
- `https://sd.oss-ab.net:8443/field-work` works from an external network;
- the unauthenticated Mini App API returns 401 on 8443;
- `/`, `/tickets`, `/users`, `/settings`, `/api/v1/users`, and other
  administrative routes return 403 on 8443;
- forwarding headers cannot change either boundary.

The Telegram menu button or BotFather Mini App URL must use
`https://sd.oss-ab.net:8443/field-work`. This is a manual Telegram configuration
step and is not changed by the WEB deployment.
