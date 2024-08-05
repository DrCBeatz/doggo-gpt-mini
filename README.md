# DoggoGPT-Mini 

![DoggoGPT-Mini Chat App iPhone Screenshot](static/images/doggo_gpt_mini_screenshot.jpg)

## Description

DoggoGPT-Mini is a translation app that translates between English and DoggoLingo (internet-speak for how dogs "talk"). It uses Ollama to run a small open-source LLM (Llama 3.1 8b) locally, but can be deployed with any LLM supported by Ollama.

The app also performs context retrieval by searching for English/DoggoLingo terms in a CSV file (`data/doggo_dictionary.csv`) and adds them to the prompt context. The CSV file can be modified to customize the translations.

## Requirements

- Git
- Docker
- Docker Compose

## Local Installation

1. **Clone the repository**:
 
`git clone https://github.com/DrCBeatz/doggo-gpt-mini.git`

2. **Navigate to the project directory**:

`cd doggo-gpt-mini`

3. **Build and run the containers**:

`docker-compose up -d --build`

4. **Pull the Llama 3.1 8b model**:

`docker-compose exec ollama ollama pull llama3.1:8b`

5. **Access the app**:

Open your web browser and go to:

`http://localhost:5000`

## Usage

1. Select the translation direction by clicking on the input field that says 'English to Doggo' (can be changed to 'Doggo to English').
2. Enter the message to be translated in the field 'Enter your message...'.
3. Click the 'Translate Message' button.

You can try traslating the following English messages to DoggoLingo:

- `Hello friends!`
- `Hello human, I am a dog!`
- `I like to eat chicken nuggets, and drink Pepsi, not Coke. Bark bark!`


## Deployment

### Deploying on EC2

1. ***Launch an EC2 instance:***
Follow AWS documentation[https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html] to set up an EC2 instance.

Recommended EC2 settings:
- Choose the Amazon Linux 2023 Amazon Machine Image (Free tier elgigble).
- Choose the c5.2xlarge Instance type (8 vCPUs, 16 Gb RAM) for adequate processing power.
- Choose EC2 Spot Instances (under Advanced details in the AWS Management Console) to save 90% on costs.

2. ***Conect to your instance:*** 
SSH into your EC2 instance, or use AWS EC2 Connect in the AWS Management Console.

3. ***Install Docker and Docker Compose***:
```bash
sudo yum update -y
sudo yum install docker -y
sudo service docker start
sudo systemctl enable docker
sudo usermod -a -G docker ec2-user
sudo chmod 666 /var/run/docker.sock
sudo yum install git -y
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

4. ***Clone the repository and set up the application***:
```bash
git clone https://github.com/DrCBeatz/doggo-gpt-mini.git
cd doggo-gpt-mini
docker-compose up -d --build
docker-compose exec ollama ollama pull llama3.1:8b
```

5. ***Configure security groups:***
Ensure your EC2 instance's security groups allow inbound traffic on port 5000.

6. ***Access the app***:
Open your browser and navigate to your EC2 instance’s public IP address on port 5000.

## License

This project is licensed under the MIT License.