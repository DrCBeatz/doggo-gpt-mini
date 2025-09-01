# app.py

from flask import Flask, render_template, request, Response, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import csv
import json
import logging
import os
import re
import time
from typing import Dict, Tuple

import requests

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "https://doggo-gpt-mini.com").split(",") if o.strip()]}})
logging.basicConfig(level=logging.DEBUG)

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

class ThinkStripper:
    """
    Streaming remover for <think>...</think> blocks that may span chunks.
    Keeps a small boundary 'tail' so it catches tags split across chunk edges.
    """
    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self.in_think = False
        self.tail = ""   # carry-over for partial tag boundaries

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
                    # Still inside <think>; keep only a small tail to catch a future </think>
                    keep = max(0, len(s) - (len(self.CLOSE) - 1))
                    self.tail = s[keep:]
                    return "".join(out)
                # skip closing tag
                i = j + len(self.CLOSE)
                self.in_think = False
                lo = s.lower()
            else:
                j = lo.find(self.OPEN, i)
                if j == -1:
                    # Output remainder, but hold a small tail to catch a future "<think"
                    rem = s[i:]
                    need = len(self.OPEN) - 1  # 6 chars
                    if len(rem) > need:
                        out.append(rem[:-need])
                        self.tail = rem[-need:]
                    else:
                        # too short; carry it entirely to next call
                        self.tail = rem
                    return "".join(out)
                out.append(s[i:j])            # output up to <think>
                i = j + len(self.OPEN)        # enter think mode
                self.in_think = True

    def flush(self) -> str:
        # On stream end, emit any safe tail only if we're not inside a <think> block
        if not self.in_think and self.tail:
            out = self.tail
            self.tail = ""
            return out
        self.tail = ""
        return ""

def compose_prompt(query: str, context: str, direction: str) -> str:
    ctx = context.replace("Context: ", "").strip()
    if direction == "eng_to_doggo":
        instructions = PROMPT_INSTRUCTIONS_ENG_TO_DOGGO
    else:
        instructions = PROMPT_INSTRUCTIONS_DOGGO_TO_ENG
    return f"""{instructions}

Context: {ctx}
Input: {query}
Output:"""


def log_event(event: str, **fields) -> None:
    fields['event'] = event
    print(json.dumps(fields))


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
    for k, v in dog2en.items():
        merged[k] = v
    return merged


