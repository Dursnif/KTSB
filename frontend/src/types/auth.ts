export type Role =
  | "child"
  | "teen"
  | "young_adult"
  | "adult"
  | "admin"
  | "household"   // system identity global_user (P120)
  | "stranger";   // system identity unknown_user (P120)

export type AuthUser = {
  username: string;
  role: Role;
};

export type AuthState = {
  user: AuthUser | null;
};
