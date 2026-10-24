import type {
  ConnectPublicKeyJwk,
  LoginMethod,
  LoginSession,
  LoginSessionEvidence,
  LoginSessionIdentity,
  LoginSessionState,
} from "./types";

export type {
  ConnectPublicKeyJwk,
  LoginMethod,
  LoginSession,
  LoginSessionEvidence,
  LoginSessionIdentity,
  LoginSessionState,
};

// ---------------------------------------------------------------------------
// Card View Model
// ---------------------------------------------------------------------------

export interface LoginCardViewModel {
  sessionId: string;
  connectionId: string;
  providerId: string;
  state: LoginSessionState;
  isTerminal: boolean;
  canCancel: boolean;
  title: string;
  description?: string;
  statusChipStatus: "ready" | "running" | "waiting-for-you" | "failed" | "needs-login";
  statusChipLabel: string;
  userCode?: string | null;
  authUrl?: string | null;
  verificationUri?: string | null;
  needsInput: boolean;
  inputHint?: string | null;
  expiresAt?: string | null;
  identity?: LoginSessionIdentity;
  evidence?: LoginSessionEvidence;
  errorExcerpt?: string | null;
  checklist?: string[];
  actions: Array<
    | "copy_code"
    | "open_auth_url"
    | "open_verification_uri"
    | "submit_input"
    | "cancel"
    | "start_again"
    | "remove_slot"
    | "check_again"
  >;
}

/**
 * Maps a login session row to the card view model for its current state.
 */
export function getLoginCardViewModel(session: LoginSession): LoginCardViewModel {
  const { id: sessionId, connection_id: connectionId, provider_id: providerId, state } = session;

  switch (state) {
    case "queued":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: false,
        canCancel: true,
        title: "Getting the sign-in ready…",
        statusChipStatus: "running",
        statusChipLabel: "Queued",
        needsInput: false,
        actions: ["cancel"],
      };

    case "starting":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: false,
        canCancel: true,
        title: "Getting the sign-in ready…",
        statusChipStatus: "running",
        statusChipLabel: "Starting",
        needsInput: false,
        actions: ["cancel"],
      };

    case "waiting_for_user":
      if (session.user_code) {
        return {
          sessionId,
          connectionId,
          providerId,
          state,
          isTerminal: false,
          canCancel: true,
          title: "Sign in with device code",
          description: "Use a private window if this browser is signed in to another account",
          statusChipStatus: "waiting-for-you",
          statusChipLabel: "Waiting for you",
          userCode: session.user_code,
          authUrl: session.auth_url ?? session.verification_uri,
          verificationUri: session.verification_uri ?? session.auth_url,
          needsInput: false,
          expiresAt: session.expires_at,
          actions: ["copy_code", "open_verification_uri", "cancel"],
        };
      }
      if (session.needs_input) {
        return {
          sessionId,
          connectionId,
          providerId,
          state,
          isTerminal: false,
          canCancel: true,
          title: "Sign in with provider",
          description: "Sign in at the provider, then paste the code back here",
          statusChipStatus: "waiting-for-you",
          statusChipLabel: "Waiting for you",
          authUrl: session.auth_url,
          needsInput: true,
          inputHint: session.input_hint ?? "Paste the code the page shows you",
          expiresAt: session.expires_at,
          actions: ["open_auth_url", "submit_input", "cancel"],
        };
      }
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: false,
        canCancel: true,
        title: "Waiting for approval",
        statusChipStatus: "waiting-for-you",
        statusChipLabel: "Waiting for you",
        authUrl: session.auth_url,
        verificationUri: session.verification_uri,
        needsInput: false,
        expiresAt: session.expires_at,
        actions: ["cancel"],
      };

    case "verifying":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: false,
        canCancel: true,
        title: "Verifying account",
        statusChipStatus: "running",
        statusChipLabel: "Verifying",
        checklist: ["Signed in", "Checking who it is", "Test reply"],
        needsInput: false,
        actions: ["cancel"],
      };

    case "done":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: true,
        canCancel: false,
        title: "Ready",
        statusChipStatus: "ready",
        statusChipLabel: "Ready",
        identity: session.identity,
        evidence: session.evidence,
        needsInput: false,
        actions: [],
      };

    case "duplicate":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: true,
        canCancel: false,
        title: "Duplicate account",
        description: "This account is already connected to another slot.",
        statusChipStatus: "failed",
        statusChipLabel: "Duplicate",
        errorExcerpt: session.error_excerpt ?? "Duplicate account detected.",
        needsInput: false,
        actions: ["start_again", "remove_slot"],
      };

    case "needs_verification":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: true,
        canCancel: false,
        title: "Verification required",
        description: "The provider asks you to verify this account",
        statusChipStatus: "waiting-for-you",
        statusChipLabel: "Verify",
        authUrl: session.auth_url,
        errorExcerpt: session.error_excerpt,
        needsInput: false,
        actions: ["open_auth_url", "check_again"],
      };

    case "failed":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: true,
        canCancel: false,
        title: "Sign-in failed",
        statusChipStatus: "failed",
        statusChipLabel: "Failed",
        errorExcerpt: session.error_excerpt ?? "Sign-in failed.",
        needsInput: false,
        actions: ["start_again"],
      };

    case "expired":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: true,
        canCancel: false,
        title: "Sign-in expired",
        statusChipStatus: "failed",
        statusChipLabel: "Expired",
        errorExcerpt: session.error_excerpt ?? "The sign-in window expired.",
        needsInput: false,
        actions: ["start_again"],
      };

    case "cancelled":
      return {
        sessionId,
        connectionId,
        providerId,
        state,
        isTerminal: true,
        canCancel: false,
        title: "Sign-in cancelled",
        statusChipStatus: "failed",
        statusChipLabel: "Cancelled",
        errorExcerpt: session.error_excerpt ?? "Sign-in was cancelled.",
        needsInput: false,
        actions: ["start_again"],
      };
  }
}

// ---------------------------------------------------------------------------
// Env Name Generation
// ---------------------------------------------------------------------------

/**
 * Generates the next free environment variable name for an API key:
 * `<PROVIDER>_KEY_<NN>`, upper-case, next free 2-digit number (e.g. REOON_KEY_01 after REOON_KEY_00).
 */
export function generateEnvName(providerId: string, existingNames: Iterable<string>): string {
  const cleanPrefix = providerId.replace(/[^a-zA-Z0-9]/g, "_").toUpperCase();
  const pattern = new RegExp(`^${cleanPrefix}_KEY_(\\d+)$`);
  const used = new Set<number>();

  for (const name of existingNames) {
    const raw = name.startsWith("env:") ? name.slice(4) : name;
    const match = pattern.exec(raw);
    if (match) {
      used.add(parseInt(match[1], 10));
    }
  }

  let n = 0;
  while (used.has(n)) {
    n++;
  }

  return `${cleanPrefix}_KEY_${String(n).padStart(2, "0")}`;
}
