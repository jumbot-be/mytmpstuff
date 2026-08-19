#!/usr/bin/env python3
"""
AWS Lambda function to generate a dynamic CloudWatch Dashboard
with metrics for RDS, EFS, VPN, and IPSEC resources across configured regions.

Environment Variables:
- REGIONS: Comma-separated list of AWS regions to monitor (default: all regions)
- DASHBOARD_NAME: Name of the CloudWatch dashboard (default: MultiRegion-Resource-Metrics)
- DASHBOARD_PREFIX: Prefix for dashboard name when including account info
"""

import os
import json
import boto3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional


# Configuration from environment variables
REGIONS = os.environ.get('REGIONS', '').split(',') if os.environ.get('REGIONS') else []
DASHBOARD_NAME = os.environ.get('DASHBOARD_NAME', 'MultiRegion-Resource-Metrics')
DASHBOARD_PREFIX = os.environ.get('DASHBOARD_PREFIX', '')

# AWS clients (will be initialized per region)
cloudwatch_client = None
rds_client = None
elasticfilesystem_client = None
ec2_client = None


def get_clients(region: str):
    """Initialize AWS clients for a specific region."""
    global cloudwatch_client, rds_client, elasticfilesystem_client, ec2_client
    
    cloudwatch_client = boto3.client('cloudwatch', region_name=region)
    rds_client = boto3.client('rds', region_name=region)
    elasticfilesystem_client = boto3.client('efs', region_name=region)
    ec2_client = boto3.client('ec2', region_name=region)


def get_all_regions() -> List[str]:
    """Get all available AWS regions if none specified."""
    ec2 = boto3.client('ec2', region_name='us-east-1')
    try:
        response = ec2.describe_regions()
        return [region['RegionName'] for region in response['Regions']]
    except Exception as e:
        print(f"Error getting regions: {e}")
        return ['us-east-1', 'us-west-1', 'us-west-2', 'eu-west-1', 'eu-central-1']


def get_rds_instances(region: str) -> List[Dict[str, Any]]:
    """Get all RDS instances in a region."""
    try:
        get_clients(region)
        response = rds_client.describe_db_instances()
        return response['DBInstances']
    except Exception as e:
        print(f"Error getting RDS instances in {region}: {e}")
        return []


def get_efs_filesystems(region: str) -> List[Dict[str, Any]]:
    """Get all EFS filesystems in a region."""
    try:
        get_clients(region)
        response = elasticfilesystem_client.describe_file_systems()
        return response['FileSystems']
    except Exception as e:
        print(f"Error getting EFS filesystems in {region}: {e}")
        return []


def get_vpn_connections(region: str) -> List[Dict[str, Any]]:
    """Get all VPN connections in a region."""
    try:
        get_clients(region)
        response = ec2_client.describe_vpn_connections()
        return response.get('VpnConnections', [])
    except Exception as e:
        print(f"Error getting VPN connections in {region}: {e}")
        return []


def get_customer_gateways(region: str) -> List[Dict[str, Any]]:
    """Get all customer gateways (for IPSEC) in a region."""
    try:
        get_clients(region)
        response = ec2_client.describe_customer_gateways()
        return response.get('CustomerGateways', [])
    except Exception as e:
        print(f"Error getting customer gateways in {region}: {e}")
        return []


