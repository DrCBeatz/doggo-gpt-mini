# main.tf

provider "aws" {
  region = var.aws_region
}

# Data source to get hosted zone ID
data "aws_route53_zone" "doggo_gpt_zone" {
  name = "${var.domain_name}."
}

# Create a VPC
resource "aws_vpc" "doggo_gpt_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
}

# Create subnets in different Availability Zones
resource "aws_subnet" "doggo_gpt_subnet" {
  vpc_id                   = aws_vpc.doggo_gpt_vpc.id
  cidr_block               = "10.0.1.0/24"
  availability_zone        = var.availability_zones[0]
  map_public_ip_on_launch  = true
}

resource "aws_subnet" "doggo_gpt_subnet_b" {
  vpc_id                   = aws_vpc.doggo_gpt_vpc.id
  cidr_block               = "10.0.2.0/24"
  availability_zone        = var.availability_zones[1]
  map_public_ip_on_launch  = true
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

# Create a launch template
resource "aws_launch_template" "doggo_gpt_template" {
  name_prefix   = "doggo-gpt-template"
  image_id      = var.ami_id
  instance_type = var.instance_type

  user_data = base64encode(<<-EOF
    #!/bin/bash
    sudo yum update -y
    sudo yum -y install docker
    sudo service docker start
    sudo systemctl enable docker
    sudo usermod -a -G docker ec2-user
    sudo chmod 666 /var/run/docker.sock
    sudo yum install git -y
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
    git clone https://github.com/DrCBeatz/doggo-gpt-mini.git
    cd doggo-gpt-mini
    docker-compose up -d --build
    docker-compose exec ollama ollama pull llama3.1:8b
  EOF
  )

  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.doggo_gpt_sg.id]
    subnet_id                   = aws_subnet.doggo_gpt_subnet.id
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 10
    }
  }

  tags = {
    Name = "doggo-gpt-backend"
  }
}

# Create an autoscaling group
resource "aws_autoscaling_group" "doggo_gpt_asg" {
  desired_capacity     = 1
  max_size             = 1
  min_size             = 1
  launch_template {
    id      = aws_launch_template.doggo_gpt_template.id
    version = "$Latest"
  }

  vpc_zone_identifier = [aws_subnet.doggo_gpt_subnet.id, aws_subnet.doggo_gpt_subnet_b.id]
  
  target_group_arns = [aws_lb_target_group.doggo_gpt_tg.arn]  # Attach the ASG to the target group

  health_check_type = "EC2"
  health_check_grace_period = 300

}

resource "aws_route53_record" "doggo_gpt_api" {
  zone_id = data.aws_route53_zone.doggo_gpt_zone.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.doggo_gpt_alb.dns_name
    zone_id                = aws_lb.doggo_gpt_alb.zone_id
    evaluate_target_health = true
  }
}


# ACM Certificate
resource "aws_acm_certificate" "doggo_gpt_ssl_cert" {
  domain_name       = var.domain_name
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
