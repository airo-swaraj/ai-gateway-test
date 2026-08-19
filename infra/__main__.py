import os
import json

import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx

config = pulumi.Config()
container_cpu = int(config.get("container_cpu") or "256")
container_memory = int(config.get("container_memory") or "512")
desired_count = int(config.get("desired_count") or "1")
instance_type = config.get("instance_type") or "t3.micro"

app_name = "ai-gateway"
docker_image = os.environ.get("DOCKER_IMAGE", f"{app_name}:latest")

caller = aws.get_caller_identity()
region = aws.get_region()
ecr_repo_url = f"{caller.account_id}.dkr.ecr.{region.name}.amazonaws.com/{app_name}"

# --- VPC ---
vpc = awsx.ec2.Vpc(
    f"{app_name}-vpc",
    awsx.ec2.VpcArgs(
        number_of_availability_zones=2,
        nat_gateways=awsx.ec2.NatGatewayConfigurationArgs(strategy=awsx.ec2.NatGatewayStrategy.SINGLE),
    ),
)

# --- Security Group ---
ecs_sg = aws.ec2.SecurityGroup(
    f"{app_name}-ecs-sg",
    vpc_id=vpc.vpc_id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=8000,
            to_port=8000,
            cidr_blocks=["0.0.0.0/0"],
        ),
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
        ),
    ],
)

# --- IAM Roles ---
ecs_instance_role = aws.iam.Role(
    f"{app_name}-ecs-instance-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
        }],
    }),
)

aws.iam.RolePolicyAttachment(
    f"{app_name}-ecs-instance-policy",
    role=ecs_instance_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
)

instance_profile = aws.iam.InstanceProfile(
    f"{app_name}-instance-profile",
    role=ecs_instance_role.name,
)

task_exec_role = aws.iam.Role(
    f"{app_name}-task-exec-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {"Service": "ecs.amazonaws.com"},
        }],
    }),
)

aws.iam.RolePolicyAttachment(
    f"{app_name}-task-exec-policy",
    role=task_exec_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# --- ECS Cluster ---
cluster = aws.ecs.Cluster(f"{app_name}-cluster")

# --- EC2 Launch Template + ASG for ECS ---
ecs_ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name", values=["amzn2-ami-ecs-hvm-*-x86_64-ebs"]),
    ],
)

user_data_script = cluster.name.apply(
    lambda name: __import__("base64").b64encode(
        f"#!/bin/bash\necho ECS_CLUSTER={name} >> /etc/ecs/ecs.config\n".encode()
    ).decode()
)

launch_template = aws.ec2.LaunchTemplate(
    f"{app_name}-lt",
    image_id=ecs_ami.id,
    instance_type=instance_type,
    user_data=user_data_script,
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        arn=instance_profile.arn,
    ),
    vpc_security_group_ids=[ecs_sg.id],
)

asg = aws.autoscaling.Group(
    f"{app_name}-asg",
    desired_capacity=desired_count,
    max_size=desired_count + 1,
    min_size=1,
    vpc_zone_identifiers=vpc.private_subnet_ids,
    launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
        id=launch_template.id,
        version="$Latest",
    ),
    tags=[
        aws.autoscaling.GroupTagArgs(
            key="AmazonECSManaged",
            value="true",
            propagate_at_launch=True,
        ),
    ],
)

capacity_provider = aws.ecs.CapacityProvider(
    f"{app_name}-cp",
    auto_scaling_group_provider=aws.ecs.CapacityProviderAutoScalingGroupProviderArgs(
        auto_scaling_group_arn=asg.arn,
        managed_scaling=aws.ecs.CapacityProviderAutoScalingGroupProviderManagedScalingArgs(
            status="ENABLED",
            target_capacity=100,
        ),
    ),
)

aws.ecs.ClusterCapacityProviders(
    f"{app_name}-cluster-cp",
    cluster_name=cluster.name,
    capacity_providers=[capacity_provider.name],
    default_capacity_provider_strategies=[
        aws.ecs.ClusterCapacityProvidersDefaultCapacityProviderStrategyArgs(
            capacity_provider=capacity_provider.name,
            weight=1,
        ),
    ],
)

# --- CloudWatch Log Group ---
log_group = aws.cloudwatch.LogGroup(
    f"{app_name}-logs",
    retention_in_days=7,
)

# --- ECS Task Definition ---
task_definition = aws.ecs.TaskDefinition(
    f"{app_name}-task",
    family=app_name,
    network_mode="bridge",
    requires_compatibilities=["EC2"],
    execution_role_arn=task_exec_role.arn,
    cpu=str(container_cpu),
    memory=str(container_memory),
    container_definitions=pulumi.Output.all(log_group.name).apply(
        lambda args: json.dumps([{
            "name": app_name,
            "image": docker_image,
            "cpu": container_cpu,
            "memory": container_memory,
            "essential": True,
            "portMappings": [{"containerPort": 8000, "hostPort": 0, "protocol": "tcp"}],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": args[0],
                    "awslogs-region": "ap-south-1",
                    "awslogs-stream-prefix": app_name,
                },
            },
        }])
    ),
)

# --- ECS Service ---
service = aws.ecs.Service(
    f"{app_name}-svc",
    cluster=cluster.arn,
    task_definition=task_definition.arn,
    desired_count=desired_count,
    capacity_provider_strategies=[
        aws.ecs.ServiceCapacityProviderStrategyArgs(
            capacity_provider=capacity_provider.name,
            weight=1,
        ),
    ],
    opts=pulumi.ResourceOptions(depends_on=[asg]),
)

# --- Exports ---
pulumi.export("cluster_name", cluster.name)
pulumi.export("service_name", service.name)
pulumi.export("ecr_repo_url", ecr_repo_url)
pulumi.export("vpc_id", vpc.vpc_id)
