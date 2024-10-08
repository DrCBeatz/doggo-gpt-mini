# app.py

from flask import Flask, render_template, request, Response, jsonify
from flask_cors import CORS
import os
import requests
import logging
from dotenv import load_dotenv
import csv
import json

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "https://doggo-gpt-mini.com"}})
logging.basicConfig(level=logging.DEBUG)

OLLAMA_API_URL = os.getenv('OLLAMA_API_URL', 'http://ollama:11434')
MODEL = os.getenv('MODEL_NAME', 'llama3.1:8b')

PROMPT_INSTRUCTIONS_ENG_TO_DOGGO = """Please translate the following message from English to Doggolingo using the context provided, without any additional text or commentary. Message: """
PROMPT_INSTRUCTIONS_DOGGO_TO_ENG = """Please translate the following message from Doggolingo to English using the context provided, without any additional text or commentary. Message: """

def load_doggo_dictionary(file_path):
    doggo_dict = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip the header row
        for row in reader:
            english, doggo = row
            doggo_dict[english.lower()] = doggo
            doggo_dict[doggo.lower()] = english
    return doggo_dict

DOGGO_DICT = load_doggo_dictionary('data/doggo_dictionary.csv')

def update_context(user_input, direction):
    context = "Context: "
    if direction == "eng_to_doggo":
        for english, doggo in DOGGO_DICT.items():
            if english in user_input.lower():
                context += f"{english}->{doggo}; "
    else:  # direction == "doggo_to_eng"
        for doggo, english in DOGGO_DICT.items():
            if doggo in user_input.lower():
                context += f"{doggo}->{english}; "
    return context

def ask_question(query, context, direction):
    if direction == "eng_to_doggo":
        prompt_instructions = PROMPT_INSTRUCTIONS_ENG_TO_DOGGO
    else:
        prompt_instructions = PROMPT_INSTRUCTIONS_DOGGO_TO_ENG

    response = requests.post(
        f"{OLLAMA_API_URL}/api/chat",
        json={"model": MODEL, "messages": [{'role': 'user', 'content': prompt_instructions + query + context}]},
        stream=True
    )
    def generate():
        for chunk in response.iter_content(chunk_size=8192):
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
    user_input = request.form['message'].strip()
    direction = request.form['direction']

    if not user_input:
        return jsonify({'error': 'Message cannot be empty'}), 400

    if direction not in ['eng_to_doggo', 'doggo_to_eng']:
        return jsonify({'error': 'Invalid direction'}), 400

    logging.debug(f"User input: {user_input}, Direction: {direction}")
    context = update_context(user_input, direction)

    try:
        return ask_question(user_input, context, direction)
    except requests.exceptions.Timeout:
        # Return a 504 Gateway Timeout if a timeout exception is caught
        return jsonify({'error': 'The request to the translation service timed out.'}), 504
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        # Handle other potential request errors here
        return jsonify({'error': 'An error occurred while processing your request.'}), 500

def ask_question_json(query, context, direction):
    if direction == "eng_to_doggo":
        prompt_instructions = PROMPT_INSTRUCTIONS_ENG_TO_DOGGO
    else:
        prompt_instructions = PROMPT_INSTRUCTIONS_DOGGO_TO_ENG

    response = requests.post(
        f"{OLLAMA_API_URL}/api/chat",
        json={"model": MODEL, "messages": [{'role': 'user', 'content': prompt_instructions + query + context}]},
        stream=True
    )

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

    if direction not in ['eng_to_doggo', 'doggo_to_eng']:
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
