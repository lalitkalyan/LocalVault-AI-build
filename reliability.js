// Decisions for one chat reply. No DOM and no network, so tests can run this directly.
// Defaults match server.generation_limits() when the environment does not override them.
// 180s to the first byte leaves room for a local model to load. 90s of silence after
// that means the reply is stuck, including during thinking. 600s is the hard cap.
export const DEFAULT_LIMITS = { first_token_s: 180, idle_s: 90, total_s: 600 };

export function splitThink(content, thinking) {
  let t = thinking || "";
  let c = content || "";
  const m = c.match(/^\s*<think>([\s\S]*?)(<\/think>|$)/);
  if (m) {
    t = (t ? t + "\n" : "") + m[1];
    c = c.slice(m[0].length);
  }
  return { t: t.trim(), c };
}

export function finalAnswer(content, thinking) {
  return splitThink(content, thinking).c.trim();
}

export function classifyTurn(turn) {
  const row = turn || {};
  if (row.stopped || row.errorKind === "cancelled") return "cancelled";
  if (row.errorKind === "timeout") return "timeout";
  if (row.error && row.errorKind !== "empty_final") return "error";
  if (!finalAnswer(row.content, row.thinking)) return "empty_final";
  return "ok";
}

export function turnNote(turn) {
  const kind = classifyTurn(turn);
  if (kind === "ok") return null;
  const answer = finalAnswer(turn.content, turn.thinking);
  const thought = splitThink(turn.content, turn.thinking).t;
  const partial = !!(answer || thought);
  if (kind === "cancelled") {
    return {
      kind,
      partial,
      action: null,
      text: partial
        ? "Stopped. The partial reply is kept."
        : "Stopped before the model wrote a reply.",
    };
  }
  if (kind === "timeout") {
    const limit = Number(turn.limit_s);
    const phase = turn.phase === "idle" ? "quiet for" : turn.phase === "total" ? "capped at" : "no reply after";
    const shown = Number.isFinite(limit) ? `${phase} ${limit}s` : "the time limit";
    return {
      kind,
      partial,
      action: "retry-same",
      text: `Timed out (${shown}). The partial reply is kept. You can retry.`,
    };
  }
  if (kind === "empty_final") {
    const withoutReasoning = !!turn.requestThink;
    return {
      kind,
      partial: !!thought,
      action: withoutReasoning ? "retry-noreason" : "retry-same",
      text: withoutReasoning
        ? "The model finished thinking but did not write an answer. Your message is still in this chat and was not sent again."
        : "The model finished without an answer. Your message is still in this chat and was not sent again.",
    };
  }
  return { kind: "error", partial, action: "retry-same", text: turn.error || "The reply failed. You can retry." };
}

export function sameGeneration(activeId, eventId) {
  return activeId != null && activeId === eventId;
}

export function canSwitchChat(busy) {
  return !busy;
}

// Retry keeps the same user turn. It does not copy that message.
export function planRetry(messages, mode) {
  const rows = messages || [];
  let assistantIndex = -1;
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].role === "assistant") { assistantIndex = i; break; }
    if (rows[i].role === "user") break;
  }
  if (assistantIndex < 0) return { ok: false };
  let userIndex = -1;
  for (let i = assistantIndex - 1; i >= 0; i--) {
    if (rows[i].role === "user") { userIndex = i; break; }
  }
  if (userIndex < 0) return { ok: false };
  const failed = rows[assistantIndex];
  const think = mode === "without_reasoning" ? false : !!failed.requestThink;
  return {
    ok: true,
    assistantIndex,
    userIndex,
    think,
    sources: failed.sources || [],
    userContent: rows[userIndex].content,
    userCount: rows.filter((m) => m.role === "user").length,
  };
}

export function applyRetry(messages, mode) {
  const plan = planRetry(messages, mode);
  if (!plan.ok) return { ok: false, messages: messages || [] };
  const next = messages.map((m, i) => (i === plan.assistantIndex ? { ...m, omitFromModel: true } : m));
  return {
    ok: true,
    messages: next,
    think: plan.think,
    sources: plan.sources,
    userCount: next.filter((m) => m.role === "user").length,
    userContent: plan.userContent,
    assistantKept: true,
  };
}

export function includeInModelContext(message) {
  if (!message || message.role !== "assistant") return true;
  if (message.omitFromModel) return false;
  if (!finalAnswer(message.content, message.thinking) && message.errorKind === "empty_final") return false;
  return true;
}
