"""Unified LLM client interface — DeepSeek V4 Pro only, then Groq 4-key fallback.
"""
import base64
import json
import mimetypes
import os
import time
import random
from pathlib import Path
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Groq keys — 4 dedicated keys (orchestrator, content1, content2, tikz)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

# DeepSeek V4 Pro — primary generation LLM. OpenAI-compatible.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_REASONING = os.getenv("DEEPSEEK_REASONING", "")


def _post_request(url: str, headers: dict, payload: dict, auth_keys: list[str] | None = None) -> str:
    """POST to an OpenAI-compatible API endpoint with 429 retry + error retry."""
    MAX_429_RETRIES = 8
    MAX_ERR_RETRIES = 2

    def _do_post() -> str:
        for attempt in range(MAX_429_RETRIES):
            if auth_keys:
                key = random.choice(auth_keys)
                headers["Authorization"] = f"Bearer {key}"

            with httpx.Client(timeout=15.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.read()

            if response.status_code == 429:
                retry_after = 5.0
                try:
                    meta = response.json().get("error", {}).get("metadata", {})
                    retry_after = float(meta.get("retry_after_seconds", 5.0))
                except Exception:
                    pass
                sleep_dur = (retry_after * (1.5 ** attempt)) + random.uniform(1.0, 4.0)
                sleep_dur = min(sleep_dur, 60.0)
                print(f"[llm] Rate limited (429) on attempt {attempt + 1}. Retrying in {sleep_dur:.2f}s...")
                time.sleep(sleep_dur)
                continue

            if response.status_code in (401, 403):
                if auth_keys and len(auth_keys) > 1:
                    print(f"[llm] Key {key[:10]}... returned {response.status_code}. Removing key from pool and retrying.")
                    auth_keys.remove(key)
                    try:
                        import config
                        if key in config.GROQ_KEYS:
                            config.GROQ_KEYS.remove(key)
                    except Exception:
                        pass
                    continue

            if response.status_code in (400, 401, 403, 404):
                err_msg = f"API call failed with permanent status {response.status_code}: {response.text}"
                raise ValueError(err_msg)

            if response.status_code != 200:
                raise Exception(
                    f"API call failed with status {response.status_code}: {response.text}"
                )

            data = response.json()
            choices = data.get("choices")
            if not choices:
                raise Exception(f"No choices returned by API: {data}")

            message = choices[0].get("message") or {}
            content = message.get("content")
            if not content:
                raise Exception(
                    f"API returned null/empty content (rate-limit or filter): {data}"
                )
            return content

        raise Exception("API persistently rate-limited (429) after all retries.")

    last_err: Exception | None = None
    delay = 3.0
    for attempt in range(MAX_ERR_RETRIES + 1):
        try:
            return _do_post()
        except ValueError as e:
            raise e
        except Exception as e:
            last_err = e
            if attempt < MAX_ERR_RETRIES:
                print(f"[llm] Attempt {attempt + 1} error: {e}. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2
    raise last_err  # type: ignore[misc]


def call_groq(messages: list, max_tokens: int = 1000, model: str | None = None) -> str:
    import config
    keys = [k for k in config.GROQ_KEYS if k and k != "YOUR_GROQ_KEY"]
    if not keys:
        raise Exception("GROQ_API_KEY is not set in .env. Please set it to proceed.")

    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model or GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.01
    }
    return _post_request(GROQ_BASE_URL, headers, payload, auth_keys=keys)


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def call_gemini(messages: list, max_tokens: int = 1000, model: str = "gemini-3.8-flash") -> str:
    """Gemini through its OpenAI-compatible endpoint (accepts image_url data URIs)."""
    import config
    keys = [k for k in config.GEMINI_KEYS if k]
    if not keys:
        raise Exception("GEMINI_API_KEY is not set in .env. Please set it to proceed.")

    headers = {"Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.01}
    return _post_request(GEMINI_BASE_URL, headers, payload, auth_keys=keys)


def call_groq_orchestrator(messages: list, max_tokens: int = 4096, model: str | None = None) -> str:
    """Groq call using the dedicated Orchestrator key (key index 0)."""
    import config
    key = config.GROQ_ORCHESTRATOR_KEY
    if not key:
        return call_groq(messages, max_tokens=max_tokens, model=model)
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model or GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.01,
    }
    try:
        return _post_request(GROQ_BASE_URL, headers, payload, auth_keys=[key])
    except Exception as e:
        print(f"[llm] Warning: Dedicated Orchestrator key failed ({e}). Falling back to content keys...")
        return call_groq(messages, max_tokens=max_tokens, model=model)


def call_groq_content(messages: list, key_index: int = 0, max_tokens: int = 4096, model: str | None = None) -> str:
    """Groq call using a specific content key (key index 1 or 2)."""
    import config
    keys = config.GROQ_CONTENT_KEYS
    if not keys or key_index >= len(keys):
        return call_groq(messages, max_tokens=max_tokens, model=model)
    key = keys[key_index]
    headers = {"Content-Type": "application/json"}
    import config as _cfg
    payload = {
        "model": model or _cfg.GROQ_CONTENT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.01,
    }
    try:
        return _post_request(GROQ_BASE_URL, headers, payload, auth_keys=[key])
    except Exception as e:
        print(f"[llm] Warning: Content key at index {key_index} failed ({e}). Falling back to general pool...")
        return call_groq(messages, max_tokens=max_tokens, model=model)


def call_groq_tikz(messages: list, max_tokens: int = 4096, model: str | None = None) -> str:
    """Groq call using the dedicated TikZ key (key index 3) to avoid contention with content keys."""
    import config
    key = config.GROQ_TIKZ_KEY
    if not key:
        return call_groq(messages, max_tokens=max_tokens, model=model)
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model or GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.01,
    }
    try:
        return _post_request(GROQ_BASE_URL, headers, payload, auth_keys=[key])
    except Exception as e:
        print(f"[llm] Warning: Dedicated TikZ key failed ({e}). Falling back to content keys...")
        return call_groq(messages, max_tokens=max_tokens, model=model)


