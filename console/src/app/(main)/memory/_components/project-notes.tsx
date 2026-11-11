import Link from "next/link";

import { resolveDataSource } from "@/lib/farm/mode";
import { getTenantId } from "@/lib/farm/seal";
import { createClient } from "@/lib/supabase/server";

export async function ProjectNotes({ project, after = "" }: { project: string; after?: string }) {
  const valid = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$/.test(project) && after.length <= 128;
  let notes: { key: string; body: string; version: number; updated_at: string }[] = [];
  let error: string | null = null;
  if (valid && resolveDataSource() === "supabase") {
    const db = await createClient();
    const result = await db
      .from("project_memory")
      .select("key,body,version,updated_at")
      .eq("tenant_id", getTenantId())
      .eq("project", project)
      .eq("deleted", false)
      .gt("key", after)
      .order("key")
      .limit(21);
    if (result.error) error = "Project notes are unavailable. Check the Farm connection and database migrations.";
    else notes = result.data ?? [];
  }
  const next = notes.length > 20 ? notes[19].key : null;
  return (
    <section className="space-y-4 rounded-xl border p-5" aria-label="Project notes">
      <h2 className="font-semibold">Project notes</h2>
      <p className="text-muted-foreground text-sm">Decisions and context saved by your connected AI operators.</p>
      <form className="flex flex-wrap gap-2" method="get">
        <label className="sr-only" htmlFor="memory-project">
          Project
        </label>
        <input
          id="memory-project"
          name="project"
          defaultValue={project}
          maxLength={63}
          required
          pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}"
          placeholder="Project name"
          className="rounded-md border bg-background px-3 py-2 text-sm"
        />
        <button type="submit" className="rounded-md border px-3 py-2 text-sm">
          View notes
        </button>
      </form>
      {error ? <p role="status">{error}</p> : null}
      {!project ? <p className="text-muted-foreground text-sm">Choose a project to view its notes.</p> : null}
      {project && !valid ? <p role="status">Enter a valid project name.</p> : null}
      {valid && !error && notes.length === 0 ? (
        <p className="text-muted-foreground text-sm">No notes saved for this project.</p>
      ) : null}
      {notes.slice(0, 20).map((note) => (
        <details key={note.key} className="rounded-lg border p-3">
          <summary className="cursor-pointer text-sm">
            {note.key} · version {note.version}
          </summary>
          <pre className="mt-3 whitespace-pre-wrap break-words text-sm">{note.body}</pre>
          <p className="mt-2 text-muted-foreground text-xs">Updated {note.updated_at}</p>
        </details>
      ))}
      {next ? (
        <Link className="text-sm underline" href={`/memory?${new URLSearchParams({ project, after: next })}`}>
          Next notes
        </Link>
      ) : null}
    </section>
  );
}
