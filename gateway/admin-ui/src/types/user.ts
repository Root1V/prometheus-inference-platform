export type AuthMethod = "oauth2" | "password";
export type PrincipalRole = "admin" | "cognitive" | "agent" | "app";

export interface Principal {
  client_id: string;
  client_name: string;
  label: string | null;
  role: string;
  allowed_scopes: string[];
  token_ttl_seconds: number;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
  auth_method: AuthMethod;
  email: string | null;
}

export interface CreatePrincipalRequest {
  client_name: string;
  role: PrincipalRole;
  allowed_scopes: string[];
  label?: string;
  auth_method: AuthMethod;
  email?: string;
  password?: string;
}

export interface CreatePrincipalResponse {
  client_id: string;
  client_name: string;
  role: string;
  allowed_scopes: string[];
  token_ttl_seconds: number;
  auth_method: AuthMethod;
  email: string | null;
  /** Shown once only: the client_secret for oauth2, or the caller-supplied password echoed back. */
  client_secret: string | null;
}

/** PATCH /admin/api/users/{id} — role/auth_method/email aren't editable in place. */
export interface UpdatePrincipalRequest {
  client_name?: string;
  label?: string | null;
  allowed_scopes?: string[];
  token_ttl_seconds?: number;
}

export interface ReactivateResponse {
  client_id: string;
  is_active: boolean;
}

export interface RotateSecretResponse {
  client_id: string;
  client_secret: string;
}

export interface ResetPasswordResponse {
  client_id: string;
  password: string;
}

export interface ShareLinkResponse {
  share_url: string;
  expires_at: string;
}
