import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileUp, Lock, LogOut, Sparkles, Square } from "lucide-react";
import { AppShell } from "@/components/lab/AppShell";
import { labQuery } from "@/lib/paperlab-api";
import { useBotName } from "@/lib/names";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/analyze")({
  head: () => ({ meta: [{ title: "Cost analyzer — PaperLab" }, { name: "description", content: "AI breakdown of fee and spread losses in a bot's trade history." }] }),
  component: AnalyzePage,
});

const MAX_CHARS = 250_000;
const field = "w-full rounded-lg border bg-surface px-3 py-2 text-sm outline-none focus:border-primary";

// The analyzer is operator-only: it spends PaperLab's OpenRouter credits. The page asks for the same password as the
// operator console and keeps it in this tab's sessionStorage under the console's key, so signing in on either page
// works for both. The OpenRouter key itself never reaches the browser.
const PW_KEY = "paperlab.pw";
const session = {
  get: (): string | null => {
    try {
      return sessionStorage.getItem(PW_KEY);
    } catch {
      return null;
    }
  },
  set: (pw: string) => {
    try {
      sessionStorage.setItem(PW_KEY, pw);
    } catch {
      /* storage blocked: the password lives only in this page's memory */
    }
  },
  clear: () => {
    try {
      sessionStorage.removeItem(PW_KEY);
    } catch {
      /* nothing stored */
    }
  },
};
const basic = (pw: string) => "Basic " + btoa(unescape(encodeURIComponent("admin:" + pw)));

interface Status { configured: boolean; model: string; busy: boolean }

function AnalyzePage() {
  const [pw, setPw] = useState<string | null>(() => session.get());
  const [status, setStatus] = useState<Status | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    if (pw == null) return;
    let gone = false;
    fetch("/api/lab/analyze/status", { headers: { Authorization: basic(pw) }, cache: "no-store" })
      .then(async (r) => {
        if (gone) return;
        if (r.status === 401) {
          session.clear();
          setPw(null);
          setAuthError("Wrong password.");
          return;
        }
        if (r.ok) setStatus(await r.json());
      })
      .catch(() => !gone && setAuthError("Couldn't reach PaperLab."));
    return () => {
      gone = true;
    };
  }, [pw]);

  const signOut = () => {
    session.clear();
    setPw(null);
    setStatus(null);
  };

  return (
    <AppShell>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Cost analyzer</h1>
          <p className="mt-1 text-muted-foreground">Upload a backtest or trade history. AI finds where fees and spread eat the edge and proposes changes you can test.</p>
        </div>
        {pw != null && (
          <button onClick={signOut} className="flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground">
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        )}
      </div>
      {pw == null ? (
        <SignIn
          error={authError}
          onSubmit={(value) => {
            session.set(value);
            setAuthError(null);
            setPw(value);
          }}
        />
      ) : (
        <Analyzer pw={pw} status={status} onUnauthorized={() => { session.clear(); setPw(null); setAuthError("Your session expired. Sign in again."); }} />
      )}
    </AppShell>
  );
}

function SignIn({ error, onSubmit }: { error: string | null; onSubmit: (pw: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="panel max-w-md space-y-4 p-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (value) onSubmit(value);
      }}
    >
      <div className="flex items-center gap-2 font-medium"><Lock className="h-4 w-4 text-primary" /> Operator only</div>
      <p className="text-sm text-muted-foreground">The analyzer runs on PaperLab's own OpenRouter key, so it needs the operator console's password. Everything else on this site is public and read-only.</p>
      <label className="block text-sm">Password
        <input type="password" autoComplete="current-password" className={cn(field, "mt-1")} value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      {error && <p role="alert" className="text-sm text-loss">{error}</p>}
      <button type="submit" disabled={!value} className="w-full rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">Sign in</button>
    </form>
  );
}

