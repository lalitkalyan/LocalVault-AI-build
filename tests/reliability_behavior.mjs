import {
  DEFAULT_LIMITS, classifyTurn, turnNote, planRetry, applyRetry,
  sameGeneration, canSwitchChat, finalAnswer, includeInModelContext,
} from "../reliability.js";

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL", msg);
    process.exitCode = 1;
  }
}

const user = { role: "user", content: "Why is the sky blue?", images: ["abc"], files: [{ name: "n.txt", text: "note" }] };
const emptyThink = {
  role: "assistant", content: "", thinking: "I am weighing a few explanations.",
  requestThink: true, model: "qwen3.5:4b",
};
assert(classifyTurn(emptyThink) === "empty_final", "thinking with no final text is an empty answer");
assert(finalAnswer("<think>hidden</think>", "") === "", "inline think tags are not the answer");
assert(finalAnswer("<think>hidden</think>The sky is blue.", "") === "The sky is blue.", "text after a think tag is the answer");

const note = turnNote(emptyThink);
assert(note.action === "retry-noreason", "reasoning reply offers retry without reasoning");
assert(note.text.includes("not sent again"), "the note says the message was not sent again");

const retried = applyRetry([user, emptyThink], "without_reasoning");
assert(retried.ok && retried.think === false, "click sets think false");
assert(retried.userCount === 1, "retry does not copy the user message");
assert(retried.userContent === user.content, "the original prompt is the one that is kept");
assert(retried.messages[0].images.length === 1 && retried.messages[0].files.length === 1, "attachments stay on the original message");
assert(retried.messages[1].omitFromModel === true, "the empty answer is left visible but omitted from the next model call");
assert(includeInModelContext(retried.messages[1]) === false, "omitted empty answer is not sent back to the model");
assert(retried.messages.filter((m) => m.role === "user").length === 1, "still one user turn");

const same = applyRetry([user, { ...emptyThink, requestThink: true }], "same");
assert(same.think === true, "a plain retry keeps reasoning on");

const plainEmpty = { role: "assistant", content: "  ", thinking: "", requestThink: false };
assert(turnNote(plainEmpty).action === "retry-same", "an empty reply that did not use reasoning retries the same way");

const stopped = { role: "assistant", content: "partial", thinking: "x", stopped: true, errorKind: "cancelled" };
assert(classifyTurn(stopped) === "cancelled", "stop is not an empty-answer failure");
assert(turnNote(stopped).action === null, "stop does not start a retry by itself");
assert(turnNote(stopped).partial === true, "stopped partial text stays labelled as kept");
assert(turnNote({ role: "assistant", content: "", thinking: "", stopped: true }).text.includes("before the model wrote"), "stop before any token is visible");

const timed = { role: "assistant", content: "part", thinking: "t", errorKind: "timeout", phase: "idle", limit_s: 90 };
assert(classifyTurn(timed) === "timeout", "timeout is its own outcome");
assert(turnNote(timed).action === "retry-same", "timeout retry uses the original reasoning setting");
assert(classifyTurn({ errorKind: "timeout", stopped: true, content: "" }) === "cancelled", "a user stop wins over a timeout flag");

assert(sameGeneration(4, 4) === true && sameGeneration(4, 5) === false, "late events from an older reply are ignored");
assert(canSwitchChat(false) === true && canSwitchChat(true) === false, "a chat switch waits until the active reply ends");
assert(DEFAULT_LIMITS.first_token_s === 180 && DEFAULT_LIMITS.idle_s === 90 && DEFAULT_LIMITS.total_s === 600, "default limits");

if (process.exitCode) process.exit(process.exitCode);
console.log("reliability behavior ok");
