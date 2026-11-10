import { useCallback, useEffect, useRef, useState } from "react";

/** Load data from an async function; reload() fetches again. */
export function useResource(load, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const latest = useRef(0);
  const run = useCallback(async () => {
    const ticket = ++latest.current;
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await load();
      if (ticket === latest.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (ticket === latest.current) setState({ data: null, error, loading: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    run();
  }, [run]);
  return { ...state, reload: run };
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function relativeTime(iso) {
  if (!iso) return "Never";
  const then = new Date(iso);
  const diff = Date.now() - then.getTime();
  if (diff < MINUTE) return "Just now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} min ago`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)} h ago`;
  if (diff < 2 * DAY) return "Yesterday";
  const sameYear = then.getFullYear() === new Date().getFullYear();
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

export const SCOPE_LABELS = { global: "Global", project: "Project", sub: "Sub-detour" };
export const POLICY_LABELS = { ask: "Always ask", notes: "Notes auto-approve", read: "Read only" };
export const KIND_LABELS = { rule: "Rule", decision: "Decision", runbook: "Runbook", prompt: "Prompt", note: "Note", reference: "Reference" };
export const KIND_TAGS = { rule: "blue", decision: "purple", runbook: "teal", prompt: "green", note: "gray", reference: "cool-gray" };
export const TARGET_LABELS = { repo: "Repo", folder: "Folder", file: "File", topic: "Topic", artifact: "Artifact", url: "URL" };
export const TARGET_TAGS = { repo: "blue", folder: "teal", file: "teal", topic: "purple", artifact: "green", url: "cool-gray" };
export const TARGET_HELP = {
  repo: "Matched by the git remote of the folder an agent works in",
  folder: "Matched when an agent works in this folder or below it",
  file: "Matched when an agent works on this file",
  topic: "Matched when the agent's task mentions one of these words",
  artifact: "Matched when an agent works on this artifact",
  url: "Matched when an agent works on this page",
};
export const ACTION_LABELS = { add: "Add", edit: "Edit", retire: "Retire", move: "Move", create_route: "New route" };
export const ACTION_TAGS = { add: "green", edit: "blue", retire: "red", move: "purple", create_route: "teal" };

export function policyText(route) {
  if (route.paused) return "Paused";
  if (route.policy === "read") return "Read only";
  return route.policy === "notes" ? "Propose · notes auto-approve" : "Propose · always ask";
}

/** Line diff (LCS) for the proposal preview. Returns [{t: 'add'|'del'|'ctx', text}]. */
export function lineDiff(before, after) {
  const a = before ? before.split("\n") : [];
  const b = after ? after.split("\n") : [];
  if (a.length * b.length > 250_000) {
    return [...a.map((text) => ({ t: "del", text })), ...b.map((text) => ({ t: "add", text }))];
  }
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ t: "ctx", text: a[i] });
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({ t: "del", text: a[i++] });
    } else {
      out.push({ t: "add", text: b[j++] });
    }
  }
  while (i < a.length) out.push({ t: "del", text: a[i++] });
  while (j < b.length) out.push({ t: "add", text: b[j++] });
  return out;
}

const THEME_KEY = "detour-theme";

export function readThemeChoice() {
  try {
    return localStorage.getItem(THEME_KEY) || "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice) {
  const dark = choice === "dark" || (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const root = document.documentElement;
  root.classList.toggle("cds--g100", dark);
  root.classList.toggle("cds--g10", !dark);
  root.dataset.theme = dark ? "dark" : "light";
}

export function saveThemeChoice(choice) {
  try {
    localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Private windows can refuse storage; the choice still applies for this visit.
  }
  applyTheme(choice);
}
