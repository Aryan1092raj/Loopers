import report from "../mocks/quiz_report_q1.json";
import job from "../mocks/job_j1.json";
import mastery from "../mocks/mastery.json";
import schedule from "../mocks/schedule.json";
import chatGrounded from "../mocks/ui_chat_grounded.json";
import chatOutside from "../mocks/ui_chat_outside.json";
import chatRefused from "../mocks/ui_chat_refused.json";
import concept from "../mocks/ui_concept.json";
import diagnostic from "../mocks/ui_diagnostic.json";
import graph from "../mocks/ui_graph.json";
import quizItem from "../mocks/ui_quiz_item.json";
import sources from "../mocks/ui_sources.json";

type Route = [method: string, pattern: RegExp, reply: (body: Record<string, unknown>) => unknown];

let jobPolls = 0;
let diagnosticCalls = 0;

const routes: Route[] = [
  ["GET", /^\/api\/status$/, () => ({ ok: true })],
  ["GET", /^\/api\/sources$/, () => sources],
  ["POST", /^\/api\/sources$/, () => { jobPolls = 0; return { source_id: "a1b2", job_id: "j1" }; }],
  ["GET", /^\/api\/jobs\/[^/]+\/log$/, () => ({ lines: ["[mock] extract started", "[mock] extract failed"] })],
  ["POST", /^\/api\/jobs\/[^/]+\/cancel$/, () => ({ job_id: "j1", status: "cancelling" })],
  ["GET", /^\/api\/jobs\/[^/]+$/, () => {
    jobPolls += 1;
    const done = jobPolls >= 4;
    return { ...job, status: done ? "done" : "running", stage: done ? "done" : job.stage, progress: Math.min(1, jobPolls / 4), units_created: done ? 12 : 0 };
  }],
  ["GET", /^\/api\/graph$/, () => graph],
  ["GET", /^\/api\/concepts\/[^/]+$/, () => concept],
  ["GET", /^\/api\/citations\/[^/]+$/, () => chatGrounded.citations[0]],
  ["POST", /^\/api\/chat$/, (b) => {
    if (!/weather/i.test(String(b.message))) return chatGrounded;
    return b.allow_outside ? chatOutside : chatRefused;
  }],
  ["POST", /^\/api\/students\/onboard$/, () => ({ status: "success", theta_0: -1 })],
  ["GET", /^\/api\/students\/[^/]+\/diagnostic$/, () => diagnostic[diagnosticCalls++] ?? { status: "complete" }],
  ["POST", /^\/api\/students\/[^/]+\/diagnostic\/respond$/, () => ({ status: "success", new_theta: 0.3, correct: true })],
  ["GET", /^\/api\/students\/[^/]+\/mastery$/, () => mastery],
  ["GET", /^\/api\/students\/[^/]+\/schedule$/, () => schedule],
  ["POST", /^\/api\/quiz\/generate$/, () => quizItem],
  ["POST", /^\/api\/quiz\/submit$/, (b) => ({ status: "success", correct: (b.student_answer as string[])[0] === "det(A - λI) = 0" })],
  ["GET", /^\/api\/quiz\/report\/[^/]+$/, () => ({ ...report, raw_metrics: { total_items: 8, correct_items: 5 } })],
  ["POST", /^\/api\/voice\/stt$/, () => ({ text: "what is an eigenvalue" })],
];

export async function mock<T>(method: string, path: string, body?: unknown): Promise<T> {
  const bare = path.split("?")[0];
  const route = routes.find(([m, re]) => m === method && re.test(bare));
  if (!route) throw new Error(`mock: no fixture for ${method} ${bare}`);
  await new Promise((r) => setTimeout(r, 150));
  const b = body instanceof FormData ? {} : ((body ?? {}) as Record<string, unknown>);
  return route[2](b) as T;
}
