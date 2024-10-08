# test_app.py

import os
from dotenv import load_dotenv
import pytest
from app import (
    app, 
    update_context,
    load_doggo_dictionary
    )
from unittest.mock import patch
import csv
import requests

@pytest.fixture
def client():
    with app.test_client() as client:
        yield client

def mock_requests_post(*args, **kwargs):
    class MockResponse:
        def __init__(self, content):
            self.content = content
        
        def iter_content(self, chunk_size=8192):
            yield self.content
        
        def json(self):
            return {"message": {"content": "Woof woof"}}
    return MockResponse(b'{{"message": {"content": "Woof woof"}}}')

def test_index_route(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'DoggoGPT-Mini' in response.data

@patch('app.requests.post', side_effect=mock_requests_post)
def test_chat_route(mock_post, client):
    response = client.post('/chat', data={'message': 'Hello', 'direction': 'eng_to_doggo'})
    assert response.status_code == 200
    assert b'Woof woof' in response.data

def test_update_context():
    # Test English to Doggo
    user_input = "I want to eat chicken nuggets"
    direction = "eng_to_doggo"
    context = update_context(user_input, direction)
    assert "eat->" in context
    assert "chicken->" in context
    assert "nuggets->" in context

    # Test Doggo to English
    user_input = "I am doggo, bork bork!"
    direction = "doggo_to_eng"
    context = update_context(user_input, direction)
    assert "bork->" in context
    assert "doggo->" in context

def test_load_doggo_dictionary():
    # Mock CSV file pth
    test_csv_path = 'data/test_doggo_dictionary.csv'
    with open(test_csv_path, mode='w', newline='') as file:
        write = csv.writer(file)
        write.writerow(['English', 'Doggo'])
        write.writerow(['bone', 'bark'])

    doggo_dict = load_doggo_dictionary(test_csv_path)
    assert doggo_dict['bone'] == 'bark'
    assert doggo_dict['bark'] == 'bone'

    # Clean up
    os.remove(test_csv_path)

def test_env_vars():
    load_dotenv()
    assert os.getenv('OLLAMA_API_URL') == 'http://ollama:11434'
    assert os.getenv('MODEL_NAME') == 'llama3.1:8b'

def test_update_context_no_matches():
    user_input = "This phrase has no matching words"
    direction = "eng_to_doggo"
    context = update_context(user_input, direction)
    assert context == "Context: "  # Expecting an empty context since no words matched

def test_update_context_case_insensitive():
    user_input = "I WANT TO EAT Chicken Nuggets"
    direction = "eng_to_doggo"
    context = update_context(user_input.lower(), direction)
    assert "eat->" in context
    assert "chicken->" in context
    assert "nuggets->" in context

@patch('app.requests.post')
def test_chat_route_timeout(mock_post, client):
    mock_post.side_effect = requests.exceptions.Timeout
    response = client.post('/chat', data={'message': 'Hello', 'direction': 'eng_to_doggo'})
    assert response.status_code == 504

def test_favicon_served(client):
    response = client.get('/static/favicon.ico')
    assert response.status_code == 200
    assert response.content_type == 'image/vnd.microsoft.icon'

def test_index_html(client):
    response = client.get('/')
    assert b'<title>DoggoGPT-Mini</title>' in response.data
    assert b'<h1 class="chat__title">DoggoGPT-Mini</h1>' in response.data
    assert b'<h2 class="chat__subtitle">AI-Powered DoggoLingo Translation</h2>' in response.data

def test_chat_route_empty_input(client):
    response = client.post('/chat', data={'message': '', 'direction': 'eng_to_doggo'})
    assert response.status_code == 400

def test_chat_route_invalid_direction(client):
    response = client.post('/chat', data={'message': 'Hello', 'direction': 'invalid_direction'})
    assert response.status_code == 400