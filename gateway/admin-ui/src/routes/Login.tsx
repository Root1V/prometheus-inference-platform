import { useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Clock, Eye, EyeOff } from "lucide-react";
import { describeLoginError } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import { cn } from "../lib/cn";

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text placeholder:text-text-muted/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20";

/** The flame from the favicon, so the sign-in screen carries the same mark. */
function Flame({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden>
      <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
    </svg>
  );
}

/**
 * PRM-249: say why they are here.
 *
 * A session that ends and a session the operator ended look identical on this
 * screen, and the page used to say nothing about either — so an expiry read as
 * the dashboard breaking. The expiry case also names where it will put them
 * back, which is the part that makes losing the session cost less.
 */
function SessionNotice({
  reason,
  returnTo,
}: {
  reason: "expired" | "signed-out";
  returnTo: string | null;
}) {
  if (reason === "signed-out") {
    return (
      <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text-muted">
        <ArrowRight size={15} className="mt-0.5 shrink-0" aria-hidden />
        <span>You are signed out. Sign in again to continue.</span>
      </div>
    );
  }
  return (
    <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-200">
      <Clock size={15} className="mt-0.5 shrink-0" aria-hidden />
      <span>
        Your session expired.
        {returnTo ? (
          <>
            {" "}
            Sign in and you will land back on{" "}
            <code className="font-mono text-xs">{returnTo}</code>.
          </>
        ) : (
          " Sign in to continue."
        )}
      </span>
    </div>
  );
}

export default function Login() {
  const { loginWithPassword, sessionEnded } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    // Read before signing in — a successful login clears `sessionEnded`, and
    // with it the route that was worth coming back to.
    const destination = sessionEnded?.returnTo ?? "/";
    try {
      await loginWithPassword(email, password);
      navigate(destination, { replace: true });
    } catch (err) {
      setError(describeLoginError(err));
    } finally {
      setSubmitting(false);
    }
  };

  // Caps Lock is the single most common reason a correct password is refused,
  // and the field hides the evidence by design.
  const trackCapsLock = (event: KeyboardEvent<HTMLInputElement>) =>
    setCapsLock(event.getModifierState("CapsLock"));

  return (
    <div className="flex min-h-screen bg-background">
      {/*
        The brand half, on wide screens only. Below `lg` the form takes the
        whole width — a sign-in screen on a laptop at 1280 has room for both,
        a tablet does not and the form is the part that matters.
      */}
      <aside className="relative hidden w-[58%] flex-col justify-between overflow-hidden border-r border-border bg-surface p-12 lg:flex xl:p-16">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-96 w-96 rounded-full"
          style={{
            background:
              "radial-gradient(closest-side, color-mix(in oklab, var(--color-primary) 22%, transparent), transparent)",
          }}
        />
        <div className="relative flex items-center gap-2.5">
          <Flame className="h-7 w-7 text-primary" />
          <span className="text-lg font-semibold tracking-tight text-text">
            Prometheus
          </span>
        </div>

        <div className="relative">
          <h2 className="max-w-2xl text-balance text-3xl font-semibold leading-tight tracking-tight text-text xl:text-4xl">
            The control surface for your own inference platform.
          </h2>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-text-muted xl:text-base">
            Models, instances, rate limits and what every request costs — on
            your hardware, behind your own gateway.
          </p>
        </div>

        <p className="relative text-xs text-text-muted">
          Self-hosted · your models, your machines
        </p>
      </aside>

      <main className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Flame className="h-6 w-6 text-primary" />
            <span className="text-base font-semibold tracking-tight text-text">
              Prometheus
            </span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-text">
            Sign in
          </h1>
          <p className="mt-1.5 text-sm text-text-muted">
            Use the email an administrator issued you.
          </p>

          <div className="mt-7">
            {sessionEnded && (
              <SessionNotice
                reason={sessionEnded.reason}
                returnTo={sessionEnded.returnTo}
              />
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label
                  className="mb-1.5 block text-xs font-medium text-text-muted"
                  htmlFor="email"
                >
                  Email
                </label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  // PRM-249: the form carried no autocomplete tokens at all, so
                  // a password manager had to guess at it and mostly declined.
                  autoComplete="username"
                  autoFocus
                  required
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputClass}
                />
              </div>

              <div>
                <label
                  className="mb-1.5 block text-xs font-medium text-text-muted"
                  htmlFor="password"
                >
                  Password
                </label>
                <div className="relative">
                  <input
                    id="password"
                    name="password"
                    type={revealed ? "text" : "password"}
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyUp={trackCapsLock}
                    onKeyDown={trackCapsLock}
                    className={cn(inputClass, "pr-10")}
                  />
                  <button
                    type="button"
                    onClick={() => setRevealed((v) => !v)}
                    aria-label={revealed ? "Hide password" : "Show password"}
                    className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-2 text-text-muted transition-colors hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                  >
                    {revealed ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                {capsLock && (
                  <p className="mt-1.5 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                    <AlertTriangle size={12} aria-hidden />
                    Caps Lock is on.
                  </p>
                )}
              </div>

              {error && (
                <p
                  role="alert"
                  className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300"
                >
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-50"
              >
                {submitting ? "Signing in…" : "Sign in"}
              </button>
            </form>

            {/*
              Where the client-id tab went. It authenticated a software
              identity against the admin dashboard, which is not what a client
              id is for — integrations take their token from the auth-service
              directly. Saying so is cheaper than letting an operator hunt for
              a control that was removed on purpose.
            */}
            <p className="mt-6 border-t border-border pt-5 text-xs leading-relaxed text-text-muted">
              Signing in with a client id and secret? Those belong to a software
              integration, not to this dashboard — they exchange for a token at
              the auth-service's token endpoint.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
