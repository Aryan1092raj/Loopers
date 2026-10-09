import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Heuristic intent classification (no LLM tokens for clear cases)
# ---------------------------------------------------------------------------

_PPT_RE = re.compile(
    r"\b(slides?|presentation|ppt|teach|lecture|explain .* to class|create .* for class)\b",
    re.IGNORECASE,
)
_NOTES_RE = re.compile(
    r"\b(notes?|study material|revision|summary for exam|prepare|summarize)\b",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(
    r"\b(assignment|test|quiz|exam|questions?|worksheet|practice problems?|solve|math problem|numerical|derivation)\b",
    re.IGNORECASE,
)
_IMPERATIVE_RE = re.compile(
    r"\b(make|create|generate|build|write|give me|prepare)\b",
    re.IGNORECASE,
)

_SAME_TOPIC_RE = re.compile(r"\b(same topic|same|previous topic|that topic)\b", re.IGNORECASE)

_ASSIGNMENT_TYPE_MAP = [
    (re.compile(r"\b(mcq|multiple.?choice|objective)\b", re.IGNORECASE), "mcq"),
    (re.compile(r"\b(short|brief)\b", re.IGNORECASE), "short"),
    (re.compile(r"\b(long|descriptive|essay)\b", re.IGNORECASE), "long"),
    (re.compile(r"\b(math|solve|calculation|numerical|derivation)\b", re.IGNORECASE), "math"),
]

_COUNT_RE = re.compile(
    r"\b(?:of\s+)?(\d{1,3})\s*(?:(?:mcq|multiple.?choice|short|long|math|mixed)\s+)?"
    r"(?:pages?|slides?|ppt\s*slides?|questions?|qs?)\b",
    re.IGNORECASE,
)
_ARTIFACT_WORDS_RE = re.compile(
    r"\b(slides?|presentation|ppt|pages?|deck|teacher|student|notes?|study material|"
    r"assignment|test|quiz|exam|questions?|worksheet|practice problems?)\b",
    re.IGNORECASE,
)
_FILLER_RE = re.compile(
    r"\b(make|create|generate|build|write|give|prepare|me|us|a|an|the|some|please|"
    r"on|about|for|of|regarding|with|in)\b",
    re.IGNORECASE,
)


def classify_intent(message: str) -> str:
    """Deterministic heuristic: returns 'ppt', 'notes', 'assignment', or 'chat'.

    LLM fallback not done here — caller decides when to escalate.
    """
    if _PPT_RE.search(message):
        return "ppt"
    if _NOTES_RE.search(message):
        return "notes"
    if _ASSIGNMENT_RE.search(message):
        return "assignment"
    return "chat"


def extract_count(message: str, intent: str) -> int | None:
    """Extract requested slide/page/question count from a teacher request."""
    match = _COUNT_RE.search(message)
    if not match:
        return None
    value = int(match.group(1))
    if intent == "ppt":
        return min(max(value, 2), 40)
    if intent == "assignment":
        return min(max(value, 1), 100)
    return None


def _clean_topic(raw: str) -> str:
    raw = _COUNT_RE.sub(" ", raw)
    raw = _ARTIFACT_WORDS_RE.sub(" ", raw)
    raw = _FILLER_RE.sub(" ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip(" .,:;?!-")[:100]


def extract_topic(message: str) -> str:
    """Extract topic: quoted text first, then last noun phrase after keyword verbs."""
    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', message)
    if quoted:
        return _clean_topic(quoted.group(1) or quoted.group(2))

    # Prefer topic markers that naturally introduce the subject. This avoids
    # treating "of 10 pages" as the topic in requests like "slides of 10 pages on X".
    for marker in ("on", "about", "regarding", "for", "of"):
        matches = list(re.finditer(rf"\b{marker}\s+([A-Za-z0-9][^\.,;?!]{{1,100}})", message, re.IGNORECASE))
        for m in reversed(matches):
            topic = _clean_topic(m.group(1))
            if topic:
                return topic

    # Fallback: remove common verbs, return remaining noun phrase
    return _clean_topic(message)


def extract_assignment_type(message: str) -> str:
    """Returns 'mcq', 'short', 'long', 'math', or 'mixed'."""
    for pattern, atype in _ASSIGNMENT_TYPE_MAP:
        if pattern.search(message):
            return atype
    return "mixed"

_SYSTEM_PROMPT = (
    "You are a helpful teacher assistant for a multimodal study-material RAG system. "
    "When the teacher wants to CREATE content (notes or an assignment), "
    "respond with ONLY a JSON block and nothing else:\n"
    '{"action":"notes"|"assignment","topic":"...","subject":"..."}\n'
    "Extract a clean academic topic only; do not include words like slides, notes, pages, or assignment. "
    "For all other messages, answer conversationally."
)

_history: list[dict] = []  # {"role": "user"|"assistant", "content": "..."}
_MAX_HISTORY = 10  # turns (each turn = 2 messages)
_last_topic: str = ""
_last_subject: str = ""

# Retrieval confidence thresholds
RERANK_REFUSE = 0.05
RERANK_LOWCONF = 0.20


# ---------------------------------------------------------------------------
# LLM selection — DeepSeek V4 Pro → Anthropic → OpenAI → AgentRouter → Gemini
# Uses the same fallback chain as generate.py::call_llm_with_fallback()
# ---------------------------------------------------------------------------
def _call_llm_chat(messages: list) -> str:
    """Call LLM with fallback chain: DeepSeek V4 Pro → Anthropic → OpenAI → AgentRouter → Gemini.
    Matches generate.py::call_llm_with_fallback() but for chat (no role/key_index)."""
    from llm import call_deepseek, call_anthropic, call_openai, call_agentrouter, call_gemini, GeminiRateLimitError
    import config

    # 1. DeepSeek V4 Pro (primary — replaces AgentRouter as primary)
    if config.DEEPSEEK_API_KEY:
        try:
            print(f"[chat] DeepSeek V4 Pro ({config.DEEPSEEK_MODEL}) for intent parsing...")
            return call_deepseek(messages, max_tokens=2048)
        except Exception as e:
            print(f"[chat] DeepSeek failed ({e}). Trying Anthropic...")

    # 2. Anthropic direct (if key present)    if config.ANTHROPIC_API_KEY:
        try:
            print("[chat] Anthropic for intent parsing...")
            return call_anthropic(messages, max_tokens=2048, role="chat")
        except Exception as e:
            print(f"[chat] Anthropic failed ({e}). Trying OpenAI...")

    # 3. OpenAI direct (if key present)
    if config.OPENAI_API_KEY:
        try:
            print(f"[chat] OpenAI for intent parsing...")
            return call_openai(messages, max_tokens=2048)
        except Exception as e:
            print(f"[chat] OpenAI failed ({e}). Trying AgentRouter...")

    # 4. AgentRouter / gpt-5.5 proxy (legacy fallback)
    if config.AGENTROUTER_API_KEY:
        try:
            model_name = config.AGENTROUTER_MODEL or "gpt-5.5"
            print(f"[chat] AgentRouter ({model_name}) for intent parsing...")
            return call_agentrouter(messages, max_tokens=2048)
        except Exception as e:
            print(f"[chat] AgentRouter failed ({e}). Trying Gemini...")

    # 5. Gemini fallback (try multiple models)
    for model in ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"]:
        try:
            print(f"[chat] Gemini fallback ({model}) for intent parsing...")
            return call_gemini(messages, model=model, max_tokens=2048)
        except GeminiRateLimitError:
            break
        except Exception as e:
            print(f"[chat] Gemini {model} failed: {e}")

    raise RuntimeError("All LLM providers exhausted for chat intent parsing.")


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------
_JSON_RE = re.compile(r"\{[^{}]+\}", re.DOTALL)


def parse_action(response_text: str) -> dict | None:
    """Extract {"action":..., "topic":..., "subject":...} from response if present."""
    for m in _JSON_RE.finditer(response_text):
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "action" in obj and "topic" in obj:
                obj["topic"] = _clean_topic(str(obj.get("topic", ""))) or str(obj.get("topic", "")).strip()
                action = obj.get("action")
                if action == "ppt" and obj.get("slide_count") is not None:
                    try:
                        obj["slide_count"] = min(max(int(obj["slide_count"]), 2), 40)
                    except Exception:
                        obj.pop("slide_count", None)
                if action == "assignment" and obj.get("question_count") is not None:
                    try:
                        obj["question_count"] = min(max(int(obj["question_count"]), 1), 100)
                    except Exception:
                        obj.pop("question_count", None)
                return obj
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Subject routing (unchanged)
# ---------------------------------------------------------------------------
def route_query(topic: str) -> dict | None:
    """Detect the subject for a topic by retrieving top chunks from Qdrant and finding the most common subject."""
    try:
        import store
        from embeddings import encode_hybrid
        from collections import Counter

        dense, sparse = encode_hybrid([topic], is_query=True)
        c = store.client()
        # Retrieve top 5 chunks without filtering by subject
        results = store.query(
            c, dense[0], k=5,
            sparse_vector=sparse[0] if sparse else None
        )
        if not results:
            return None

        # Count frequencies of subjects in retrieved chunks
        subjects = [r.get("subject") for r in results if r.get("subject")]
        if not subjects:
            return None

        best_subject = Counter(subjects).most_common(1)[0][0]
        # Return dict matching old format so caller behaves the same
        return {
            "name": best_subject.replace("_", " ").title(),
            "description": f"Retrieved subject for topic '{topic}'",
            "id": best_subject
        }
    except Exception as e:
        logger.warning(f"Failed to route query: {e}")
        return None


# ---------------------------------------------------------------------------
# Pipeline runner (action → files)
# ---------------------------------------------------------------------------
def _run_pipeline(action: dict) -> dict:
    topic = action.get("topic", "")
    subject_hint = action.get("subject") or None
    cluster = route_query(topic)
    subject = cluster["name"].lower().replace(" ", "_") if cluster else subject_hint
    logger.info("detected subject: %s", subject or "unknown")

    t = action["action"]
    if t in ("ppt", "slides"):
        logger.info("[chat] Redirecting ppt/slides request to notes")
        from notes import run as notes_run
        pdf = notes_run(topic, subject=subject)
        files = [str(pdf)]
        t = "notes"
    elif t == "notes":
        from notes import run as notes_run
        pdf = notes_run(topic, subject=subject)
        files = [str(pdf)]
    elif t == "assignment":
        from assignment import run as assign_run
        student, teacher = assign_run(
            topic,
            subject=subject,
            assignment_type=action.get("assignment_type"),
            question_count=action.get("question_count"),
        )
        files = [str(student), str(teacher)]
    else:
        from notes import run as notes_run
        pdf = notes_run(topic, subject=subject)
        files = [str(pdf)]
        t = "notes"

    return {
        "status": "ok",
        "type": t,
        "topic": topic,
        "subject_detected": cluster["name"] if cluster else "unknown",
        "files": files,
    }


# ---------------------------------------------------------------------------
# Build message list from history + new message
# ---------------------------------------------------------------------------
def _build_messages(message: str) -> list:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_core.messages.base import BaseMessage
    msgs: list[BaseMessage] = [SystemMessage(content=_SYSTEM_PROMPT)]
    for h in _history[-(_MAX_HISTORY * 2):]:
        if h["role"] == "user":
            msgs.append(HumanMessage(content=h["content"]))
        else:
            msgs.append(AIMessage(content=h["content"]))
    msgs.append(HumanMessage(content=message))
    return msgs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _check_retrieval_confidence(topic: str) -> float:
    """Quick top-1 retrieval score for a topic. Returns 0.0 on any error."""
    try:
        import store
        from embeddings import encode_hybrid
        from config import COLLECTION
        dense, sparse = encode_hybrid([topic], is_query=True)
        c = store.client()
        results = store.query(c, dense[0], k=1, sparse_vector=sparse[0] if sparse else None)
        return float(results[0].get("score", 0.0)) if results else 0.0
    except Exception:
        return 1.0  # assume ok if check fails — don't block on infra issues


def handle_chat(message: str) -> dict:
    global _history, _last_topic, _last_subject
    try:
        # --- Sticky topic: reuse last topic if user says "same topic" etc. ---
        if _SAME_TOPIC_RE.search(message) and _last_topic:
            message = re.sub(_SAME_TOPIC_RE, _last_topic, message, count=1)

        # --- Step 1: Deterministic intent classification (no LLM) ---
        intent = classify_intent(message)

        if intent == "ppt":
            return {
                "status": "ok",
                "type": "chat",
                "message": "Slide/PPTX generation is not supported. Please request student notes or assignments instead (e.g., 'generate notes on photosynthesis').",
                "files": [],
            }

        if intent != "chat" or _IMPERATIVE_RE.search(message):
            topic = extract_topic(message)
            requested_count = extract_count(message, intent)
            assignment_type = extract_assignment_type(message) if intent == "assignment" else None

            if topic and intent != "chat":
                # --- Step 2: Soft retrieval confidence gate ---
                # Don't hard-block — a low score doesn't mean context is useless;
                # the LLM is better placed to decide if the retrieved chunks suffice.
                score = _check_retrieval_confidence(topic)
                low_conf_warning = score < RERANK_LOWCONF
                if score < RERANK_REFUSE:
                    logger.warning(
                        "Very low retrieval score (%.4f) for topic '%s'. Proceeding with caution.",
                        score, topic,
                    )
                    low_conf_warning = True

                # --- Step 3: Dispatch directly without LLM ---
                cluster = route_query(topic)
                subject = cluster["name"].lower().replace(" ", "_") if cluster else None
                _last_topic = topic
                _last_subject = subject or ""
                action = {
                    "action": intent,
                    "topic": topic,
                    "subject": subject,
                }
                if requested_count and intent == "ppt":
                    action["slide_count"] = requested_count
                if requested_count and intent == "assignment":
                    action["question_count"] = requested_count
                if assignment_type:
                    action["assignment_type"] = assignment_type

                _history.append({"role": "user", "content": message})
                _history.append({"role": "assistant", "content": f"[heuristic dispatch: {intent} on {topic}]"})

                result = _run_pipeline(action)
                if low_conf_warning:
                    result["warning"] = "Limited content found — results may be thin."
                return result

        # --- Fallback: LLM for ambiguous messages ---
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        lc_msgs = _build_messages(message)
        messages = []
        for m in lc_msgs:
            if isinstance(m, SystemMessage):
                messages.append({"role": "system", "content": m.content})
            elif isinstance(m, HumanMessage):
                messages.append({"role": "user", "content": m.content})
            elif isinstance(m, AIMessage):
                messages.append({"role": "assistant", "content": m.content})

        response_text = _call_llm_chat(messages)

        _history.append({"role": "user", "content": message})
        _history.append({"role": "assistant", "content": response_text})

        action = parse_action(response_text)
        if action:
            if action.get("action") == "ppt":
                action["action"] = "notes"
            _last_topic = action.get("topic", "")
            _last_subject = action.get("subject", "")
            return _run_pipeline(action)

        return {
            "status": "ok",
            "type": "chat",
            "message": response_text,
            "files": [],
        }
    except Exception as e:
        logger.exception("Chat handling failed")
        return {"status": "error", "message": str(e)}
