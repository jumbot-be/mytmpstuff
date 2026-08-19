# AWS CloudWatch Dashboard Generator Lambda

This project contains an AWS Lambda function that dynamically generates a CloudWatch dashboard with metrics for RDS, EFS, VPN, and IPSEC resources across multiple AWS regions.

## Features

- **Dynamic Resource Discovery**: Automatically discovers resources in configured regions
- **Multi-Region Support**: Monitors resources across all specified AWS regions
- **Comprehensive Metrics**: Includes all requested metrics:
  - **RDS Databases**:
    - Free Storage Available (time series, average)
    - Connections (time series, average)
    - CPU Utilization (time series, average)
    - Free Memory (time series, average)
    - FreeStorageSpace (bar chart, average)
  - **EFS**:
    - StorageBytes (time series, average)
    - StorageSpace (bar chart, average)
  - **VPN**:
    - Tunnel State (time series, minimum)
  - **IPSEC**:
    - State (time series, minimum)
    - TunnelDataIn (time series, minimum)

## Project Structure

```
.
├── cloudwatch_dashboard_lambda.py   # Lambda function code
├── cloudwatch_dashboard_template.yaml # CloudFormation template
├── deploy_dashboard.sh             # Deployment script
├── config.json                     # Configuration file
└── README_DASHBOARD.md             # This file
```

## Prerequisites

- AWS CLI installed and configured with appropriate permissions
- Python 3.9+ (for local testing)
- boto3 library (`pip install boto3`)
- jq (for JSON parsing in deployment script)
- zip utility

## Deployment

### Method 1: Using the Deployment Script

1. **Edit the deployment script** to set your S3 bucket:
   ```bash
   nano deploy_dashboard.sh
   # Set S3_BUCKET="your-bucket-name"
   ```

2. **Make the script executable**:
   ```bash
   chmod +x deploy_dashboard.sh
   ```

3. **Full deployment** (package, deploy, test):
   ```bash
   ./deploy_dashboard.sh full --s3-bucket your-bucket-name
   ```

4. **Or step by step**:
   ```bash
   # Package Lambda code and upload to S3
   ./deploy_dashboard.sh package --s3-bucket your-bucket-name
   
   # Deploy CloudFormation stack
   ./deploy_dashboard.sh deploy --s3-bucket your-bucket-name --regions "us-east-1,eu-west-1"
   
   # Test the Lambda function
   ./deploy_dashboard.sh test
   ```

### Method 2: Manual Deployment with AWS CLI

1. **Package the Lambda code**:
   ```bash
   zip cloudwatch_dashboard_lambda.zip cloudwatch_dashboard_lambda.py
   ```

2. **Upload to S3**:
   ```bash
   aws s3 cp cloudwatch_dashboard_lambda.zip s3://your-bucket-name/lambda/cloudwatch-dashboard/
   ```

3. **Deploy CloudFormation stack**:
   ```bash
   aws cloudformation deploy \
     --template-file cloudwatch_dashboard_template.yaml \
     --stack-name cloudwatch-dashboard-generator \
     --parameter-overrides \
       S3BucketName=your-bucket-name \
       S3KeyPrefix=lambda/cloudwatch-dashboard/ \
       RegionsToMonitor="us-east-1,eu-west-1" \
       DashboardName=MyResourceDashboard \
       DashboardPrefix=MyCompany \
     --capabilities CAPABILITY_IAM
   ```

### Method 3: Using AWS Console

1. Upload the Lambda code to S3
2. Go to CloudFormation console
3. Create new stack with the template file
4. Provide required parameters

## Configuration

### Environment Variables

The Lambda function uses the following environment variables:

- `REGIONS`: Comma-separated list of AWS regions to monitor (e.g., "us-east-1,eu-west-1")
- `DASHBOARD_NAME`: Base name for the CloudWatch dashboard (default: "MultiRegion-Resource-Metrics")
- `DASHBOARD_PREFIX`: Optional prefix for the dashboard name

