// static/js/index.js

document.getElementById('chat-form').addEventListener('submit', function(event) {
    event.preventDefault();
    const messageBox = document.querySelector('.chat__message-box');
    const directionSelect = document.querySelector('.chat__translation-direction');
    const chatLog = document.getElementById('chat-log');
    const spinner = document.getElementById('spinner');
    const chatButton = document.querySelector('.chat__button');
    const message = messageBox.value;
    const direction = directionSelect.value;

    // Show the loading spinner while waiting for a response
    spinner.style.display = 'block';
    // Disable the button
    chatButton.disabled = true;

    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: new URLSearchParams({
            'message': message,
            'direction': direction
        })
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        chatLog.innerHTML += '<p><strong>Input:</strong> ' + message + '</p>';
        messageBox.value = '';

        function readChunk() {
            reader.read().then(function processText({ done, value }) {
                if (done) {
                    chatLog.innerHTML += '<hr>';
                    // Hide the loading spinner when the response starts
                    spinner.style.display = 'none';
                    // Enable the button
                    chatButton.disabled = false;
                    return;
                }
                const chunk = decoder.decode(value, { stream: true });
                console.log(`Received chunk: ${chunk}`);
                try {
                    const json = JSON.parse(chunk);
                    if (json.message && json.message.content) {
                        chatLog.innerHTML += '<span>' + json.message.content.replace(/\n/g, '<br>') + '</span>';
                        chatLog.scrollTop = chatLog.scrollHeight;
                    }
                } catch (e) {
                    console.error(`Error parsing chunk: ${e}`);
                }
                return reader.read().then(processText);
            });
        }
        chatLog.innerHTML += '<strong>DoggoGPT: </strong>';
        readChunk();
    }).catch(error => {
        console.error('Error:', error);
        // Hide the spiner and enable the button in case of an error
        spinner.style.display = 'none';
        chatButton.disabled = false;
    });
});