def create_rds_widgets(region: str, db_instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create CloudWatch widgets for RDS metrics."""
    widgets = []
    
    if not db_instances:
        return widgets
    
    # Time series widgets
    time_series_metrics = [
        {
            'metric_name': 'FreeStorageSpace',
            'label': 'Free Storage Available',
            'statistic': 'Average',
            'namespace': 'AWS/RDS'
        },
        {
            'metric_name': 'DatabaseConnections',
            'label': 'Connections',
            'statistic': 'Average',
            'namespace': 'AWS/RDS'
        },
        {
            'metric_name': 'CPUUtilization',
            'label': 'CPU Utilization',
            'statistic': 'Average',
            'namespace': 'AWS/RDS'
        },
        {
            'metric_name': 'FreeableMemory',
            'label': 'Free Memory',
            'statistic': 'Average',
            'namespace': 'AWS/RDS'
        }
    ]
    
    # Create time series for each metric
    for metric in time_series_metrics:
        expressions = []
        for db in db_instances:
            db_id = db['DBInstanceIdentifier']
            expressions.append({
                'Id': f'{db_id}_{metric["metric_name"]}',
                'MetricStat': {
                    'Metric': {
                        'Namespace': metric['namespace'],
                        'MetricName': metric['metric_name'],
                        'Dimensions': [
                            {'Name': 'DBInstanceIdentifier', 'Value': db_id}
                        ]
                    },
                    'Period': 300,
                    'Stat': metric['statistic']
                },
                'Label': f'{db_id} - {metric["label"]}',
                'ReturnData': True
            })
        
        if expressions:
            widgets.append({
                'type': 'timeSeries',
                'width': 24,
                'height': 8,
                'x': 0,
                'y': 0,
                'properties': {
                    'metrics': expressions,
                    'view': 'timeSeries',
                    'stacked': False,
                    'region': region,
                    'title': f'RDS - {metric["label"]} - {region}',
                    'period': 300,
                    'stat': metric['statistic'],
                    'legend': {
                        'position': 'bottom'
                    }
                }
            })
    
    # Bar chart for FreeStorageSpace
    bar_expressions = []
    for db in db_instances:
        db_id = db['DBInstanceIdentifier']
        bar_expressions.append({
            'Id': f'{db_id}_FreeStorageSpace_bar',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/RDS',
                    'MetricName': 'FreeStorageSpace',
                    'Dimensions': [
                        {'Name': 'DBInstanceIdentifier', 'Value': db_id}
                    ]
                },
                'Period': 300,
                'Stat': 'Average'
            },
            'Label': db_id,
            'ReturnData': True
        })
    
    if bar_expressions:
        widgets.append({
            'type': 'metric',
            'width': 24,
            'height': 8,
            'x': 0,
            'y': 0,
            'properties': {
                'metrics': bar_expressions,
                'view': 'bar',
                'stacked': False,
                'region': region,
                'title': f'RDS - FreeStorageSpace - {region}',
                'period': 300,
                'stat': 'Average',
                'legend': {
                    'position': 'bottom'
                }
            }
        })
    
    return widgets


def create_efs_widgets(region: str, filesystems: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create CloudWatch widgets for EFS metrics."""
    widgets = []
    
    if not filesystems:
        return widgets
    
    # Time series for StorageBytes
    time_series_expressions = []
    for fs in filesystems:
        fs_id = fs['FileSystemId']
        time_series_expressions.append({
            'Id': f'{fs_id}_StorageBytes',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/EFS',
                    'MetricName': 'StorageBytes',
                    'Dimensions': [
                        {'Name': 'FileSystemId', 'Value': fs_id}
                    ]
                },
                'Period': 300,
                'Stat': 'Average'
            },
            'Label': f'EFS-{fs_id} - StorageBytes',
            'ReturnData': True
        })
    
    if time_series_expressions:
        widgets.append({
            'type': 'timeSeries',
            'width': 24,
            'height': 8,
            'x': 0,
            'y': 0,
            'properties': {
                'metrics': time_series_expressions,
                'view': 'timeSeries',
                'stacked': False,
                'region': region,
                'title': f'EFS - StorageBytes - {region}',
                'period': 300,
                'stat': 'Average',
                'legend': {
                    'position': 'bottom'
                }
            }
        })
    
    # Bar chart for StorageSpace
    bar_expressions = []
    for fs in filesystems:
        fs_id = fs['FileSystemId']
        bar_expressions.append({
            'Id': f'{fs_id}_StorageSpace_bar',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/EFS',
                    'MetricName': 'StorageBytes',
                    'Dimensions': [
                        {'Name': 'FileSystemId', 'Value': fs_id}
                    ]
                },
                'Period': 300,
                'Stat': 'Average'
            },
            'Label': fs_id,
            'ReturnData': True
        })
    
    if bar_expressions:
        widgets.append({
            'type': 'metric',
            'width': 24,
            'height': 8,
            'x': 0,
            'y': 0,
            'properties': {
                'metrics': bar_expressions,
                'view': 'bar',
                'stacked': False,
                'region': region,
                'title': f'EFS - StorageSpace - {region}',
                'period': 300,
                'stat': 'Average',
                'legend': {
                    'position': 'bottom'
                }
            }
        })
    
    return widgets