### CloudFormation Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| DashboardName | MultiRegion-Resource-Metrics | Base name for the dashboard |
| DashboardPrefix | (empty) | Optional prefix for dashboard name |
| RegionsToMonitor | (empty - all regions) | Comma-separated list of regions |
| LambdaRuntime | python3.12 | Python runtime version |
| LambdaMemorySize | 512 | Memory size in MB |
| LambdaTimeout | 300 | Timeout in seconds |
| ScheduleExpression | rate(1 hour) | How often to update the dashboard |
| S3BucketName | (required) | S3 bucket for Lambda code |
| S3KeyPrefix | lambda/cloudwatch-dashboard/ | S3 key prefix |

## IAM Permissions

The Lambda function requires the following IAM permissions:

- **CloudWatch**: PutDashboard, GetDashboard, ListDashboards, DeleteDashboard
- **RDS**: DescribeDBInstances, ListTagsForResource
- **EFS**: DescribeFileSystems, ListTagsForResource, DescribeTags
- **EC2**: DescribeVpnConnections, DescribeCustomerGateways, DescribeRegions, DescribeVpnGateways
- **STS**: GetCallerIdentity
- **Logs**: CreateLogGroup, CreateLogStream, PutLogEvents

These permissions are automatically created by the CloudFormation template.

## Testing

### Local Testing

1. Set up AWS credentials in your environment
2. Run the Lambda function locally:
   ```bash
   python3 cloudwatch_dashboard_lambda.py
   ```

### Lambda Testing

Use the deployment script to test the deployed Lambda:
```bash
./deploy_dashboard.sh test
```

Or manually:
```bash
# Get Lambda function ARN
aws cloudformation describe-stack-resources \
  --stack-name cloudwatch-dashboard-generator \
  --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" \
  --output text

# Invoke Lambda
aws lambda invoke \
  --function-name YOUR_LAMBDA_ARN \
  --payload '{}' \
  response.json
```

## Customization

### Adding More Metrics

Edit `cloudwatch_dashboard_lambda.py` and add new metric definitions in the appropriate widget creation functions:

- `create_rds_widgets()` for RDS metrics
- `create_efs_widgets()` for EFS metrics
- `create_vpn_widgets()` for VPN metrics
- `create_ipsec_widgets()` for IPSEC metrics

### Changing Dashboard Layout

Modify the widget properties in the code to change:
- Widget width and height
- Position (x, y coordinates)
- Chart types (timeSeries, bar, etc.)
- Colors and styling

### Filtering Resources

Add filtering logic in the resource discovery functions to include/exclude specific resources based on tags or other criteria.

## Cleanup

To remove all resources created by this project:

```bash
# Delete CloudFormation stack
./deploy_dashboard.sh delete

# Or manually
aws cloudformation delete-stack --stack-name cloudwatch-dashboard-generator
```

This will remove:
- Lambda function
- CloudWatch Events rule
- IAM role
- CloudWatch dashboard (the dashboard itself needs to be deleted manually)

## Troubleshooting

### Common Issues

1. **Missing IAM Permissions**: Ensure the Lambda execution role has all required permissions.
2. **Region Access**: The Lambda function needs to be able to access resources in all specified regions.
3. **S3 Bucket Access**: The Lambda function needs read access to the S3 bucket containing its code.
4. **Timeout Errors**: Increase the Lambda timeout if processing many regions/resources.

### Debugging

Check CloudWatch Logs for the Lambda function:
```bash
# Get Lambda function name
aws cloudformation describe-stack-resources \
  --stack-name cloudwatch-dashboard-generator \
  --query "StackResources[?ResourceType=='AWS::Lambda::Function'].LogicalResourceId" \
  --output text

# View logs
aws logs tail /aws/lambda/YOUR_LAMBDA_NAME --follow
```

## License

This project is provided as-is. Feel free to use and modify it according to your needs.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues or questions, please open an issue in the repository.