def call_deepseek(
    messages: list,
    max_tokens: int = 4096,
    model: str | None = None,
    reasoning_effort: str | None = None,
    thinking_disabled: bool = False,
    role: str = "",
) -> str:
    """Call DeepSeek's OpenAI-compatible chat completions endpoint.

    DeepSeek V4 Pro is a reasoning-first model: `thinking: {type: enabled}` is
    normally REQUIRED in the payload, otherwise the model spends the entire token
    budget on internal reasoning and returns an empty `content` field. We always
    enable thinking and pick an extraction strategy that works whether the model
    returns the final answer in `content` or only in `reasoning_content`.

    Set thinking_disabled=True for simple structured JSON tasks (outlines,
    diagram stubs) where reasoning adds nothing but wasted tokens.

    Retry budget: capped at 3 attempts (vs the default 8 in _post_request) so
    a 429 storm from hitting the 500-concurrent-requests account ceiling bails
    fast and lets the fallback chain try the next provider.
    """
    import config

    key = config.DEEPSEEK_API_KEY
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is not set in .env")

    model = model or config.DEEPSEEK_MODEL
    base_url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    # Flash only, thinking always off: callers' thinking_disabled / reasoning_effort are ignored.
    payload["thinking"] = {"type": "disabled"}

    import httpx
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)) as client:
                response = client.post(base_url, headers=headers, json=payload)
                response.read()
            if response.status_code == 429:
                err = Exception(f"DeepSeek 429: {response.text[:300]}")
                if attempt == 2:
                    raise err
                import time, random
                sleep_dur = min(30.0, (5.0 * (2 ** attempt)) + random.uniform(1.0, 3.0))
                print(f"[llm] DeepSeek 429 on attempt {attempt + 1}, retrying in {sleep_dur:.1f}s...")
                time.sleep(sleep_dur)
                continue
            if response.status_code in (400, 401, 403, 404):
                raise Exception(f"DeepSeek call failed with permanent status {response.status_code}: {response.text}")
            if response.status_code != 200:
                raise Exception(f"DeepSeek call failed with status {response.status_code}: {response.text}")
            data = response.json()
            message = (data.get("choices") or [{}])[0].get("message") or {}
            content = message.get("content")
            if not content:
                reasoning = message.get("reasoning_content")
                if reasoning:
                    return reasoning
                raise Exception(f"DeepSeek returned null/empty content: {data}")

            # Record cost for DeepSeek V4 Pro
            try:
                import cost_logger
                usage = data.get("usage") or {}
                cost_logger.record(
                    "deepseek", model,
                    usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                    role=role,
                )
            except Exception:
                pass

            return content
        except Exception as e:
            if "DeepSeek" in str(e) and ("429" in str(e) or "permanent" in str(e) or "null/empty" in str(e)):
                raise
            last_err = e
            if attempt == 2:
                raise
            import time, random
            sleep_dur = min(30.0, (5.0 * (2 ** attempt)) + random.uniform(1.0, 3.0))
            print(f"[llm] DeepSeek attempt {attempt + 1} error: {e}. Retrying in {sleep_dur:.1f}s...")
            time.sleep(sleep_dur)
    raise last_err  # type: ignore[misc]


