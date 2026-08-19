#!/bin/bash
# Deployment script for CloudWatch Dashboard Lambda function

set -e

# Configuration
STACK_NAME="cloudwatch-dashboard-generator"
TEMPLATE_FILE="cloudwatch_dashboard_template.yaml"
LAMBDA_CODE_FILE="cloudwatch_dashboard_lambda.py"
OUTPUT_ZIP="cloudwatch_dashboard_lambda.zip"
S3_BUCKET=""  # Set your S3 bucket name here
S3_PREFIX="lambda/cloudwatch-dashboard/"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI is not installed. Please install it first.${NC}"
    exit 1
fi

# Check if jq is installed (for JSON parsing)
if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW}Warning: jq is not installed. Some features may not work.${NC}"
fi

# Function to display usage
usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --stack-name NAME     CloudFormation stack name (default: $STACK_NAME)"
    echo "  --template FILE      CloudFormation template file (default: $TEMPLATE_FILE)"
    echo "  --s3-bucket BUCKET   S3 bucket for Lambda code (required for deployment)"
    echo "  --s3-prefix PREFIX   S3 prefix for Lambda code (default: $S3_PREFIX)"
    echo "  --regions REGIONS    Comma-separated list of regions to monitor"
    echo "  --dashboard-name NAME Dashboard name (default: MultiRegion-Resource-Metrics)"
    echo "  --dashboard-prefix PREFIX Dashboard name prefix"
    echo "  --help               Show this help message"
    echo ""
    echo "Commands:"
    echo "  package              Package Lambda code and upload to S3"
    echo "  deploy               Create/update CloudFormation stack"
    echo "  delete               Delete CloudFormation stack"
    echo "  test                 Test Lambda function locally"
    echo "  full                 Package, deploy, and test"
}

# Parse command line arguments
COMMAND=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        --template)
            TEMPLATE_FILE="$2"
            shift 2
            ;;
        --s3-bucket)
            S3_BUCKET="$2"
            shift 2
            ;;
        --s3-prefix)
            S3_PREFIX="$2"
            shift 2
            ;;
        --regions)
            REGIONS="$2"
            shift 2
            ;;
        --dashboard-name)
            DASHBOARD_NAME="$2"
            shift 2
            ;;
        --dashboard-prefix)
            DASHBOARD_PREFIX="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        package|deploy|delete|test|full)
            COMMAND="$1"
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Validate S3 bucket for package and deploy commands
if [[ "$COMMAND" == "package" || "$COMMAND" == "deploy" || "$COMMAND" == "full" ]]; then
    if [[ -z "$S3_BUCKET" ]]; then
        echo -e "${RED}Error: S3 bucket is required for $COMMAND command.${NC}"
        echo "Use --s3-bucket to specify your S3 bucket name."
        exit 1
    fi
fi

# Package Lambda code
package_lambda() {
    echo -e "${GREEN}Packaging Lambda function...${NC}"
    
    # Create zip file
    if [[ -f "$LAMBDA_CODE_FILE" ]]; then
        zip "$OUTPUT_ZIP" "$LAMBDA_CODE_FILE"
        echo -e "${GREEN}Created $OUTPUT_ZIP${NC}"
    else
        echo -e "${RED}Error: Lambda code file $LAMBDA_CODE_FILE not found.${NC}"
        exit 1
    fi
    
    # Upload to S3
    echo -e "${GREEN}Uploading to S3...${NC}"
    aws s3 cp "$OUTPUT_ZIP" "s3://$S3_BUCKET/$S3_PREFIX$OUTPUT_ZIP"
    echo -e "${GREEN}Uploaded to s3://$S3_BUCKET/$S3_PREFIX$OUTPUT_ZIP${NC}"
}

# Deploy CloudFormation stack
deploy_stack() {
    echo -e "${GREEN}Deploying CloudFormation stack...${NC}"
    
    # Build parameter list
    PARAMETERS=()
    PARAMETERS+=("ParameterKey=S3BucketName,ParameterValue=$S3_BUCKET")
    PARAMETERS+=("ParameterKey=S3KeyPrefix,ParameterValue=$S3_PREFIX")
    
    if [[ -n "$REGIONS" ]]; then
        PARAMETERS+=("ParameterKey=RegionsToMonitor,ParameterValue=$REGIONS")
    fi
    
    if [[ -n "$DASHBOARD_NAME" ]]; then
        PARAMETERS+=("ParameterKey=DashboardName,ParameterValue=$DASHBOARD_NAME")
    fi
    
    if [[ -n "$DASHBOARD_PREFIX" ]]; then
        PARAMETERS+=("ParameterKey=DashboardPrefix,ParameterValue=$DASHBOARD_PREFIX")
    fi
    
    # Check if stack exists
    if aws cloudformation describe-stacks --stack-name "$STACK_NAME" &> /dev/null; then
        echo -e "${YELLOW}Stack $STACK_NAME already exists. Updating...${NC}"
        aws cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body file://"$TEMPLATE_FILE" \
            --parameters "${PARAMETERS[*]}" \
            --capabilities CAPABILITY_IAM
        
        # Wait for update to complete
        aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME"
        echo -e "${GREEN}Stack $STACK_NAME updated successfully.${NC}"
    else
        echo -e "${GREEN}Creating new stack $STACK_NAME...${NC}"
        aws cloudformation create-stack \
            --stack-name "$STACK_NAME" \
            --template-body file://"$TEMPLATE_FILE" \
            --parameters "${PARAMETERS[*]}" \
            --capabilities CAPABILITY_IAM
        
        # Wait for creation to complete
        aws cloudformation wait stack-create-complete --stack-name "$STACK_NAME"
        echo -e "${GREEN}Stack $STACK_NAME created successfully.${NC}"
    fi
    
    # Get stack outputs
    echo -e "${GREEN}Stack Outputs:${NC}"
    aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs" --output table
}

# Delete CloudFormation stack
delete_stack() {
    echo -e "${YELLOW}Deleting CloudFormation stack $STACK_NAME...${NC}"
    aws cloudformation delete-stack --stack-name "$STACK_NAME"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
    echo -e "${GREEN}Stack $STACK_NAME deleted successfully.${NC}"
}

# Test Lambda function
test_lambda() {
    echo -e "${GREEN}Testing Lambda function...${NC}"
    
    # Get Lambda function name
    LAMBDA_ARN=$(aws cloudformation describe-stack-resources \
        --stack-name "$STACK_NAME" \
        --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" \
        --output text)
    
    if [[ -z "$LAMBDA_ARN" ]]; then
        echo -e "${RED}Error: Could not find Lambda function ARN. Is the stack deployed?${NC}"
        exit 1
    fi
    
    echo "Invoking Lambda function: $LAMBDA_ARN"
    aws lambda invoke \
        --function-name "$LAMBDA_ARN" \
        --payload '{}' \
        response.json
    
    echo -e "${GREEN}Response:${NC}"
    cat response.json | jq . || cat response.json
    rm -f response.json
}

# Main execution
case "$COMMAND" in
    package)
        package_lambda
        ;;
    deploy)
        deploy_stack
        ;;
    delete)
        delete_stack
        ;;
    test)
        test_lambda
        ;;
    full)
        package_lambda
        deploy_stack
        test_lambda
        ;;
    "")
        usage
        exit 1
        ;;
esac

echo -e "${GREEN}Done!${NC}"