def create_vpn_widgets(region: str, vpn_connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create CloudWatch widgets for VPN metrics."""
    widgets = []
    
    if not vpn_connections:
        return widgets
    
    # Time series for Tunnel State
    tunnel_state_expressions = []
    for vpn in vpn_connections:
        vpn_id = vpn['VpnConnectionId']
        # Get tunnel information
        tunnels = vpn.get('VgwTelemetry', [])
        for tunnel in tunnels:
            tunnel_state_expressions.append({
                'Id': f'{vpn_id}_{tunnel["AcceptRouteCount"]}_TunnelState',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'AWS/VPN',
                        'MetricName': 'TunnelState',
                        'Dimensions': [
                            {'Name': 'VpnId', 'Value': vpn_id},
                            {'Name': 'TunnelIpAddress', 'Value': tunnel.get('OutsideIpAddress', 'Unknown')}
                        ]
                    },
                    'Period': 300,
                    'Stat': 'Minimum'
                },
                'Label': f'VPN-{vpn_id} - TunnelState',
                'ReturnData': True
            })
    
    if tunnel_state_expressions:
        widgets.append({
            'type': 'timeSeries',
            'width': 24,
            'height': 8,
            'x': 0,
            'y': 0,
            'properties': {
                'metrics': tunnel_state_expressions,
                'view': 'timeSeries',
                'stacked': False,
                'region': region,
                'title': f'VPN - Tunnel State - {region}',
                'period': 300,
                'stat': 'Minimum',
                'legend': {
                    'position': 'bottom'
                }
            }
        })
    
    return widgets


def create_ipsec_widgets(region: str, customer_gateways: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create CloudWatch widgets for IPSEC metrics."""
    widgets = []
    
    if not customer_gateways:
        return widgets
    
    # Time series for IPSEC State
    ipsec_state_expressions = []
    for cgw in customer_gateways:
        cgw_id = cgw['CustomerGatewayId']
        ipsec_state_expressions.append({
            'Id': f'{cgw_id}_IPSEC_State',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/VPN',
                    'MetricName': 'TunnelState',
                    'Dimensions': [
                        {'Name': 'CustomerGatewayId', 'Value': cgw_id}
                    ]
                },
                'Period': 300,
                'Stat': 'Minimum'
            },
            'Label': f'IPSEC-{cgw_id} - State',
            'ReturnData': True
        })
    
    if ipsec_state_expressions:
        widgets.append({
            'type': 'timeSeries',
            'width': 24,
            'height': 8,
            'x': 0,
            'y': 0,
            'properties': {
                'metrics': ipsec_state_expressions,
                'view': 'timeSeries',
                'stacked': False,
                'region': region,
                'title': f'IPSEC - State - {region}',
                'period': 300,
                'stat': 'Minimum',
                'legend': {
                    'position': 'bottom'
                }
            }
        })
    
    # Time series for TunnelDataIn
    tunnel_data_expressions = []
    for cgw in customer_gateways:
        cgw_id = cgw['CustomerGatewayId']
        tunnel_data_expressions.append({
            'Id': f'{cgw_id}_TunnelDataIn',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/VPN',
                    'MetricName': 'TunnelDataIn',
                    'Dimensions': [
                        {'Name': 'CustomerGatewayId', 'Value': cgw_id}
                    ]
                },
                'Period': 300,
                'Stat': 'Minimum'
            },
            'Label': f'IPSEC-{cgw_id} - TunnelDataIn',
            'ReturnData': True
        })
    
    if tunnel_data_expressions:
        widgets.append({
            'type': 'timeSeries',
            'width': 24,
            'height': 8,
            'x': 0,
            'y': 0,
            'properties': {
                'metrics': tunnel_data_expressions,
                'view': 'timeSeries',
                'stacked': False,
                'region': region,
                'title': f'IPSEC - TunnelDataIn - {region}',
                'period': 300,
                'stat': 'Minimum',
                'legend': {
                    'position': 'bottom'
                }
            }
        })
    
    return widgets