def update_context(user_input: str, direction: str) -> str:
    context = "Context: "
    text = user_input
    mapping = EN2DOG if direction == "eng_to_doggo" else DOG2EN
    for src, dst in mapping.items():
        if re.search(rf"\b{re.escape(src)}\b", text, flags=re.I):
            context += f"{src}->{dst}; "
    return context


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

            # Emit full NDJSON lines as we receive them
            while True:
                nl = buf.find("\n")
                if nl == -1:
                    break
                line = buf[:nl]
                buf = buf[nl + 1:]

                now = time.time()

                # Try parse upstream NDJSON
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Not JSON (rare). If filtering, strip think tags; else pass through.
                    if use_filter:
                        cleaned = re.sub(r"(?is)<think>.*?</think>", "", line)
                        if cleaned.strip():
                            yield cleaned + "\n"
                            last_emit = now
                        elif now - last_emit >= HB:
                            yield '{"keepalive": true}\n'
                            last_emit = now
                    else:
                        yield line + "\n"
                        last_emit = now
                    continue

                content = (obj.get("message") or {}).get("content", "")
                wrote = False

                if content:
                    cleaned = think.feed(content) if use_filter else content
                    if cleaned:
                        obj["message"]["content"] = cleaned
                        yield json.dumps(obj) + "\n"
                        last_emit = now
                        wrote = True

                # Finish stream: flush any safe tail just before 'done: true'
                if obj.get("done") is True:
                    if use_filter:
                        tail = think.flush()
                        if tail:
                            extra = {
                                "model": obj.get("model"),
                                "message": {"role": "assistant", "content": tail},
                                "done": False,
                            }
                            yield json.dumps(extra) + "\n"
                            last_emit = now
                    yield json.dumps(obj) + "\n"
                    last_emit = now
                    wrote = True

                # If nothing to forward and we’re stripping, send a keepalive instead of leaking <think>
                if not wrote:
                    if use_filter:
                        if now - last_emit >= HB:
                            yield '{"keepalive": true}\n'
                            last_emit = now
                    else:
                        # Non‑reasoning models: just pass through
                        yield json.dumps(obj) + "\n"
                        last_emit = now

        # Stream ended: flush any safe tail if filtering
        if use_filter:
            tail = think.flush()
            if tail:
                yield json.dumps({"message": {"role": "assistant", "content": tail}, "done": False}) + "\n"

    return Response(generate(), content_type="text/plain")

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat', methods=['POST'])
def chat():
    t0 = time.time()
    user_input = request.form['message'].strip()
    direction = request.form['direction']
    model = request.form.get('model', MODEL)
    if not user_input or direction not in ALLOWED_DIRECTIONS or model not in ALLOWED_MODELS:
        log_event('validation_error', direction=direction, model=model, empty=not bool(user_input))
        return jsonify({'error': 'Invalid input'}), 400
    context = update_context(user_input, direction)
    try:
        resp = ask_question(user_input, context, direction, model)
        log_event('request_ok',
                  model=model, direction=direction,
                  chars=len(user_input), t_ms=int((time.time() - t0) * 1000))
        return resp
    except requests.exceptions.Timeout:
        log_event('timeout', model=model, direction=direction)
        return jsonify({'error': 'Timed out'}), 504
    except requests.exceptions.RequestException as e:
        log_event('upstream_error', model=model, direction=direction, err=str(e))
        return jsonify({'error': 'Upstream error'}), 500


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
                # No line right now; if filtering and we've been quiet, send a heartbeat
                now = time.time()
                if use_filter and (now - last_emit >= HB):
                    yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"
                    last_emit = now
                continue

            now = time.time()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Silently ignore non‑JSON lines, but keep the connection alive if needed
                if use_filter and (now - last_emit >= HB):
                    yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"
                    last_emit = now
                continue

            msg = (data.get("message") or {}).get("content", "")
            emitted = False

            if msg:
                cleaned = think.feed(msg) if use_filter else msg
                if cleaned:
                    yield f"data: {json.dumps({'content': cleaned})}\n\n"
                    last_emit = now
                    emitted = True

            # If we didn’t emit due to stripping, send a heartbeat occasionally
            if use_filter and not emitted and (now - last_emit >= HB):
                yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"
                last_emit = now

            if data.get("done") is True:
                if use_filter:
                    tail = think.flush()
                    if tail:
                        yield f"data: {json.dumps({'content': tail})}\n\n"
                # end the SSE stream
                break

    return Response(generate(), content_type="text/event-stream")

@app.route('/chat_json', methods=['POST'])
def chat_json():
    user_input = request.form['message'].strip()
    direction = request.form['direction']
    model = request.form.get('model', MODEL)

    if not user_input:
        return jsonify({'error': 'Message cannot be empty'}), 400
    if direction not in ALLOWED_DIRECTIONS:
        return jsonify({'error': 'Invalid direction'}), 400
    if model not in ALLOWED_MODELS:
        return jsonify({'error': 'Model not allowed'}), 400

    logging.debug(f"User input: {user_input}, Direction: {direction}, Model: {model}")
    context = update_context(user_input, direction)

    try:
        return ask_question_json(user_input, context, direction, model)
    except requests.exceptions.Timeout:
        return jsonify({'error': 'The request to the translation service timed out.'}), 504
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        return jsonify({'error': 'An error occurred while processing your request.'}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return "OK", 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=80)