function Analyzer({ pw, status, onUnauthorized }: { pw: string; status: Status | null; onUnauthorized: () => void }) {
  const { data: lab } = useQuery(labQuery());
  const display = useBotName();
  const [file, setFile] = useState<{ name: string; content: string; truncated: boolean } | null>(null);
  const [botId, setBotId] = useState("");
  const [notes, setNotes] = useState("");
  const [output, setOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    const text = await f.text();
    // Keep the header and the most recent rows when a file is too large.
    const truncated = text.length > MAX_CHARS;
    const content = truncated ? text.slice(0, 4000) + "\n...[middle rows omitted]...\n" + text.slice(-(MAX_CHARS - 4100)) : text;
    setFile({ name: f.name, content, truncated });
  };

  const run = async () => {
    if (!file) return;
    const bot = lab?.bots.find((b) => b.id === botId);
    const botContext = bot
      ? `name=${bot.name} key=${bot.key} program=${bot.programId} symbol=${bot.symbol} net=${bot.returnPct}% gross=${bot.grossPct}% costs=${bot.feesPct}% winRate=${bot.winRate}% trades=${bot.trades} maxDD=${bot.maxDrawdown}%`
      : undefined;
    setOutput("");
    setError(null);
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const res = await fetch("/api/lab/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: basic(pw), "X-PaperLab": "1" },
        body: JSON.stringify({ fileName: file.name, content: file.content, botContext, notes: notes || undefined }),
        signal: ac.signal,
      });
      if (res.status === 401) return onUnauthorized();
      if (!res.ok || !res.body) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error ?? `Request failed (${res.status})`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let acc = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        acc += dec.decode(value, { stream: true });
        const i = acc.indexOf("[[ERROR]]");
        if (i >= 0) {
          setOutput(acc.slice(0, i).trim());
          setError(acc.slice(i + 9).trim());
        } else setOutput(acc);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <section className="panel space-y-4 p-5 lg:col-span-1">
        <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-surface p-6 text-center text-sm text-muted-foreground hover:border-primary/50">
          <FileUp className="h-6 w-6 text-primary" />
          {file ? <span className="text-foreground">{file.name}</span> : <span>Choose a CSV, JSON or text file</span>}
          {file?.truncated && <span className="text-xs text-warn">Large file: kept the header and most recent rows.</span>}
          <input type="file" accept=".csv,.json,.txt,.tsv,.log" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
        </label>

        <label className="block text-sm">Link to a dashboard bot (optional)
          <select className={cn(field, "mt-1")} value={botId} onChange={(e) => setBotId(e.target.value)}>
            <option value="">None</option>
            {lab?.bots.map((b) => <option key={b.id} value={b.id}>{display(b)} · {b.key}</option>)}
          </select>
        </label>

        <label className="block text-sm">Notes (optional)
          <textarea rows={3} className={cn(field, "mt-1")} placeholder="e.g. taker entries, 0.055% fee, 5m timeframe" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>

        {busy ? (
          <button onClick={() => abortRef.current?.abort()} className="flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm">
            <Square className="h-4 w-4" /> Stop
          </button>
        ) : (
          <button disabled={!file || status?.configured === false} onClick={run} className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
            <Sparkles className="h-4 w-4" /> Analyze
          </button>
        )}
        <p className="text-xs text-muted-foreground">
          {status?.configured === false
            ? "Not available: the server has no OPENROUTER_API_KEY."
            : `Runs on PaperLab's OpenRouter credits${status ? ` (${status.model})` : ""}. Files are not stored. Suggestions are research ideas, not financial advice.`}
        </p>
      </section>

      <section className="panel min-h-[400px] p-6 lg:col-span-2">
        {error && <div role="alert" className="mb-4 rounded-lg bg-loss/10 p-3 text-sm text-loss">{error}</div>}
        {!output && !busy && !error && <p className="text-sm text-muted-foreground">Your analysis will appear here.</p>}
        {busy && !output && <p className="animate-pulse text-sm text-muted-foreground">Reading your trades…</p>}
        {output && (
          <article className="prose-lab">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{output}</ReactMarkdown>
          </article>
        )}
      </section>
    </div>
  );
}