def label_image_locally(image_path: Path) -> str:
    """Run labeling locally on the GPU using PaddleOCR for transcription and Ollama Moondream for captioning."""
    from ocr import ocr_path
    
    ocr_text = ocr_path(image_path)
    
    import base64
    b64_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    payload = {
        "model": "moondream",
        "messages": [
            {
                "role": "user",
                "content": "Describe what is shown in this image in one short sentence.",
                "images": [b64_data]
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }
    
    caption_text = ""
    try:
        response = httpx.post("http://localhost:11434/api/chat", json=payload, timeout=15.0)
        if response.status_code == 200:
            caption_text = response.json()["message"]["content"].strip()
    except Exception as e:
        print(f"[llm] Local Ollama moondream captioning failed: {e}")
        
    img_type = "equation" if "_equation_" in image_path.name else "diagram"
    
    keywords = ["math", img_type]
    if ocr_text:
        words = [w.strip(".,()[]{}").lower() for w in ocr_text.split() if len(w) > 3]
        keywords.extend(words[:5])
        
    res = {
        "caption": caption_text or f"Crop of {img_type}",
        "type": img_type,
        "keywords": list(set(keywords))[:6],
        "transcription": ocr_text
    }
    return json.dumps(res)


def label_image_with_vision(image_path: Path, prompt: str) -> str:
    """Send image to cloud APIs first for high quality, falling back to local GPU."""
    media_type = mimetypes.guess_type(image_path)[0] or "image/png"
    b64_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{b64_data}"
                    }
                }
            ]
        }
    ]

    import config
    if config.GEMINI_KEYS:
        from llm import call_gemini
        gemini_models = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.7-flash"]
        for model in gemini_models:
            try:
                print(f"[llm] Trying Gemini vision labeling with: {model}")
                return call_gemini(messages, model=model, max_tokens=1000)
            except Exception as e:
                print(f"[llm] Gemini model {model} failed: {e}")

    if config.NVIDIA_KEYS and config.NVIDIA_KEYS[0]:
        try:
            print("[llm] Trying Nvidia NIM vision labeling with: meta/llama-3.2-11b-vision-instruct")
            return call_nvidia_nim(messages, model="meta/llama-3.2-11b-vision-instruct", max_tokens=600)
        except Exception as e:
            print(f"[llm] Nvidia NIM vision model failed: {e}")

    try:
        print(f"[llm] Trying local GPU vision labeling (PaddleOCR + Moondream) as fallback")
        return label_image_locally(image_path)
    except Exception as e:
        print(f"[llm] Local GPU vision labeling failed: {e}")

    raise RuntimeError("All vision labeling models failed.")


def load_slide_schema() -> dict | None:
    try:
        schema_path = ROOT / "templates" / "slide_spec.schema.json"
        if schema_path.exists():
            schema = json.loads(schema_path.read_text())
            schema.pop("$schema", None)
            schema.pop("title", None)
            return schema
    except Exception as e:
        print(f"[llm] Warning: failed to load or clean slide_spec.schema.json: {e}")
    return None