def create_dashboard_body(regions: List[str]) -> Dict[str, Any]:
    """Create the complete dashboard body with all widgets."""
    all_widgets = []
    
    for region in regions:
        print(f"Processing region: {region}")
        
        # Get resources for this region
        db_instances = get_rds_instances(region)
        filesystems = get_efs_filesystems(region)
        vpn_connections = get_vpn_connections(region)
        customer_gateways = get_customer_gateways(region)
        
        print(f"  Found {len(db_instances)} RDS instances, {len(filesystems)} EFS filesystems, "
              f"{len(vpn_connections)} VPN connections, {len(customer_gateways)} customer gateways")
        
        # Create widgets for each resource type
        rds_widgets = create_rds_widgets(region, db_instances)
        efs_widgets = create_efs_widgets(region, filesystems)
        vpn_widgets = create_vpn_widgets(region, vpn_connections)
        ipsec_widgets = create_ipsec_widgets(region, customer_gateways)
        
        all_widgets.extend(rds_widgets)
        all_widgets.extend(efs_widgets)
        all_widgets.extend(vpn_widgets)
        all_widgets.extend(ipsec_widgets)
    
    # Organize widgets in a grid layout
    # We'll create sections for each resource type
    dashboard_body = {
        'widgets': []
    }
    
    # Add header
    dashboard_body['widgets'].append({
        'type': 'text',
        'width': 24,
        'height': 2,
        'x': 0,
        'y': 0,
        'properties': {
            'markdown': f"# Multi-Region Resource Dashboard\n\n"
                        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
                        f"Regions: {', '.join(regions)}"
        }
    })
    
    # Add all widgets
    y_position = 2
    for widget in all_widgets:
        widget['y'] = y_position
        dashboard_body['widgets'].append(widget)
        y_position += widget.get('height', 8)
    
    return dashboard_body


def lambda_handler(event, context):
    """Main Lambda handler."""
    try:
        # Determine regions to monitor
        regions_to_monitor = REGIONS if REGIONS else get_all_regions()
        
        if not regions_to_monitor:
            regions_to_monitor = ['us-east-1']
        
        print(f"Monitoring regions: {regions_to_monitor}")
        
        # Create dashboard name
        account_id = boto3.client('sts').get_caller_identity()['Account']
        dashboard_name = DASHBOARD_NAME
        if DASHBOARD_PREFIX:
            dashboard_name = f"{DASHBOARD_PREFIX}-{dashboard_name}"
        dashboard_name = f"{dashboard_name}-{account_id}"
        
        # Create dashboard body
        dashboard_body = create_dashboard_body(regions_to_monitor)
        
        # Create or update the dashboard
        cloudwatch = boto3.client('cloudwatch')
        
        try:
            # Try to update existing dashboard
            cloudwatch.put_dashboard(
                DashboardName=dashboard_name,
                DashboardBody=json.dumps(dashboard_body)
            )
            print(f"Successfully updated dashboard: {dashboard_name}")
        except cloudwatch.exceptions.ResourceNotFound:
            # Create new dashboard
            cloudwatch.put_dashboard(
                DashboardName=dashboard_name,
                DashboardBody=json.dumps(dashboard_body)
            )
            print(f"Successfully created dashboard: {dashboard_name}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Dashboard created/updated successfully',
                'dashboard_name': dashboard_name,
                'regions': regions_to_monitor,
                'widget_count': len(dashboard_body['widgets'])
            })
        }
        
    except Exception as e:
        print(f"Error in lambda_handler: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


# For local testing
if __name__ == '__main__':
    # Set environment variables for testing
    os.environ['REGIONS'] = 'us-east-1,eu-west-1'
    os.environ['DASHBOARD_NAME'] = 'Test-MultiRegion-Dashboard'
    
    # Initialize boto3 session for local testing
    # You'll need AWS credentials configured
    
    class MockContext:
        pass
    
    result = lambda_handler({}, MockContext())
    print(result)
