import type { components } from "./schema";

type S = components["schemas"];

// Typed by the live OpenAPI document
export type TutorResponse = S["TutorResponse"];
export type Citation = S["Citation"];
export type Locator = S["Locator"];
export type ChatRequest = S["ChatRequest"];

// Untyped on the server today (no response_model); shapes taken from routes.py and the live console
export type Status = "queued" | "running" | "done" | "failed";

export interface SourceRow {
  source_id: string;
  kind: "video" | "slides" | "textbook";
  title: string;
  file_name: string;
  lecture_no: number | null;
  lang: string;
  status: Status;
  created_at: string;
  unit_count: number;
}

export interface Job {
  job_id: string;
  source_id: string | null;
  stage: string | null;
  progress: number;
  units_created: number;
  error: string | null;
  status: Status;
}

export interface GraphNode {
  id: string;
  name: string;
  level: "topic" | "subtopic" | "concept";
  parent_id: string | null;
  p_know: number;
}
export interface GraphEdge { src: string; dst: string; weight: number }
export interface Graph { nodes: GraphNode[]; edges: GraphEdge[] }

export interface ConceptDetail {
  concept: { id: string; name: string; level: string; parent_id: string | null; aliases: string[]; description: string };
  prerequisites: string[];
  dependents: string[];
  units: Citation[];
}

export interface Mastery {
  concept_id: string;
  p_know: number;
  retrievability: number | null;
  n_obs: number;
  last_review: string | null;
}

export interface ScheduleDay {
  date: string;
  tasks: { concept_id: string; minutes: number; reason: string }[];
}

export interface DiagnosticItem {
  item_id: string;
  concept_id: string;
  difficulty: number;
  stem: string;
  options_json: string;
}
export type DiagnosticReply = DiagnosticItem | { status: "complete" };

// answer_json is deliberately absent: /api/quiz/generate leaks it and the UI must not use it
export interface QuizItem {
  item_id: string;
  concept_id: string;
  topic_id: string;
  type: string;
  bloom: string;
  stem: string;
  options_json: string;
}

export interface QuizReport {
  quiz_id: string;
  score: number;
  mastery_delta: { concept_id: string; before: number; after: number }[];
  weak_topics: { topic_id: string; p_know: number }[];
  misconceptions: { label: string; concept_id: string; citation?: Citation }[];
  next_steps: { concept_id: string; reason: string }[];
  raw_metrics?: { total_items: number; correct_items: number };
}