def _groq_multi_key_generate(messages: list, keys: list[str], model: str, max_tokens: int) -> str:
    """Split context across up to 3 Groq content keys in parallel, then merge JSON specs."""
    import threading, re

    n = min(len(keys), 3)
    if n == 1:
        headers = {"Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.01}
        return _post_request(GROQ_BASE_URL, headers, payload, auth_keys=[keys[0]])

    user_idx = next((i for i, m in enumerate(messages) if m["role"] == "user"), -1)
    if user_idx == -1:
        raise ValueError("No user message found")

    user_content = messages[user_idx]["content"]

    guidance_marker = "\n\n--- DYNAMIC DECK GUIDANCE ---"
    guidance_suffix = ""
    main_content = user_content
    if guidance_marker in user_content:
        main_content, rest = user_content.split(guidance_marker, 1)
        guidance_suffix = guidance_marker + rest

    if "\n\nCONTEXT:\n" in main_content:
        topic_part, context_body = main_content.split("\n\nCONTEXT:\n", 1)
    else:
        topic_part, context_body = "", main_content

    chunk_len = len(context_body) // n
    splits = []
    pos = 0
    for i in range(n - 1):
        end = pos + chunk_len
        boundary = context_body.rfind("\n\n", pos, end + 500)
        splits.append(context_body[pos: boundary if boundary > pos else end])
        pos = boundary if boundary > pos else end
    splits.append(context_body[pos:])

    def make_msgs(chunk):
        m = list(messages)
        content = f"{topic_part}\n\nCONTEXT:\n{chunk}{guidance_suffix}"
        m[user_idx] = {**messages[user_idx], "content": content}
        return m

    results: list = [None] * n
    errors: list = [None] * n

    def call_chunk(idx, half_msgs, key):
        headers = {"Content-Type": "application/json"}
        payload = {"model": model, "messages": half_msgs, "max_tokens": max_tokens, "temperature": 0.01}
        try:
            results[idx] = _post_request(GROQ_BASE_URL, headers, payload, auth_keys=[key])
        except Exception as e:
            errors[idx] = e

    threads = [threading.Thread(target=call_chunk, args=(i, make_msgs(splits[i]), keys[i])) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    valid = [(r, e) for r, e in zip(results, errors) if not e and r]
    if not valid:
        raise Exception(f"All Groq keys failed: {[str(e) for e in errors]}")

    def _extract(text):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {}

    specs = [_extract(r) for r, _ in valid]
    specs = [s for s in specs if s]
    if not specs:
        return valid[0][0]

    merged_slides = []
    seen_titles = set()
    for spec in specs:
        for s in spec.get("slides", []):
            t = s.get("title", "")
            if t not in seen_titles:
                seen_titles.add(t)
                merged_slides.append(s)

    non_summary = [s for s in merged_slides if s.get("type") != "summary"]
    summaries   = [s for s in merged_slides if s.get("type") == "summary"]
    merged = {**specs[0], "slides": non_summary + summaries[:1]}
    total_in = sum(len(s.get("slides", [])) for s in specs)
    print(f"[llm] Groq {n}-key merge: {total_in} → {len(merged['slides'])} slides")
    return json.dumps(merged)


def generate_spec_with_llm(query: str, system_prompt: str, context: str) -> str:
    """Generate slide deck spec JSON. DeepSeek V4 Pro only, then Groq 4-key fallback."""
    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": f"TOPIC: {query}\n\nCONTEXT:\n{context}"
        }
    ]

    import config as _cfg

    # 1. DeepSeek V4 Pro — primary generation (strict JSON, reasoning=high, thinking enabled, 32K tokens)
    if _cfg.DEEPSEEK_API_KEY:
        try:
            print(f"[llm] DeepSeek V4 Pro ({_cfg.DEEPSEEK_MODEL}) for spec generation...")
            raw = call_deepseek(messages, max_tokens=32768, reasoning_effort="high", thinking_disabled=False)
            import re as _re
            clean = raw.strip()
            if clean.startswith("```"):
                match = _re.search(r"```(?:json)?\s*(.*?)\s*```", clean, _re.DOTALL)
                if match:
                    clean = match.group(1).strip()
            import json as _json
            _json.loads(clean)
            return clean
        except Exception as e:
            print(f"[llm] DeepSeek failed ({e}). Trying Groq 4-key fallback...")

    # 2. Groq 4-key fallback (sequential)
    if _cfg.GROQ_KEYS:
        for idx in range(min(4, len(_cfg.GROQ_KEYS))):
            try:
                print(f"[llm] Trying Groq key index {idx} for spec generation...")
                raw = call_groq_content(messages, key_index=idx, max_tokens=4096)
                clean = raw.strip()
                if clean.startswith("```"):
                    import re as _re
                    match = _re.search(r"```(?:json)?\s*(.*?)\s*```", clean, _re.DOTALL)
                    if match:
                        clean = match.group(1).strip()
                import json as _json
                _json.loads(clean)
                return clean
            except Exception as e:
                print(f"[llm] Groq key {idx} failed: {e}")

    raise RuntimeError("All generation providers exhausted.")


def judge_answer_with_llm(question: str, context: str) -> str:
    """Evaluate and grade answer using DeepSeek V4 Pro only, then Groq 4-key fallback."""
    prompt = (
        "Given ONLY this retrieved context, answer the question, then "
        "rate how well the context supports a correct answer on a 1-5 "
        "scale. Reply with just the integer.\n\n"
        f"QUESTION: {question}\n\nCONTEXT:\n{context}"
    )
    messages = [{"role": "user", "content": prompt}]
    import config
    # 1. DeepSeek V4 Pro — reasoning mode on (good for grading)
    if config.DEEPSEEK_API_KEY:
        try:
            return call_deepseek(messages, max_tokens=32768, reasoning_effort="high", thinking_disabled=False)
        except Exception as e:
            print(f"[llm] DeepSeek failed for judge ({e}), trying Groq 4-key fallback...")
    # 2. Groq 4-key fallback
    if config.GROQ_KEYS:
        for idx in range(min(4, len(config.GROQ_KEYS))):
            try:
                print(f"[llm] Trying Groq key index {idx} for judge...")
                return call_groq_content(messages, key_index=idx, max_tokens=2048)
            except Exception as e:
                print(f"[llm] Groq key {idx} failed for judge: {e}")
    raise RuntimeError("All providers exhausted for judge_answer_with_llm.")