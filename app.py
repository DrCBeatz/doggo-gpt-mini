from flask import Flask, render_template, request, Response, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import csv
import json
import logging
import os
import re
import uuid
import time
from typing import Dict, Tuple

import requests
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "https://doggo-gpt-mini.com").split(",") if o.strip()]}})
logging.basicConfig(level=logging.DEBUG)

# --- Core config --------------------------------------------------------------
OLLAMA_API_URL = os.getenv('OLLAMA_API_URL', 'http://ollama:11434')
MODEL = os.getenv('MODEL_NAME', 'llama3.1:8b')
ALLOWED_MODELS = {m.strip() for m in (os.getenv('ALLOWED_MODELS') or MODEL).split(',') if m.strip()}
ALLOWED_DIRECTIONS = {'eng_to_doggo', 'doggo_to_eng'}
CHUNK_SIZE = int(os.getenv("STREAM_CHUNK_SIZE", "8192"))
_timeout = os.getenv("UPSTREAM_TIMEOUT", "")
REQUEST_TIMEOUT = float(_timeout) if _timeout else None
SSE_HEARTBEAT_SECS = float(os.getenv("SSE_HEARTBEAT_SECS", "10"))
NDJSON_HEARTBEAT_SECS = float(os.getenv("NDJSON_HEARTBEAT_SECS", "10"))

PROMPT_INSTRUCTIONS_ENG_TO_DOGGO = """Please translate the following message from English to Doggolingo using the context provided, without any additional text or commentary. Message: """
PROMPT_INSTRUCTIONS_DOGGO_TO_ENG = """Please translate the following message from Doggolingo to English using the context provided, without any additional text or commentary. Message: """

OLLAMA_OPTIONS = {
    "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0")),
    "top_p": float(os.getenv("OLLAMA_TOP_P", "1")),
    "top_k": int(os.getenv("OLLAMA_TOP_K", "0")),
    "repeat_penalty": float(os.getenv("OLLAMA_REPEAT_PENALTY", "1.0")),
    "seed": int(os.getenv("OLLAMA_SEED", "42")),
}

THINK_TAG_MODELS = {
    m.strip().lower()
    for m in (os.getenv("THINK_TAG_MODELS", "deepseek-r1:1.5b").split(","))
    if m.strip()
}

# --- Metrics (Win #1) ---------------------------------------------------------
REQS = Counter(
    'doggo_requests_total', 'Total requests',
    ['route', 'model', 'direction', 'outcome']
)
LAT = Histogram(
    'doggo_request_latency_seconds', 'Request latency (s)',
    ['route', 'model', 'direction'],
    buckets=(0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6)
)
UP_TIMEOUTS = Counter('doggo_upstream_timeouts_total', 'Upstream timeouts', ['model'])
UP_ERRORS   = Counter('doggo_upstream_errors_total',  'Upstream errors',   ['model'])
GUARDRAILS  = Counter('doggo_guardrail_actions_total','Guardrail actions', ['action'])

@app.before_request
def assign_request_id():
    rid = request.headers.get('X-Request-ID') or uuid.uuid4().hex[:12]
    request.request_id = rid

@app.route('/metrics', methods=['GET'])
def metrics():
    # Optional shared-secret to avoid exposing metrics publicly
    key = os.getenv('METRICS_KEY', '')
    if key and request.args.get('k') != key:
        return "Forbidden", 403
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

def log_event(event: str, **fields) -> None:
    fields['event'] = event
    fields.setdefault('rid', getattr(request, 'request_id', None))
    print(json.dumps(fields))

# --- Stream filtering for reasoning models -----------------------------------
class ThinkStripper:
    """Removes <think>...</think> from streamed chunks."""
    OPEN = "<think>"
    CLOSE = "</think>"
    def __init__(self):
        self.in_think = False
        self.tail = ""
    def feed(self, piece: str) -> str:
        s = self.tail + (piece or "")
        self.tail = ""
        out = []
        i = 0
        lo = s.lower()
        while True:
            if self.in_think:
                j = lo.find(self.CLOSE, i)
                if j == -1:
                    keep = max(0, len(s) - (len(self.CLOSE) - 1))
                    self.tail = s[keep:]
                    return "".join(out)
                i = j + len(self.CLOSE)
                self.in_think = False
                lo = s.lower()
            else:
                j = lo.find(self.OPEN, i)
                if j == -1:
                    rem = s[i:]
                    need = len(self.OPEN) - 1
                    if len(rem) > need:
                        out.append(rem[:-need])
                        self.tail = rem[-need:]
                    else:
                        self.tail = rem
                    return "".join(out)
                out.append(s[i:j])
                i = j + len(self.OPEN)
                self.in_think = True
    def flush(self) -> str:
        if not self.in_think and self.tail:
            out = self.tail
            self.tail = ""
            return out
        self.tail = ""
        return ""

