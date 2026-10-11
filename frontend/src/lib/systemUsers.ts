// System identities (P120): users that stand for an unidentified speaker, not a person.
// global_user = unrecognised person inside the house (most likely a household member);
// unknown_user = untrusted person (outdoor/door node, or any unidentified caller).
// They are listed with the other users and their tools / learning-influence can be set,
// but they cannot log in and have no PIN, VPN, voice profile, edit or delete.
import type { TFunction } from "i18next";

export const SYSTEM_USERS = new Set(["global_user", "unknown_user"]);

export const isSystemUser = (username: string): boolean => SYSTEM_USERS.has(username);

export function userLabel(u: { username: string; display_name?: string }, t: TFunction): string {
  return isSystemUser(u.username)
    ? t(`users.system.${u.username}.name`)
    : (u.display_name || u.username);
}

// The built-in admin account is a default user too: it exists on every install, starts with
// PIN 1234 that must be changed, and is not a person — only its PIN can be changed (P120).
export const BUILTIN_ADMIN = "admin";
const DEFAULT_ORDER = [BUILTIN_ADMIN, "global_user", "unknown_user"];
const ROLE_ORDER = ["admin", "adult", "young_adult", "teen", "child"];

export const isDefaultUser = (username: string): boolean => DEFAULT_ORDER.includes(username);

/** Users page groups: the default users in fixed order, then the users the admin created
 *  (by role, then name). */
export function groupUsers<T extends { username: string; role: string; display_name?: string }>(
  users: T[],
): { defaults: T[]; created: T[] } {
  const defaults = DEFAULT_ORDER
    .map(name => users.find(u => u.username === name))
    .filter((u): u is T => !!u);
  const rank = (role: string) => { const i = ROLE_ORDER.indexOf(role); return i < 0 ? ROLE_ORDER.length : i; };
  const created = users
    .filter(u => !isDefaultUser(u.username))
    .sort((a, b) => rank(a.role) - rank(b.role)
      || (a.display_name || a.username).localeCompare(b.display_name || b.username, undefined, { sensitivity: "base" }));
  return { defaults, created };
}
