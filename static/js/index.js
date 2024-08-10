// static/js/index.js

function showAlert(message, type = 'error') {
    const alertBox = document.getElementById('chat-alert');
    const body = document.body;
    alertBox.textContent = message;
    alertBox.classList.add('chat__alert--visible');
    alertBox.classList.remove('chat__alert--hidden');
    body.classList.add('prevent-scroll');

    if (type === 'error') {
        alertBox.classList.add('chat__alert--error');
    } else {
        alertBox.classList.remove('chat__alert--error');
    }

    // Hide the alert after 3 seconds with a fade-out effect
    setTimeout(() => {
        alertBox.classList.add('chat__alert--hidden');
        alertBox.classList.remove('chat__alert--visible');
        body.classList.remove('prevent-scroll');

        // Optionally hide the element after the fade-out transition completes
        setTimeout(() => {
            alertBox.classList.remove('chat__alert--hidden');
        }, 500); // Match this duration to the transition duration in CSS
    }, 3000);
}

document.getElementById('chat-form').addEventListener('submit', function(event) {
    event.preventDefault();
    const messageBox = document.querySelector('.chat__message-box');
    const directionSelect = document.querySelector('.chat__translation-direction');
    const chatLog = document.getElementById('chat-log');
    const spinner = document.getElementById('spinner');
    const chatButton = document.querySelector('.chat__button');
    const message = messageBox.value;
    const direction = directionSelect.value;

    if (message === '') {
        showAlert('Please enter a message before submitting.');
        return;
    }

    if (!['eng_to_doggo', 'doggo_to_eng'].includes(direction)) {
        showAlert('Please select a valid translation direction.');
        return;
    }

    // Show the loading spinner while waiting for a response
    spinner.style.display = 'block';
    document.body.classList.add('prevent-scroll'); // Prevent scrolling while spinner is shown
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
                    document.body.classList.remove('prevent-scroll'); // Re-enable scrolling
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
        showAlert('Something went wrong. Please try again later.');
        // Hide the spinner and enable the button in case of an error
        spinner.style.display = 'none';
        document.body.classList.remove('prevent-scroll'); // Re-enable scrolling
        chatButton.disabled = false;
    });
});
