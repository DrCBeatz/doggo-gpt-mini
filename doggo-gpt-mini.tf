provider "aws" {
  region = "us-east-2"  # Replace with your desired region
}

# Data source to get hosted zone ID
data "aws_route53_zone" "doggo_gpt_zone" {
  name = "doggo-gpt-mini-api.com."
}

# Create a VPC
resource "aws_vpc" "doggo_gpt_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
}

# Create subnets in different Availability Zones
resource "aws_subnet" "doggo_gpt_subnet" {
  vpc_id            = aws_vpc.doggo_gpt_vpc.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "us-east-2a"
  map_public_ip_on_launch = true
}

resource "aws_subnet" "doggo_gpt_subnet_b" {
  vpc_id            = aws_vpc.doggo_gpt_vpc.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "us-east-2b"
  map_public_ip_on_launch = true
}

# Create an Internet Gateway
resource "aws_internet_gateway" "doggo_gpt_igw" {
  vpc_id = aws_vpc.doggo_gpt_vpc.id
}

# Create a Route Table and Associate with Subnets
resource "aws_route_table" "doggo_gpt_route_table" {
  vpc_id = aws_vpc.doggo_gpt_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.doggo_gpt_igw.id
  }
}

resource "aws_route_table_association" "doggo_gpt_route_table_association" {
  subnet_id      = aws_subnet.doggo_gpt_subnet.id
  route_table_id = aws_route_table.doggo_gpt_route_table.id
}

resource "aws_route_table_association" "doggo_gpt_route_table_association_b" {
  subnet_id      = aws_subnet.doggo_gpt_subnet_b.id
  route_table_id = aws_route_table.doggo_gpt_route_table.id
}

# Security Group
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

# EC2 Instance
resource "aws_instance" "doggo_gpt_instance" {
  ami                  = "ami-067df2907035c28c2"
  instance_type        = "c6g.2xlarge"
  subnet_id            = aws_subnet.doggo_gpt_subnet.id
  vpc_security_group_ids = [aws_security_group.doggo_gpt_sg.id]

  associate_public_ip_address = true

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


  root_block_device {
    volume_size = 10
  }

  tags = {
    Name = "doggo-gpt-backend"
  }
}

resource "aws_route53_record" "doggo_gpt_api" {
  zone_id = data.aws_route53_zone.doggo_gpt_zone.zone_id
  name    = "doggo-gpt-mini-api.com"
  type    = "A"

  alias {
    name                   = aws_lb.doggo_gpt_alb.dns_name
    zone_id                = aws_lb.doggo_gpt_alb.zone_id
    evaluate_target_health = true
  }
}


# ACM Certificate
resource "aws_acm_certificate" "doggo_gpt_ssl_cert" {
  domain_name       = "doggo-gpt-mini-api.com"
  validation_method = "DNS"

  tags = {
    Name = "doggo-gpt-api-cert"
  }
}

# ALB
resource "aws_lb" "doggo_gpt_alb" {
  name               = "doggo-gpt-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.doggo_gpt_sg.id]
  subnets            = [aws_subnet.doggo_gpt_subnet.id, aws_subnet.doggo_gpt_subnet_b.id]

  enable_deletion_protection = false
}

# Target Group
resource "aws_lb_target_group" "doggo_gpt_tg" {
  name     = "doggo-gpt-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.doggo_gpt_vpc.id

  health_check {
    path                = "/health"
    protocol            = "HTTP"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

# Attach the EC2 instance to the target group
resource "aws_lb_target_group_attachment" "doggo_gpt_tg_attachment" {
  target_group_arn = aws_lb_target_group.doggo_gpt_tg.arn
  target_id        = aws_instance.doggo_gpt_instance.id
  port             = 80
}

# Listener
resource "aws_lb_listener" "doggo_gpt_listener" {
  load_balancer_arn = aws_lb.doggo_gpt_alb.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-2016-08"
  certificate_arn   = aws_acm_certificate.doggo_gpt_ssl_cert.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.doggo_gpt_tg.arn
  }
}

# Listener for HTTP to HTTPS redirection
resource "aws_lb_listener" "http_redirect_listener" {
  load_balancer_arn = aws_lb.doggo_gpt_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}


# Outputs
output "alb_dns_name" {
  value = aws_lb.doggo_gpt_alb.dns_name
}

output "instance_public_ip" {
  value = aws_instance.doggo_gpt_instance.public_ip
}
