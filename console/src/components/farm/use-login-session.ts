"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { submitCommand } from "@/lib/farm/actions";
import {
  type ConnectPublicKeyJwk,
  type LoginMethod,
  type LoginSession,
  getLoginCardViewModel,
} from "@/lib/farm/login";
import { readActiveLoginSession, readConnectPublicKey, readLoginSession } from "@/lib/farm/login-actions";
import { buildAad, seal } from "@/lib/farm/seal";

export interface UseLoginSessionOptions {
  sessionId?: string;
  connectionId?: string;
  onDone?: (session: LoginSession) => void;
  onCancelled?: () => void;
}

const POLL_INTERVAL_MS = 1500;

export function useLoginSession({
  sessionId: initialSessionId,
  connectionId,
  onDone,
  onCancelled,
}: UseLoginSessionOptions) {
  const [sessionId, setSessionId] = useState<string | undefined>(initialSessionId);
  const [session, setSession] = useState<LoginSession | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [publicKey, setPublicKey] = useState<ConnectPublicKeyJwk | null>(null);

  const mounted = useRef<boolean>(true);
  const isMounted = useCallback(() => mounted.current, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (initialSessionId) {
      setSessionId(initialSessionId);
    }
  }, [initialSessionId]);

  // Load public key once on mount
  useEffect(() => {
    let active = true;
    async function loadKey() {
      try {
        const pk = await readConnectPublicKey();
        if (active && pk) {
          setPublicKey(pk);
        }
      } catch (err) {
        if (active) {
          console.error("Failed to read connect public key", err);
        }
      }
    }
    void loadKey();
    return () => {
      active = false;
    };
  }, []);

  // Fetch session
  const fetchSession = useCallback(async () => {
    if (!sessionId && !connectionId) {
      setSession(null);
      setLoading(false);
      return;
    }

    try {
      let data: LoginSession | null = null;
      if (sessionId) {
        data = await readLoginSession(sessionId);
      } else if (connectionId) {
        data = await readActiveLoginSession(connectionId);
        if (data && !sessionId) {
          setSessionId(data.id);
        }
      }

      if (!isMounted()) return;
      setSession(data);
      setError(null);
      setLoading(false);

      if (data?.state === "done") {
        onDone?.(data);
      } else if (data?.state === "cancelled") {
        onCancelled?.();
      }
    } catch (err) {
      if (!isMounted()) return;
      setError(err instanceof Error ? err.message : "Failed to load login session.");
      setLoading(false);
    }
  }, [sessionId, connectionId, isMounted, onDone, onCancelled]);

  // Initial load
  useEffect(() => {
    void fetchSession();
  }, [fetchSession]);

  // Polling every 1.5s while card is open and active (stop when finished_at is set)
  useEffect(() => {
    if (!sessionId) return;
    if (session?.finished_at) return;

    const interval = setInterval(() => {
      void fetchSession();
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [sessionId, session?.finished_at, fetchSession]);

  // Start login mutation
  const startLogin = useCallback(
    async (method?: LoginMethod): Promise<boolean> => {
      if (!connectionId) return false;
      if (!publicKey) {
        toast.error("Start the Farm to sign accounts in");
        return false;
      }
      setLoading(true);
      setError(null);
      try {
        const res = await submitCommand("start_login", {
          connection_id: connectionId,
          ...(method ? { method } : {}),
        });
        if (!res.ok) {
          setError(res.error);
          toast.error(res.error);
          setLoading(false);
          return false;
        }

        // Wait a tick for fixture/command to process
        setTimeout(() => {
          if (!isMounted()) return;
          if (res.data.result && typeof res.data.result.session_id === "string") {
            setSessionId(res.data.result.session_id);
          } else {
            void fetchSession();
          }
        }, 500);

        return true;
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to start sign-in";
        setError(msg);
        toast.error(msg);
        setLoading(false);
        return false;
      }
    },
    [connectionId, publicKey, isMounted, fetchSession],
  );

  // Submit sealed input mutation
  const submitInput = useCallback(
    async (code: string): Promise<boolean> => {
      if (!session) return false;
      if (!publicKey) {
        toast.error("Start the Farm to sign accounts in");
        return false;
      }

      const cleanCode = code.trim();
      if (!cleanCode) {
        toast.error("Enter the code from the provider");
        return false;
      }

      setLoading(true);
      setError(null);

      try {
        const aad = buildAad(session.id, "login_input");
        const sealed = await seal(cleanCode, publicKey, aad);

        const res = await submitCommand("submit_login_input", {
          session_id: session.id,
          sealed,
        });

        if (!res.ok) {
          setError(res.error);
          toast.error(res.error);
          setLoading(false);
          return false;
        }

        // Optimistically set verifying state
        setSession((prev) => (prev ? { ...prev, state: "verifying" } : null));

        // Poll after submit
        setTimeout(() => {
          void fetchSession();
        }, 600);

        return true;
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to submit code";
        setError(msg);
        toast.error(msg);
        setLoading(false);
        return false;
      }
    },
    [session, publicKey, fetchSession],
  );

  // Cancel login mutation
  const cancelLogin = useCallback(async (): Promise<boolean> => {
    if (!session) return false;
    setLoading(true);
    try {
      const res = await submitCommand("cancel_login", {
        session_id: session.id,
      });

      if (!res.ok) {
        setError(res.error);
        toast.error(res.error);
        setLoading(false);
        return false;
      }

      setSession((prev) =>
        prev
          ? {
              ...prev,
              state: "cancelled",
              error_excerpt: "Sign-in cancelled by user",
              finished_at: new Date().toISOString(),
            }
          : null,
      );
      setLoading(false);
      onCancelled?.();
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to cancel sign-in";
      setError(msg);
      toast.error(msg);
      setLoading(false);
      return false;
    }
  }, [session, onCancelled]);

  const viewModel = session ? getLoginCardViewModel(session) : null;

  return {
    sessionId,
    session,
    viewModel,
    loading,
    error,
    publicKey,
    canSignIn: publicKey !== null,
    startLogin,
    submitInput,
    cancelLogin,
    refresh: fetchSession,
  };
}