# --- Prompt & dictionary helpers ---------------------------------------------
def compose_prompt(query: str, context: str, direction: str) -> str:
    ctx = context.replace("Context: ", "").strip()
    instructions = PROMPT_INSTRUCTIONS_ENG_TO_DOGGO if direction == "eng_to_doggo" else PROMPT_INSTRUCTIONS_DOGGO_TO_ENG
    return f"""{instructions}

Context: {ctx}
Input: {query}
Output:"""

def load_doggo_dictionaries(file_path: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    en2dog, dog2en = {}, {}
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
        for english, doggo in reader:
            e = english.strip().lower()
            d = doggo.strip().lower()
            en2dog.setdefault(e, d)
            dog2en.setdefault(d, e)
    return en2dog, dog2en

EN2DOG, DOG2EN = load_doggo_dictionaries('data/doggo_dictionary.csv')

def load_doggo_dictionary(file_path):
    """Back-compat shim for tests: returns a merged bi-directional dict."""
    en2dog, dog2en = load_doggo_dictionaries(file_path)
    merged = {}
    merged.update(en2dog)
    merged.update({k: v for k, v in dog2en.items()})
    return merged

def update_context(user_input: str, direction: str) -> str:
    context = "Context: "
    text = user_input
    mapping = EN2DOG if direction == "eng_to_doggo" else DOG2EN
    for src, dst in mapping.items():
        if re.search(rf"\b{re.escape(src)}\b", text, flags=re.I):
            context += f"{src}->{dst}; "
    return context

# --- Guardrails (Win #2) ------------------------------------------------------
PII_REDACTION = os.getenv('PII_REDACTION', 'on').lower() == 'on'
MAX_INPUT_CHARS = int(os.getenv('MAX_INPUT_CHARS', '500'))

EMAIL_RX = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')
PHONE_RX = re.compile(r'(?x)(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4})')
INJECTION_PATTERNS = [
    r'(?i)ignore (?:all|previous) instructions',
    r'(?i)reveal (?:the )?(?:system|hidden) prompt',
    r'(?i)you are now .*',
]

def redact_pii(txt: str) -> str:
    return PHONE_RX.sub('[REDACTED:PHONE]', EMAIL_RX.sub('[REDACTED:EMAIL]', txt))

def strip_injection(txt: str) -> str:
    out = txt
    for p in INJECTION_PATTERNS:
        out = re.sub(p, '[BLOCKED-INJECTION]', out)
    return out

def sanitize_user_input(txt: str) -> Tuple[str, Dict]:
    actions = []
    original_len = len(txt)
    if len(txt) > MAX_INPUT_CHARS:
        txt = txt[:MAX_INPUT_CHARS]; actions.append('truncate')
    if PII_REDACTION:
        _t = redact_pii(txt)
        if _t != txt: actions.append('pii_redacted')
        txt = _t
    _t = strip_injection(txt)
    if _t != txt: actions.append('injection_stripped')
    txt = _t
    for a in actions: GUARDRAILS.labels(a).inc()
    return txt, {'actions': actions, 'original_len': original_len, 'sanitized_len': len(txt)}

def deterministic_translate(user_input: str, direction: str) -> str:
    """Dictionary-based best-effort translation for reliability fallback."""
    mapping = EN2DOG if direction == 'eng_to_doggo' else DOG2EN
    token_rx = re.compile(r"\b\w+\b", re.UNICODE)
    def repl(m):
        w = m.group(0)
        return mapping.get(w.lower(), w)
    return token_rx.sub(repl, user_input)

def stream_fallback_ndjson(answer: str, model: str = 'dictionary-fallback') -> Response:
    """NDJSON streaming shape for the /chat endpoint fallback."""
    def gen():
        yield json.dumps({
            "model": model,
            "message": {"role": "assistant", "content": answer},
            "done": False
        }) + "\n"
        yield json.dumps({"done": True}) + "\n"
    return Response(gen(), content_type="text/plain")

def stream_fallback_sse(answer: str) -> Response:
    """SSE streaming shape for the /chat_json endpoint fallback."""
    def gen():
        yield f"data: {json.dumps({'content': answer})}\n\n"
    return Response(gen(), content_type="text/event-stream")

# --- Upstream call + streaming adapters --------------------------------------
def _ollama_post(prompt: str, model: str):
    """Start a streaming chat call to Ollama; returns the raw Response."""
    return requests.post(
        f"{OLLAMA_API_URL}/api/chat",
        json={
            "model": model,
            "messages": [{'role': 'user', 'content': prompt}],
            "options": OLLAMA_OPTIONS,
        },
        stream=True,
        timeout=REQUEST_TIMEOUT,
    )

def ask_question(query: str, context: str, direction: str, model: str) -> Response:
    response = _ollama_post(compose_prompt(query, context, direction), model)
    use_filter = model.lower() in THINK_TAG_MODELS
    think = ThinkStripper() if use_filter else None

    def generate():
        if response.status_code != 200:
            response.raise_for_status()

        buf = ""
        last_emit = time.time()
        HB = NDJSON_HEARTBEAT_SECS

        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            buf += chunk.decode("utf-8", errors="ignore")

            while True:
                nl = buf.find("\n")
                if nl == -1:
                    break
                line = buf[:nl]
                buf = buf[nl + 1:]
                now = time.time()
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    if use_filter:
                        cleaned = re.sub(r"(?is)<think>.*?</think>", "", line)
                        if cleaned.strip():
                            yield cleaned + "\n"; last_emit = now
                        elif now - last_emit >= HB:
                            yield '{"keepalive": true}\n'; last_emit = now
                    else:
                        yield line + "\n"; last_emit = now
                    continue

                content = (obj.get("message") or {}).get("content", "")
                wrote = False
                if content:
                    cleaned = think.feed(content) if use_filter else content
                    if cleaned:
                        obj["message"]["content"] = cleaned
                        yield json.dumps(obj) + "\n"; last_emit = now; wrote = True

                if obj.get("done") is True:
                    if use_filter:
                        tail = think.flush()
                        if tail:
                            extra = {"model": obj.get("model"), "message": {"role": "assistant", "content": tail}, "done": False}
                            yield json.dumps(extra) + "\n"; last_emit = now
                    yield json.dumps(obj) + "\n"; last_emit = now; wrote = True

                if not wrote:
                    if use_filter:
                        if now - last_emit >= HB:
                            yield '{"keepalive": true}\n'; last_emit = now
                    else:
                        yield json.dumps(obj) + "\n"; last_emit = now

        if use_filter:
            tail = think.flush()
            if tail:
                yield json.dumps({"message": {"role": "assistant", "content": tail}, "done": False}) + "\n"

    return Response(generate(), content_type="text/plain")

def ask_question_json(query: str, context: str, direction: str, model: str) -> Response:
    response = _ollama_post(compose_prompt(query, context, direction), model)
    use_filter = model.lower() in THINK_TAG_MODELS
    think = ThinkStripper() if use_filter else None

    def generate():
        if response.status_code != 200:
            response.raise_for_status()

        last_emit = time.time()
        HB = SSE_HEARTBEAT_SECS

        for raw in response.iter_lines():
            if not raw:
                now = time.time()
                if use_filter and (now - last_emit >= HB):
                    yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"; last_emit = now
                continue

            now = time.time()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                if use_filter and (now - last_emit >= HB):
                    yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"; last_emit = now
                continue

            msg = (data.get("message") or {}).get("content", "")
            emitted = False
            if msg:
                cleaned = think.feed(msg) if use_filter else msg
                if cleaned:
                    yield f"data: {json.dumps({'content': cleaned})}\n\n"; last_emit = now; emitted = True

            if use_filter and not emitted and (now - last_emit >= HB):
                yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"; last_emit = now

            if data.get("done") is True:
                if use_filter:
                    tail = think.flush()
                    if tail:
                        yield f"data: {json.dumps({'content': tail})}\n\n"
                break

    return Response(generate(), content_type="text/event-stream")

# --- Routes -------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    t0 = time.time()
    route = '/chat'
    user_input_raw = request.form['message'].strip()
    direction = request.form['direction']
    model = request.form.get('model', MODEL)

    # Validation
    if not user_input_raw or direction not in ALLOWED_DIRECTIONS or model not in ALLOWED_MODELS:
        REQS.labels(route, model, direction, 'validation_error').inc()
        log_event('validation_error', direction=direction, model=model, empty=not bool(user_input_raw))
        return jsonify({'error': 'Invalid input'}), 400

    # Guardrails sanitize (Win #2)
    user_input, meta = sanitize_user_input(user_input_raw)
    context = update_context(user_input, direction)

    try:
        resp = ask_question(user_input, context, direction, model)
        REQS.labels(route, model, direction, 'ok').inc()
        LAT.labels(route, model, direction).observe(time.time() - t0)  # note: time-to-first-byte
        log_event('request_ok',
                  model=model, direction=direction,
                  chars=len(user_input_raw), sanitized=len(user_input),
                  guardrail_actions=meta['actions'], t_ms=int((time.time()-t0)*1000))
        return resp
    except requests.exceptions.Timeout:
        UP_TIMEOUTS.labels(model).inc()
        REQS.labels(route, model, direction, 'timeout_fallback').inc()
        LAT.labels(route, model, direction).observe(time.time() - t0)
        answer = deterministic_translate(user_input, direction)
        log_event('timeout_fallback', model=model, direction=direction, answer_len=len(answer))
        return stream_fallback_ndjson(answer)
    except requests.exceptions.RequestException as e:
        UP_ERRORS.labels(model).inc()
        REQS.labels(route, model, direction, 'upstream_fallback').inc()
        LAT.labels(route, model, direction).observe(time.time() - t0)
        answer = deterministic_translate(user_input, direction)
        log_event('upstream_fallback', model=model, direction=direction, err=str(e), answer_len=len(answer))
        return stream_fallback_ndjson(answer)

@app.route('/chat_json', methods=['POST'])
def chat_json():
    t0 = time.time()
    route = '/chat_json'
    user_input_raw = request.form['message'].strip()
    direction = request.form['direction']
    model = request.form.get('model', MODEL)

    # Validation
    if not user_input_raw or direction not in ALLOWED_DIRECTIONS or model not in ALLOWED_MODELS:
        REQS.labels(route, model, direction, 'validation_error').inc()
        return jsonify({'error': 'Invalid input'}), 400

    # Guardrails sanitize (Win #2)
    user_input, meta = sanitize_user_input(user_input_raw)
    context = update_context(user_input, direction)

    try:
        resp = ask_question_json(user_input, context, direction, model)
        REQS.labels(route, model, direction, 'ok').inc()
        LAT.labels(route, model, direction).observe(time.time() - t0)  # note: time-to-first-byte
        log_event('request_ok',
                  model=model, direction=direction,
                  chars=len(user_input_raw), sanitized=len(user_input),
                  guardrail_actions=meta['actions'], t_ms=int((time.time()-t0)*1000))
        return resp
    except requests.exceptions.Timeout:
        UP_TIMEOUTS.labels(model).inc()
        REQS.labels(route, model, direction, 'timeout_fallback').inc()
        LAT.labels(route, model, direction).observe(time.time() - t0)
        return stream_fallback_sse(deterministic_translate(user_input, direction))
    except requests.exceptions.RequestException:
        UP_ERRORS.labels(model).inc()
        REQS.labels(route, model, direction, 'upstream_fallback').inc()
        LAT.labels(route, model, direction).observe(time.time() - t0)
        return stream_fallback_sse(deterministic_translate(user_input, direction))

@app.route('/health', methods=['GET'])
def health_check():
    return "OK", 200

if __name__ == '__main__':
    # For local dev only; container uses gunicorn per Dockerfile
    app.run(debug=True, host='0.0.0.0', port=80)