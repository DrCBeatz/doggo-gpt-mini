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
ALLOWED_DIRECTIONS = {'eng_to_doggo', 'doggo_to_eng'}
CHUNK_SIZE = int(os.getenv("STREAM_CHUNK_SIZE", "8192"))
# Keep default behavior (no timeout) unless explicitly set:
_timeout = os.getenv("UPSTREAM_TIMEOUT", "")
REQUEST_TIMEOUT = float(_timeout) if _timeout else None

PROMPT_INSTRUCTIONS_ENG_TO_DOGGO = """Please translate the following message from English to Doggolingo using the context provided, without any additional text or commentary. Message: """
PROMPT_INSTRUCTIONS_DOGGO_TO_ENG = """Please translate the following message from Doggolingo to English using the context provided, without any additional text or commentary. Message: """

OLLAMA_OPTIONS = {
    "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0")),
    "top_p": float(os.getenv("OLLAMA_TOP_P", "1")),
    "top_k": int(os.getenv("OLLAMA_TOP_K", "0")),
    "repeat_penalty": float(os.getenv("OLLAMA_REPEAT_PENALTY", "1.0")),
    "seed": int(os.getenv("OLLAMA_SEED", "42")),
}


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
            # keep first occurrence to avoid CSV duplicates overriding
            en2dog.setdefault(e, d)
            dog2en.setdefault(d, e)
    return en2dog, dog2en


EN2DOG, DOG2EN = load_doggo_dictionaries('data/doggo_dictionary.csv')


def load_doggo_dictionary(file_path):
    """Back-compat shim for tests: returns a merged bi-directional dict."""
    en2dog, dog2en = load_doggo_dictionaries(file_path)
    merged = {}
    merged.update(en2dog)
    # merge reverse direction too so tests can assert both ways
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


def ask_question(query: str, context: str, direction: str) -> Response:
    response = _ollama_post(compose_prompt(query, context, direction), MODEL)

    def generate():
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                chunk_str = chunk.decode('utf-8')
                logging.debug(f"Chunk: {chunk_str}")
                yield chunk_str

    return Response(generate(), content_type='text/plain')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat', methods=['POST'])
def chat():
    t0 = time.time()
    user_input = request.form['message'].strip()
    direction = request.form['direction']
    if not user_input or direction not in ALLOWED_DIRECTIONS:
        log_event('validation_error', direction=direction, empty=not bool(user_input))
        return jsonify({'error': 'Invalid input'}), 400
    context = update_context(user_input, direction)
    try:
        resp = ask_question(user_input, context, direction)
        log_event('request_ok',
                  model=MODEL, direction=direction,
                  chars=len(user_input), t_ms=int((time.time() - t0) * 1000))
        return resp
    except requests.exceptions.Timeout:
        log_event('timeout', model=MODEL, direction=direction)
        return jsonify({'error': 'Timed out'}), 504
    except requests.exceptions.RequestException as e:
        log_event('upstream_error', model=MODEL, direction=direction, err=str(e))
        return jsonify({'error': 'Upstream error'}), 500


def ask_question_json(query: str, context: str, direction: str) -> Response:
    response = _ollama_post(compose_prompt(query, context, direction), MODEL)

    def generate():
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        logging.debug(f"Parsed data: {data}")
                        message_content = data.get('message', {}).get('content', '')
                        if message_content:
                            yield f"data: {json.dumps({'content': message_content})}\n\n"
                    except json.JSONDecodeError as e:
                        logging.error(f"JSON decode error: {e}")
                        continue  # Skip lines that aren't valid JSON
        else:
            response.raise_for_status()

    return Response(generate(), content_type='text/event-stream')


@app.route('/chat_json', methods=['POST'])
def chat_json():
    user_input = request.form['message'].strip()
    direction = request.form['direction']

    if not user_input:
        return jsonify({'error': 'Message cannot be empty'}), 400

    if direction not in ALLOWED_DIRECTIONS:
        return jsonify({'error': 'Invalid direction'}), 400

    logging.debug(f"User input: {user_input}, Direction: {direction}")
    context = update_context(user_input, direction)

    try:
        # Directly return the streaming response
        return ask_question_json(user_input, context, direction)
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