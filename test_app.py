# test_app.py

import pytest
from app import app, update_context, DOGGO_DICT
from unittest.mock import patch

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
