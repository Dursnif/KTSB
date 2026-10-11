// Network suggestions from the address the browser opened KTSB on (P127).

/** The address the browser reached KTSB on — on a LAN install that is the host's LAN address (P127). */
export function suggestedMediaHost(): string {
  const h = window.location.hostname;
  return h === "localhost" || h.startsWith("127.") || h === "::1" || h === "[::1]" ? "" : h;
}

/** The /24 around the address the browser opened KTSB on, when that can be a home LAN (P127).
 *  Never the Docker bridge (172.16.0.0/12) or the VPN — mirrors suggest_local_subnet() in the API. */
export function subnetFromHostname(vpnSubnet: string): string {
  const m = window.location.hostname.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return "";
  const [a, b, c] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const isPrivate = a === 10 || (a === 192 && b === 168);  // 172.16/12 left out on purpose (Docker)
  if (!isPrivate) return "";
  const subnet = `${a}.${b}.${c}.0/24`;
  return vpnSubnet.startsWith(`${a}.${b}.${c}.`) ? "" : subnet;
}
