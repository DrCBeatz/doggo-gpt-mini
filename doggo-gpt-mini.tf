# doggo-gpt-mini.tf

# Define the provider
provider "aws" {
  region = "us-east-2"  # Replace with your desired region
}

# Create a VPC
resource "aws_vpc" "doggo_gpt_vpc" {
  cidr_block = "10.0.0.0/16"
}

# Create a subnet
resource "aws_subnet" "doggo_gpt_subnet" {
  vpc_id     = aws_vpc.doggo_gpt_vpc.id
  cidr_block = "10.0.1.0/24"
  map_public_ip_on_launch = true
}

# Create an internet gateway
resource "aws_internet_gateway" "doggo_gpt_igw" {
  vpc_id = aws_vpc.doggo_gpt_vpc.id
}

# Create a route table
resource "aws_route_table" "doggo_gpt_route_table" {
  vpc_id = aws_vpc.doggo_gpt_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.doggo_gpt_igw.id
  }
}

# Associate the route table with the subnet
resource "aws_route_table_association" "doggo_gpt_route_table_association" {
  subnet_id      = aws_subnet.doggo_gpt_subnet.id
  route_table_id = aws_route_table.doggo_gpt_route_table.id
}

# Create a security group to allow SSH and HTTP/HTTPS traffic
resource "aws_security_group" "doggo_gpt_sg" {
  vpc_id = aws_vpc.doggo_gpt_vpc.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Create an EC2 instance
resource "aws_instance" "doggo_gpt_instance" {
  ami           = "ami-067df2907035c28c2"  # Replace with the Amazon Linux 2023 AMI ID for your region
  instance_type = "c6g.2xlarge"            # The ARM-based Graviton instance type
  subnet_id     = aws_subnet.doggo_gpt_subnet.id
  vpc_security_group_ids = [aws_security_group.doggo_gpt_sg.id]

  associate_public_ip_address = true

  # Attach the user data script
  user_data = <<-EOF
    #!/bin/bash
    # Update the system
    sudo yum update -y

    # Install Docker
    sudo yum -y install docker

    # Start Docker service
    sudo service docker start

    # Enable Docker service to start on boot
    sudo systemctl enable docker

    # Add ec2-user to the docker group
    sudo usermod -a -G docker ec2-user

    # Give ec2-user permission to access Docker socket
    sudo chmod 666 /var/run/docker.sock

    # Install Git
    sudo yum install git -y

    # Check Git version
    git --version

    # Install Docker Compose
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose

    # Pull repository from GitHub
    git clone https://github.com/DrCBeatz/doggo-gpt-mini.git
    cd doggo-gpt-mini

    # Pull the latest Docker image and start the app
    docker-compose up -d --build

    # Pull the Llama 3.1 8b model for the application using Ollama
    docker-compose exec ollama ollama pull llama3.1:8b
  EOF

  # Attach a 10 GB EBS volume
  root_block_device {
    volume_size = 10
  }

  tags = {
    Name = "doggo-gpt-backend"
  }
}

# Output the instance public IP
output "instance_public_ip" {
  value = aws_instance.doggo_gpt_instance.public_ip
}
