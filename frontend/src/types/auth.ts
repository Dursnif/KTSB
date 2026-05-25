export type Role =
  | "child"
  | "teen"
  | "young_adult"
  | "adult"
  | "admin";

export type AuthUser = {
  username: string;
  role: Role;
};

export type AuthState = {
  user: AuthUser | null;
};
