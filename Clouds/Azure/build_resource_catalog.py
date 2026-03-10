#!/usr/bin/env python3
"""
build_resource_catalog.py
Generates Clouds/Azure/resource_catalog.json from iconlist.txt.

Schema (keyed by display name, e.g. "Virtual Machine"):
{
  "Virtual Machine": {
    "icon": "10021-icon-service-Virtual-Machine.svg",
    "category": "compute",
    "resourceType": "Microsoft.Compute/virtualMachines",
    "bicepType": "Microsoft.Compute/virtualMachines@2024-11-01",
    "terraformType": "azurerm_linux_virtual_machine",
    "schemaRef": "https://learn.microsoft.com/azure/templates/microsoft.compute/virtualMachines",
    "deployable": true,
    "confidence": "seeded"   // "seeded" | "auto" | "review"
  }
}

confidence:
  seeded  – explicit entry in SEEDED_MAP (highest confidence)
  auto    – derived algorithmically from icon name
  review  – could not resolve, needs manual mapping
"""

import json
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def icon_stem_to_name(stem: str) -> str:
    """'10021-icon-service-Virtual-Machine' → 'Virtual Machine'"""
    # Handle filenames like '030777508 -icon-service-...' (space before dash)
    name = re.sub(r"^\d+\s*-icon-service-", "", stem)
    return name.replace("-", " ").strip()


def schema_ref(resource_type: str) -> str:
    ns, *rest = resource_type.split("/", 1)
    path = "/".join(rest).lower() if rest else ""
    return f"https://learn.microsoft.com/azure/templates/{ns.lower()}/{path}"


# ---------------------------------------------------------------------------
# SEEDED MAP  – key = display name as it appears from icon stem
# Entries cover non-obvious mappings, plural/singular mismatches,
# multi-ARM-type icons, and icons that need a specific bicep API version.
# Format: (resourceType, apiVersion, terraformType)
#         terraformType = "" means diagram/policy only (no azurerm_ resource)
# ---------------------------------------------------------------------------

SEEDED_MAP: dict[str, tuple[str, str, str]] = {
    # ── AI + Machine Learning ──────────────────────────────────────────────
    "Genomics": ("Microsoft.Genomics/accounts", "2018-01-01", "azurerm_genomics_account"),
    "Genomics Accounts": ("Microsoft.Genomics/accounts", "2018-01-01", "azurerm_genomics_account"),
    "Computer Vision": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Custom Vision": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Face APIs": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Content Moderators": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Personalizers": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Speech Services": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Translator Text": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Immersive Readers": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Anomaly Detector": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Form Recognizers": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Azure Experimentation Studio": ("Microsoft.MachineLearningServices/workspaces", "2024-04-01", "azurerm_machine_learning_workspace"),
    "Azure Object Understanding": ("Microsoft.MixedReality/objectAnchorsAccounts", "2021-03-01-preview", ""),
    "Metrics Advisor": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Azure Applied AI Services": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Language": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Cognitive Services Decisions": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Serverless Search": ("Microsoft.Search/searchServices", "2024-03-01-preview", "azurerm_search_service"),
    "Bonsai": ("Microsoft.MachineLearningServices/workspaces", "2024-04-01", "azurerm_machine_learning_workspace"),
    "Content Safety": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Azure OpenAI": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "AI Studio": ("Microsoft.MachineLearningServices/workspaces", "2024-04-01", "azurerm_machine_learning_workspace"),
    "Cognitive Search": ("Microsoft.Search/searchServices", "2024-03-01-preview", "azurerm_search_service"),
    "Cognitive Services": ("Microsoft.CognitiveServices/accounts", "2023-05-01", "azurerm_cognitive_account"),
    "Machine Learning": ("Microsoft.MachineLearningServices/workspaces", "2024-04-01", "azurerm_machine_learning_workspace"),
    "Machine Learning Studio Workspaces": ("Microsoft.MachineLearningServices/workspaces", "2024-04-01", "azurerm_machine_learning_workspace"),
    "Machine Learning Studio Web Service Plans": ("Microsoft.MachineLearningServices/workspaces", "2024-04-01", "azurerm_machine_learning_workspace"),
    "Bot Services": ("Microsoft.BotService/botServices", "2023-09-15-preview", "azurerm_bot_service_azure_bot"),

    # ── Analytics ─────────────────────────────────────────────────────────
    "Event Hubs": ("Microsoft.EventHub/namespaces", "2024-01-01", "azurerm_eventhub_namespace"),
    "Stream Analytics Jobs": ("Microsoft.StreamAnalytics/streamingjobs", "2021-10-01-preview", "azurerm_stream_analytics_job"),
    "Endpoint Analytics": ("Microsoft.OperationalInsights/workspaces", "2023-09-01", "azurerm_log_analytics_workspace"),
    "Azure Synapse Analytics": ("Microsoft.Synapse/workspaces", "2021-06-01", "azurerm_synapse_workspace"),
    "Power BI Embedded": ("Microsoft.PowerBIDedicated/capacities", "2021-01-01", "azurerm_powerbi_embedded"),
    "HD Insight Clusters": ("Microsoft.HDInsight/clusters", "2023-04-15-preview", "azurerm_hdinsight_hadoop_cluster"),
    "Azure Data Explorer Clusters": ("Microsoft.Kusto/clusters", "2023-08-15", "azurerm_kusto_cluster"),
    "Analysis Services": ("Microsoft.AnalysisServices/servers", "2017-08-01", "azurerm_analysis_services_server"),
    "Event Hub Clusters": ("Microsoft.EventHub/clusters", "2024-01-01", "azurerm_eventhub_cluster"),
    "Azure Databricks": ("Microsoft.Databricks/workspaces", "2024-05-01", "azurerm_databricks_workspace"),

    # ── App Services ───────────────────────────────────────────────────────
    "App Service Plans": ("Microsoft.Web/serverfarms", "2023-12-01", "azurerm_service_plan"),
    "App Service Certificates": ("Microsoft.Web/certificates", "2023-12-01", "azurerm_app_service_certificate"),
    "App Service Domains": ("Microsoft.DomainRegistration/domains", "2023-01-01", "azurerm_app_service_domain"),
    "App Services": ("Microsoft.Web/sites", "2023-12-01", "azurerm_linux_web_app"),
    "App Service Environments": ("Microsoft.Web/hostingEnvironments", "2023-12-01", "azurerm_app_service_environment_v3"),

    # ── Azure Ecosystem / Stack ────────────────────────────────────────────
    "Collaborative Service": ("Microsoft.Synapse/workspaces", "2021-06-01", ""),
    "Azure Hybrid Center": ("Microsoft.HybridConnectivity/endpoints", "2023-03-15", ""),
    "Multi Tenancy": ("Microsoft.AzureStack/registrations", "2022-06-15-preview", ""),
    "Infrastructure Backup": ("Microsoft.RecoveryServices/vaults", "2024-04-01", "azurerm_recovery_services_vault"),
    "Capacity": ("Microsoft.Capacity/reservationOrders", "2022-11-01", "azurerm_capacity_reservation"),
    "Offers": ("Microsoft.Subscription/aliases", "2021-10-01", ""),
    "User Subscriptions": ("Microsoft.Subscription/aliases", "2021-10-01", ""),
    "Plans": ("Microsoft.Subscription/aliases", "2021-10-01", ""),
    "Updates": ("Microsoft.Maintenance/maintenanceConfigurations", "2023-04-01", "azurerm_maintenance_configuration"),

    # ── Compute ────────────────────────────────────────────────────────────
    "Maintenance Configuration": ("Microsoft.Maintenance/maintenanceConfigurations", "2023-04-01", "azurerm_maintenance_configuration"),
    "Host Pools": ("Microsoft.DesktopVirtualization/hostPools", "2024-04-03", "azurerm_virtual_desktop_host_pool"),
    "Application Group": ("Microsoft.DesktopVirtualization/applicationGroups", "2024-04-03", "azurerm_virtual_desktop_application_group"),
    "Workspaces": ("Microsoft.DesktopVirtualization/workspaces", "2024-04-03", "azurerm_virtual_desktop_workspace"),
    "Disk Encryption Sets": ("Microsoft.Compute/diskEncryptionSets", "2024-03-02", "azurerm_disk_encryption_set"),
    "Automanaged VM": ("Microsoft.Automanage/configurationProfiles", "2022-05-04", ""),
    "Managed Service Fabric": ("Microsoft.ServiceFabric/managedClusters", "2023-12-01-preview", "azurerm_service_fabric_managed_cluster"),
    "Image Templates": ("Microsoft.VirtualMachineImages/imageTemplates", "2024-02-01", "azurerm_image_builder_template"),
    "Restore Points": ("Microsoft.Compute/restorePointCollections/restorePoints", "2024-03-01", ""),
    "Restore Points Collections": ("Microsoft.Compute/restorePointCollections", "2024-03-01", ""),
    "Azure Compute Galleries": ("Microsoft.Compute/galleries", "2024-03-02", "azurerm_shared_image_gallery"),
    "Compute Fleet": ("Microsoft.AzureFleet/fleets", "2024-11-01", ""),
    "AKS Automatic": ("Microsoft.ContainerService/managedClusters", "2024-09-01", "azurerm_kubernetes_cluster"),
    "Virtual Machine": ("Microsoft.Compute/virtualMachines", "2024-07-01", "azurerm_linux_virtual_machine"),
    "Availability Sets": ("Microsoft.Compute/availabilitySets", "2024-07-01", "azurerm_availability_set"),
    "Disks Snapshots": ("Microsoft.Compute/snapshots", "2024-03-02", "azurerm_snapshot"),
    "Function Apps": ("Microsoft.Web/sites", "2023-12-01", "azurerm_linux_function_app"),
    "Batch Accounts": ("Microsoft.Batch/batchAccounts", "2024-07-01", "azurerm_batch_account"),
    "Disks": ("Microsoft.Compute/disks", "2024-03-02", "azurerm_managed_disk"),
    "Images": ("Microsoft.Compute/images", "2024-07-01", "azurerm_image"),
    "VM Scale Sets": ("Microsoft.Compute/virtualMachineScaleSets", "2024-07-01", "azurerm_linux_virtual_machine_scale_set"),
    "Service Fabric Clusters": ("Microsoft.ServiceFabric/clusters", "2023-11-01-preview", "azurerm_service_fabric_cluster"),
    "Image Definitions": ("Microsoft.Compute/galleries/images", "2024-03-02", "azurerm_shared_image"),
    "Image Versions": ("Microsoft.Compute/galleries/images/versions", "2024-03-02", "azurerm_shared_image_version"),
    "Shared Image Galleries": ("Microsoft.Compute/galleries", "2024-03-02", "azurerm_shared_image_gallery"),
    "Host Groups": ("Microsoft.Compute/hostGroups", "2024-07-01", "azurerm_dedicated_host_group"),
    "Hosts": ("Microsoft.Compute/hostGroups/hosts", "2024-07-01", "azurerm_dedicated_host"),

    # ── Containers ─────────────────────────────────────────────────────────
    "Azure Red Hat OpenShift": ("Microsoft.RedHatOpenShift/openShiftClusters", "2023-11-22", "azurerm_redhat_openshift_cluster"),
    "Kubernetes Services": ("Microsoft.ContainerService/managedClusters", "2024-09-01", "azurerm_kubernetes_cluster"),
    "Container Instances": ("Microsoft.ContainerInstance/containerGroups", "2023-05-01", "azurerm_container_group"),
    "Container Registries": ("Microsoft.ContainerRegistry/registries", "2023-07-01", "azurerm_container_registry"),

    # ── Databases ─────────────────────────────────────────────────────────
    "SQL Data Warehouses": ("Microsoft.Sql/servers/databases", "2023-05-01-preview", "azurerm_synapse_sql_pool"),
    "Azure SQL": ("Microsoft.Sql/servers", "2023-05-01-preview", "azurerm_mssql_server"),
    "SSIS Lift And Shift IR": ("Microsoft.DataFactory/factories/integrationRuntimes", "2018-06-01", "azurerm_data_factory_integration_runtime_azure_ssis"),
    "Azure SQL Edge": ("Microsoft.Sql/servers", "2023-05-01-preview", "azurerm_mssql_server"),
    "Azure Database PostgreSQL Server Group": ("Microsoft.DBforPostgreSQL/serverGroupsv2", "2022-11-08", "azurerm_cosmosdb_postgresql_cluster"),
    "Oracle Database": ("Microsoft.OracleDatabase/cloudVmClusters", "2023-09-01-preview", "azurerm_oracle_cloud_vm_cluster"),
    "Azure Cosmos DB": ("Microsoft.DocumentDB/databaseAccounts", "2024-05-15", "azurerm_cosmosdb_account"),
    "Azure Database MySQL Server": ("Microsoft.DBforMySQL/flexibleServers", "2023-12-30", "azurerm_mysql_flexible_server"),
    "Azure Database MariaDB Server": ("Microsoft.DBforMariaDB/servers", "2018-06-01", "azurerm_mariadb_server"),
    "Azure SQL VM": ("Microsoft.SqlVirtualMachine/sqlVirtualMachines", "2023-10-01", "azurerm_mssql_virtual_machine"),
    "Virtual Clusters": ("Microsoft.Sql/managedInstances", "2023-05-01-preview", "azurerm_mssql_managed_instance"),
    "Elastic Job Agents": ("Microsoft.Sql/servers/jobAgents", "2023-05-01-preview", "azurerm_mssql_job_agent"),
    "SQL Database": ("Microsoft.Sql/servers/databases", "2023-05-01-preview", "azurerm_mssql_database"),
    "Azure Database PostgreSQL Server": ("Microsoft.DBforPostgreSQL/flexibleServers", "2024-03-01-preview", "azurerm_postgresql_flexible_server"),
    "SQL Server": ("Microsoft.Sql/servers", "2023-05-01-preview", "azurerm_mssql_server"),
    "SQL Elastic Pools": ("Microsoft.Sql/servers/elasticPools", "2023-05-01-preview", "azurerm_mssql_elasticpool"),
    "Managed Database": ("Microsoft.Sql/managedInstances/databases", "2023-05-01-preview", "azurerm_mssql_managed_database"),
    "SQL Managed Instance": ("Microsoft.Sql/managedInstances", "2023-05-01-preview", "azurerm_mssql_managed_instance"),
    "Cache Redis": ("Microsoft.Cache/redis", "2024-03-01", "azurerm_redis_cache"),
    "Instance Pools": ("Microsoft.Sql/instancePools", "2023-05-01-preview", "azurerm_mssql_managed_instance"),
    "SQL Server Registries": ("Microsoft.AzureData/sqlServerRegistrations", "2019-07-24-preview", ""),

    # ── DevOps ─────────────────────────────────────────────────────────────
    "CloudTest": ("Microsoft.CloudTest/accounts", "2022-09-01-preview", ""),
    "Load Testing": ("Microsoft.LoadTestService/loadTests", "2022-12-01", "azurerm_load_test"),
    "Lab Accounts": ("Microsoft.LabServices/labaccounts", "2018-10-15", "azurerm_lab_service_lab"),
    "DevOps Starter": ("Microsoft.DevOps/pipelines", "2019-07-01-preview", ""),
    "Managed DevOps Pools": ("Microsoft.DevOpsInfrastructure/pools", "2024-10-19", ""),
    "Code Optimization": ("Microsoft.ProfilerService/Accounts", "2023-09-01-preview", ""),
    "Workspace Gateway": ("Microsoft.DevCenter/devcenters", "2024-05-01-preview", ""),
    "Azure DevOps": ("Microsoft.DevOps/pipelines", "2019-07-01-preview", ""),
    "DevTest Labs": ("Microsoft.DevTestLab/labs", "2018-09-15", "azurerm_dev_test_lab"),
    "Lab Services": ("Microsoft.LabServices/labs", "2022-08-01", "azurerm_lab_service_lab"),

    # ── General ────────────────────────────────────────────────────────────
    "Subscriptions": ("Microsoft.Subscription/aliases", "2021-10-01", "azurerm_subscription"),
    "Reservations": ("Microsoft.Capacity/reservationOrders", "2022-11-01", "azurerm_capacity_reservation_group"),
    "Resource Groups": ("Microsoft.Resources/resourceGroups", "2024-03-01", "azurerm_resource_group"),
    "Templates": ("Microsoft.Resources/deployments", "2024-03-01", "azurerm_resource_group_template_deployment"),
    "Management Groups": ("Microsoft.Management/managementGroups", "2023-04-01", "azurerm_management_group"),
    "Tag": ("Microsoft.Resources/tags", "2024-03-01", "azurerm_resource_group"),
    "Dashboard": ("Microsoft.Portal/dashboards", "2022-12-01-preview", "azurerm_portal_dashboard"),
    "Cost Management": ("Microsoft.CostManagement/exports", "2023-11-01", "azurerm_cost_management_export_resource_group"),
    "Troubleshoot": ("Microsoft.Network/networkWatchers/troubleshootingResults", "2024-03-01", ""),
    "Biz Talk": ("Microsoft.BizTalkServices/BizTalk", "2014-04-01", ""),
    "Blob Block": ("Microsoft.Storage/storageAccounts/blobServices", "2024-01-01", "azurerm_storage_blob"),
    "Blob Page": ("Microsoft.Storage/storageAccounts/blobServices", "2024-01-01", "azurerm_storage_blob"),
    "Branch": ("Microsoft.Deployment/operations", "2024-03-01", ""),
    "Browser": ("", "", ""),
    "Bug": ("", "", ""),
    "Builds": ("Microsoft.DevOps/pipelines", "2019-07-01-preview", ""),
    "Cache": ("Microsoft.Cache/redis", "2024-03-01", "azurerm_redis_cache"),
    "Code": ("", "", ""),
    "Commit": ("", "", ""),
    "Controls": ("", "", ""),
    "Controls Horizontal": ("", "", ""),
    "Cost Alerts": ("Microsoft.CostManagement/budgets", "2023-11-01", "azurerm_consumption_budget_resource_group"),
    "Cost Budgets": ("Microsoft.CostManagement/budgets", "2023-11-01", "azurerm_consumption_budget_resource_group"),
    "Counter": ("", "", ""),
    "Cubes": ("", "", ""),
    "Dev Console": ("", "", ""),
    "Download": ("", "", ""),
    "Error": ("", "", ""),
    "Extensions": ("Microsoft.Compute/virtualMachines/extensions", "2024-07-01", "azurerm_virtual_machine_extension"),
    "File": ("Microsoft.Storage/storageAccounts/fileServices/shares", "2024-01-01", "azurerm_storage_share"),
    "Files": ("Microsoft.Storage/storageAccounts/fileServices/shares", "2024-01-01", "azurerm_storage_share"),
    "Folder Blank": ("", "", ""),
    "Folder Website": ("", "", ""),
    "FTP": ("", "", ""),
    "Gear": ("", "", ""),
    "Globe Error": ("", "", ""),
    "Globe Success": ("", "", ""),
    "Globe Warning": ("", "", ""),
    "Guide": ("", "", ""),
    "Heart": ("", "", ""),
    "Image": ("Microsoft.Compute/images", "2024-07-01", "azurerm_image"),
    "Input Output": ("", "", ""),
    "Journey Hub": ("", "", ""),
    "Load Test": ("Microsoft.LoadTestService/loadTests", "2022-12-01", "azurerm_load_test"),
    "Location": ("", "", ""),
    "Log Streaming": ("Microsoft.OperationalInsights/workspaces", "2023-09-01", "azurerm_log_analytics_workspace"),
    "Media File": ("", "", ""),
    "Mobile": ("", "", ""),
    "Mobile Engagement": ("", "", ""),
    "Power": ("", "", ""),
    "Powershell": ("", "", ""),
    "Power Up": ("", "", ""),
    "Process Explorer": ("", "", ""),
    "Production Ready Database": ("Microsoft.Sql/servers/databases", "2023-05-01-preview", "azurerm_mssql_database"),
    "Resource Linked": ("Microsoft.Resources/links", "2016-09-01", ""),
    "Scheduler": ("Microsoft.Logic/workflows", "2019-05-01", "azurerm_logic_app_workflow"),
    "Search": ("Microsoft.Search/searchServices", "2024-03-01-preview", "azurerm_search_service"),
    "Server Farm": ("Microsoft.Web/serverfarms", "2023-12-01", "azurerm_service_plan"),
    "SSD": ("Microsoft.Compute/disks", "2024-03-02", "azurerm_managed_disk"),
    "Storage Azure Files": ("Microsoft.Storage/storageAccounts/fileServices/shares", "2024-01-01", "azurerm_storage_share"),
    "Storage Container": ("Microsoft.Storage/storageAccounts/blobServices/containers", "2024-01-01", "azurerm_storage_container"),
    "Storage Queue": ("Microsoft.Storage/storageAccounts/queueServices/queues", "2024-01-01", "azurerm_storage_queue"),
    "Table": ("Microsoft.Storage/storageAccounts/tableServices/tables", "2024-01-01", "azurerm_storage_table"),
    "Tags": ("Microsoft.Resources/tags", "2024-03-01", ""),
    "TFS VC Repository": ("", "", ""),
    "Toolbox": ("", "", ""),
    "Versions": ("", "", ""),
    "Website Power": ("Microsoft.Web/sites", "2023-12-01", "azurerm_linux_web_app"),
    "Website Staging": ("Microsoft.Web/sites/slots", "2023-12-01", "azurerm_linux_web_app_slot"),
    "Web Slots": ("Microsoft.Web/sites/slots", "2023-12-01", "azurerm_linux_web_app_slot"),
    "Web Test": ("Microsoft.Insights/webtests", "2022-06-15", "azurerm_application_insights_web_test"),
    "Workbooks": ("Microsoft.Insights/workbooks", "2023-06-01", "azurerm_application_insights_workbook"),
    "Workflow": ("Microsoft.Logic/workflows", "2019-05-01", "azurerm_logic_app_workflow"),
    "Backlog": ("", "", ""),
    "Media": ("Microsoft.Media/mediaservices", "2023-01-01", ""),
    "Module": ("", "", ""),
    "Search Grid": ("Microsoft.Search/searchServices", "2024-03-01-preview", "azurerm_search_service"),

    # ── Hybrid + Multicloud ────────────────────────────────────────────────
    "Azure Operator 5G Core": ("Microsoft.MobileNetwork/packetCoreControlPlanes", "2024-04-01", ""),
    "Azure Operator Nexus": ("Microsoft.NetworkCloud/clusters", "2024-07-01", ""),
    "Azure Operator Insights": ("Microsoft.NetworkAnalytics/dataProducts", "2023-11-15", ""),
    "Azure Operator Service Manager": ("Microsoft.ContainerOrchestratorRuntime/storageClasses", "2024-03-01", ""),
    "Azure Programmable Connectivity": ("Microsoft.ProgrammableConnectivity/gateways", "2024-01-15-preview", ""),
    "Azure Monitor Pipeline": ("Microsoft.Monitor/pipelineGroups", "2024-10-01-preview", ""),

    # ── Identity ───────────────────────────────────────────────────────────
    "Security": ("Microsoft.Security/pricings", "2024-01-01", "azurerm_security_center_subscription_pricing"),
    "Administrative Units": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Verifiable Credentials": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Privleged Identity Management": ("Microsoft.Authorization/roleEligibilitySchedules", "2022-04-01-preview", ""),
    "API Proxy": ("Microsoft.ApiManagement/service", "2024-05-01", "azurerm_api_management"),
    "Tenant Properties": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Identity Custom Roles": ("Microsoft.Authorization/roleDefinitions", "2022-04-01", "azurerm_role_definition"),
    "Entra Identity Licenses": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Connect": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Verified ID": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Verification As A Service": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Multi Factor Authentication": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Global Secure Access": ("Microsoft.NetworkAccess/globalSecureAccess", "2024-05-01-preview", ""),
    "External Identities": ("Microsoft.AzureActiveDirectory/b2cDirectories", "2023-05-17-preview", "azurerm_aadb2c_directory"),
    "Entra Private Access": ("Microsoft.NetworkAccess/privateAccess", "2024-05-01-preview", ""),
    "Entra Internet Access": ("Microsoft.NetworkAccess/internetAccess", "2024-05-01-preview", ""),
    "Entra Connect Sync": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Domain Services": ("Microsoft.AAD/domainServices", "2022-12-01", "azurerm_active_directory_domain_service"),
    "Groups": ("Microsoft.Resources/resourceGroups", "2024-03-01", "azurerm_resource_group"),
    "Active Directory Connect Health": ("Microsoft.ADHybridHealthService/adfarms", "2014-01-01", ""),
    "Entra Connect Health": ("Microsoft.ADHybridHealthService/adfarms", "2014-01-01", ""),
    "Enterprise Applications": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Managed Identities": ("Microsoft.ManagedIdentity/userAssignedIdentities", "2023-07-31-preview", "azurerm_user_assigned_identity"),
    "Managed Identities": ("Microsoft.ManagedIdentity/userAssignedIdentities", "2023-07-31-preview", "azurerm_user_assigned_identity"),
    "Azure AD B2C": ("Microsoft.AzureActiveDirectory/b2cDirectories", "2023-05-17-preview", "azurerm_aadb2c_directory"),
    "Users": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra ID Protection": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "App Registrations": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Identity Governance": ("Microsoft.Authorization/policyAssignments", "2024-04-01", ""),
    "Entra Identity Roles and Administrators": ("Microsoft.Authorization/roleDefinitions", "2022-04-01", "azurerm_role_definition"),
    "User Settings": ("Microsoft.AAD/domainServices", "2022-12-01", ""),

    # ── Integration ────────────────────────────────────────────────────────
    "Integration Service Environments": ("Microsoft.Logic/integrationServiceEnvironments", "2019-05-01", ""),
    "Partner Topic": ("Microsoft.EventGrid/partnerTopics", "2022-06-15", ""),
    "System Topic": ("Microsoft.EventGrid/systemTopics", "2022-06-15", "azurerm_eventgrid_system_topic"),
    "Partner Registration": ("Microsoft.EventGrid/partnerRegistrations", "2022-06-15", ""),
    "Partner Namespace": ("Microsoft.EventGrid/partnerNamespaces", "2022-06-15", ""),
    "Logic Apps": ("Microsoft.Logic/workflows", "2019-05-01", "azurerm_logic_app_workflow"),
    "Power Platform": ("Microsoft.PowerPlatform/accounts", "2020-10-30-preview", ""),
    "Integration Environments": ("Microsoft.IntegrationSpaces/spaces", "2023-11-14-preview", ""),
    "Business Process Tracking": ("Microsoft.BusinessProcess/traces", "2023-11-14-preview", ""),
    "API Management Services": ("Microsoft.ApiManagement/service", "2024-05-01", "azurerm_api_management"),
    "API Connections": ("Microsoft.Web/connections", "2016-06-01", "azurerm_api_connection"),
    "Data Factories": ("Microsoft.DataFactory/factories", "2018-06-01", "azurerm_data_factory"),
    "Event Grid Topics": ("Microsoft.EventGrid/topics", "2022-06-15", "azurerm_eventgrid_topic"),
    "Relays": ("Microsoft.Relay/namespaces", "2021-11-01", "azurerm_relay_namespace"),
    "Azure API for FHIR": ("Microsoft.HealthcareApis/services", "2023-11-01", "azurerm_healthcare_fhir_service"),
    "Software as a Service": ("Microsoft.SaaS/applications", "2023-07-01-preview", ""),
    "Event Grid Domains": ("Microsoft.EventGrid/domains", "2022-06-15", "azurerm_eventgrid_domain"),
    "Azure Data Catalog": ("Microsoft.DataCatalog/catalogs", "2016-03-30", ""),
    "Integration Accounts": ("Microsoft.Logic/integrationAccounts", "2019-05-01", "azurerm_logic_app_integration_account"),
    "App Configuration": ("Microsoft.AppConfiguration/configurationStores", "2024-05-01", "azurerm_app_configuration"),
    "SendGrid Accounts": ("Microsoft.SendGrid/accounts", "2021-06-01", ""),
    "Event Grid Subscriptions": ("Microsoft.EventGrid/eventSubscriptions", "2022-06-15", "azurerm_eventgrid_event_subscription"),
    "Logic Apps Custom Connector": ("Microsoft.Web/customApis", "2016-06-01", ""),
    "Azure Service Bus": ("Microsoft.ServiceBus/namespaces", "2023-01-01-preview", "azurerm_servicebus_namespace"),

    # ── Intune ─────────────────────────────────────────────────────────────
    "Device Security Apple": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Device Security Google": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Device Security Windows": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Intune": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "eBooks": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Client Apps": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Devices": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Device Compliance": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Software Updates": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Security Baselines": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Device Enrollment": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Device Configuration": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Exchange Access": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Tenant Status": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Intune For Education": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Intune App Protection": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "Mindaro": ("Microsoft.ContainerService/managedClusters", "2024-09-01", ""),

    # ── IoT ────────────────────────────────────────────────────────────────
    "Digital Twins": ("Microsoft.DigitalTwins/digitalTwinsInstances", "2023-01-31", "azurerm_digital_twins_instance"),
    "Industrial IoT": ("Microsoft.IoTOperations/instances", "2024-09-15-preview", ""),
    "Azure Stack HCI Sizer": ("Microsoft.AzureStackHCI/clusters", "2024-04-01", "azurerm_stack_hci_cluster"),
    "Stack HCI Premium": ("Microsoft.AzureStackHCI/clusters", "2024-04-01", "azurerm_stack_hci_cluster"),
    "Azure IoT Operations": ("Microsoft.IoTOperations/instances", "2024-09-15-preview", ""),
    "Notification Hub Namespaces": ("Microsoft.NotificationHubs/namespaces", "2023-09-01", "azurerm_notification_hub_namespace"),
    "Azure Stack": ("Microsoft.AzureStack/registrations", "2022-06-15-preview", ""),
    "IoT Hub": ("Microsoft.Devices/IotHubs", "2023-06-30", "azurerm_iothub"),
    "IoT Central Applications": ("Microsoft.IoTCentral/iotApps", "2021-11-01-preview", "azurerm_iotcentral_application"),
    "Azure Maps Accounts": ("Microsoft.Maps/accounts", "2024-01-01-preview", "azurerm_maps_account"),
    "IoT Edge": ("Microsoft.Devices/IotHubs", "2023-06-30", "azurerm_iothub"),
    "Windows10 Core Services": ("Microsoft.WindowsIoT/deviceServices", "2019-06-01", ""),
    "Device Provisioning Services": ("Microsoft.Devices/provisioningServices", "2022-12-12", "azurerm_iothub_dps"),

    # ── Management + Governance ────────────────────────────────────────────
    "Alerts": ("Microsoft.AlertsManagement/alerts", "2023-07-12-preview", "azurerm_monitor_alert_processing_rule_action_group"),
    "Cost Management and Billing": ("Microsoft.CostManagement/exports", "2023-11-01", "azurerm_cost_management_export_resource_group"),
    "Blueprints": ("Microsoft.Blueprint/blueprints", "2018-11-01-preview", "azurerm_blueprint_assignment"),
    "My Customers": ("Microsoft.ManagedServices/registrationDefinitions", "2022-10-01", ""),
    "Recovery Services Vaults": ("Microsoft.RecoveryServices/vaults", "2024-04-01", "azurerm_recovery_services_vault"),
    "Solutions": ("Microsoft.OperationsManagement/solutions", "2015-11-01-preview", "azurerm_log_analytics_solution"),
    "Automation Accounts": ("Microsoft.Automation/automationAccounts", "2023-11-01", "azurerm_automation_account"),
    "Service Providers": ("Microsoft.ManagedServices/registrationDefinitions", "2022-10-01", ""),
    "Service Catalog MAD": ("Microsoft.ServiceCatalog/applications", "2023-07-01-preview", ""),
    "Azure Lighthouse": ("Microsoft.ManagedServices/registrationDefinitions", "2022-10-01", "azurerm_lighthouse_definition"),
    "Universal Print": ("Microsoft.UniversalPrint/printers", "2023-07-26", ""),
    "Azure Arc": ("Microsoft.HybridCompute/machines", "2024-07-31-preview", "azurerm_arc_machine"),
    "Arc Machines": ("Microsoft.HybridCompute/machines", "2024-07-31-preview", "azurerm_arc_machine"),
    "Resources Provider": ("Microsoft.Resources/providers", "2024-03-01", ""),
    "Managed Desktop": ("Microsoft.ManagedServices/registrationDefinitions", "2022-10-01", ""),
    "Managed Applications Center": ("Microsoft.Solutions/applications", "2021-07-01", "azurerm_managed_application"),
    "Customer Lockbox for Microsoft Azure": ("Microsoft.CustomerLockbox/tenantOptIn", "2018-02-28-preview", ""),
    "Policy": ("Microsoft.Authorization/policyDefinitions", "2023-04-01", "azurerm_policy_definition"),
    "Resource Graph Explorer": ("Microsoft.ResourceGraph/graphQueries", "2021-03-01", ""),
    "MachinesAzureArc": ("Microsoft.HybridCompute/machines", "2024-07-31-preview", "azurerm_arc_machine"),

    # ── Menu ───────────────────────────────────────────────────────────────
    "Keys": ("Microsoft.KeyVault/vaults/keys", "2024-04-01-preview", "azurerm_key_vault_key"),

    # ── Migrate ────────────────────────────────────────────────────────────
    "Data Box": ("Microsoft.DataBox/jobs", "2022-12-01", "azurerm_databox_job"),
    "Azure Stack Edge": ("Microsoft.DataBoxEdge/dataBoxEdgeDevices", "2023-12-01", "azurerm_databox_edge_device"),
    "Azure Migrate": ("Microsoft.Migrate/migrateProjects", "2020-06-01-preview", "azurerm_migrate_project"),

    # ── Migration ──────────────────────────────────────────────────────────
    "Azure Database Migration Services": ("Microsoft.DataMigration/services", "2023-07-15-preview", "azurerm_database_migration_service"),

    # ── Mixed Reality ──────────────────────────────────────────────────────
    "Remote Rendering": ("Microsoft.MixedReality/remoteRenderingAccounts", "2021-03-01-preview", "azurerm_mixed_reality_remote_rendering_account"),
    "Spatial Anchor Accounts": ("Microsoft.MixedReality/spatialAnchorsAccounts", "2021-03-01-preview", "azurerm_spatial_anchors_account"),

    # ── Mobile ─────────────────────────────────────────────────────────────
    "Notification Hubs": ("Microsoft.NotificationHubs/namespaces/notificationHubs", "2023-09-01", "azurerm_notification_hub"),

    # ── Monitor ────────────────────────────────────────────────────────────
    "Monitor": ("Microsoft.Insights/components", "2020-02-02", "azurerm_application_insights"),
    "Diagnostics Settings": ("Microsoft.Insights/diagnosticSettings", "2021-05-01-preview", "azurerm_monitor_diagnostic_setting"),
    "Log Analytics Workspaces": ("Microsoft.OperationalInsights/workspaces", "2023-09-01", "azurerm_log_analytics_workspace"),
    "Application Insights": ("Microsoft.Insights/components", "2020-02-02", "azurerm_application_insights"),
    "Azure Monitors for SAP Solutions": ("Microsoft.Workloads/monitors", "2024-02-01-preview", "azurerm_workloads_sap_discovery_virtual_instance"),
    "Change Analysis": ("Microsoft.ChangeAnalysis/changes", "2021-04-01", ""),
    "Azure Workbooks": ("Microsoft.Insights/workbooks", "2023-06-01", "azurerm_application_insights_workbook"),
    "Auto Scale": ("Microsoft.Insights/autoscalesettings", "2022-10-01", "azurerm_monitor_autoscale_setting"),

    # ── Networking ────────────────────────────────────────────────────────
    "CDN Profiles": ("Microsoft.Cdn/profiles", "2024-05-01", "azurerm_cdn_frontdoor_profile"),
    "Azure Firewall Manager": ("Microsoft.Network/firewallPolicies", "2024-03-01", "azurerm_firewall_policy"),
    "Azure Firewall Policy": ("Microsoft.Network/firewallPolicies", "2024-03-01", "azurerm_firewall_policy"),
    "Private Link": ("Microsoft.Network/privateLinkServices", "2024-03-01", "azurerm_private_link_service"),
    "IP Groups": ("Microsoft.Network/ipGroups", "2024-03-01", "azurerm_ip_group"),
    "Virtual WAN Hub": ("Microsoft.Network/virtualHubs", "2024-03-01", "azurerm_virtual_hub"),
    "Private Link Service": ("Microsoft.Network/privateLinkServices", "2024-03-01", "azurerm_private_link_service"),
    "Resource Management Private Link": ("Microsoft.Authorization/privateLinkAssociations", "2020-05-01", ""),
    "Private Link Services": ("Microsoft.Network/privateLinkServices", "2024-03-01", "azurerm_private_link_service"),
    "Load Balancer Hub": ("Microsoft.Network/loadBalancers", "2024-03-01", "azurerm_lb"),
    "Bastions": ("Microsoft.Network/bastionHosts", "2024-03-01", "azurerm_bastion_host"),
    "Virtual Router": ("Microsoft.Network/virtualRouters", "2024-03-01", "azurerm_virtual_hub"),
    "Connected Cache": ("Microsoft.ConnectedCache/cacheNodes", "2023-05-01-preview", ""),
    "Spot VMSS": ("Microsoft.Compute/virtualMachineScaleSets", "2024-07-01", "azurerm_linux_virtual_machine_scale_set"),
    "Spot VM": ("Microsoft.Compute/virtualMachines", "2024-07-01", "azurerm_linux_virtual_machine"),
    "Subnet": ("Microsoft.Network/virtualNetworks/subnets", "2024-03-01", "azurerm_subnet"),
    "DNS Private Resolver": ("Microsoft.Network/dnsResolvers", "2023-07-01-preview", "azurerm_private_dns_resolver"),
    "Azure Communications Gateway": ("Microsoft.VoiceServices/communicationsGateways", "2023-04-03", ""),
    "Application Gateway Containers": ("Microsoft.ServiceNetworking/trafficControllers", "2024-05-01-preview", ""),
    "DNS Security Policy": ("Microsoft.Network/dnsResolverPolicies", "2023-07-01-preview", ""),
    "DNS Multistack": ("Microsoft.Network/dnsZones", "2023-07-01-preview", "azurerm_dns_zone"),
    "ATM Multistack": ("Microsoft.Network/trafficManagerProfiles", "2022-04-01", "azurerm_traffic_manager_profile"),
    "IP Address manager": ("Microsoft.Network/networkManagers/ipamPools", "2024-05-01", ""),
    "Virtual Networks": ("Microsoft.Network/virtualNetworks", "2024-03-01", "azurerm_virtual_network"),
    "Load Balancers": ("Microsoft.Network/loadBalancers", "2024-03-01", "azurerm_lb"),
    "Virtual Network Gateways": ("Microsoft.Network/virtualNetworkGateways", "2024-03-01", "azurerm_virtual_network_gateway"),
    "DNS Zones": ("Microsoft.Network/dnsZones", "2023-07-01-preview", "azurerm_dns_zone"),
    "Traffic Manager Profiles": ("Microsoft.Network/trafficManagerProfiles", "2022-04-01", "azurerm_traffic_manager_profile"),
    "Network Watcher": ("Microsoft.Network/networkWatchers", "2024-03-01", "azurerm_network_watcher"),
    "Network Security Groups": ("Microsoft.Network/networkSecurityGroups", "2024-03-01", "azurerm_network_security_group"),
    "Public IP Addresses": ("Microsoft.Network/publicIPAddresses", "2024-03-01", "azurerm_public_ip"),
    "On Premises Data Gateways": ("Microsoft.Web/connectionGateways", "2016-06-01", "azurerm_on_premise_gateway"),
    "Route Filters": ("Microsoft.Network/routeFilters", "2024-03-01", "azurerm_route_filter"),
    "DDoS Protection Plans": ("Microsoft.Network/ddosProtectionPlans", "2024-03-01", "azurerm_network_ddos_protection_plan"),
    "Front Door and CDN Profiles": ("Microsoft.Cdn/profiles", "2024-05-01", "azurerm_cdn_frontdoor_profile"),
    "Application Gateways": ("Microsoft.Network/applicationGateways", "2024-03-01", "azurerm_application_gateway"),
    "Local Network Gateways": ("Microsoft.Network/localNetworkGateways", "2024-03-01", "azurerm_local_network_gateway"),
    "ExpressRoute Circuits": ("Microsoft.Network/expressRouteCircuits", "2024-03-01", "azurerm_express_route_circuit"),
    "Network Interfaces": ("Microsoft.Network/networkInterfaces", "2024-03-01", "azurerm_network_interface"),
    "Connections": ("Microsoft.Network/connections", "2024-03-01", "azurerm_virtual_network_gateway_connection"),
    "Route Tables": ("Microsoft.Network/routeTables", "2024-03-01", "azurerm_route_table"),
    "Firewalls": ("Microsoft.Network/azureFirewalls", "2024-03-01", "azurerm_firewall"),
    "Service Endpoint Policies": ("Microsoft.Network/serviceEndpointPolicies", "2024-03-01", ""),
    "NAT": ("Microsoft.Network/natGateways", "2024-03-01", "azurerm_nat_gateway"),
    "Virtual WANs": ("Microsoft.Network/virtualWans", "2024-03-01", "azurerm_virtual_wan"),
    "Web Application Firewall Policies(WAF)": ("Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies", "2024-03-01", "azurerm_web_application_firewall_policy"),
    "Proximity Placement Groups": ("Microsoft.Compute/proximityPlacementGroups", "2024-07-01", "azurerm_proximity_placement_group"),
    "Public IP Prefixes": ("Microsoft.Network/publicIPPrefixes", "2024-03-01", "azurerm_public_ip_prefix"),

    # ── New Icons ─────────────────────────────────────────────────────────
    "Toolchain Orchestrator": ("Microsoft.AzureFleet/fleets", "2024-11-01", ""),
    "Workload Orchestration": ("Microsoft.Workloads/monitors", "2024-02-01-preview", ""),
    "Data Virtualization": ("Microsoft.Synapse/workspaces", "2021-06-01", ""),
    "Edge Actions": ("Microsoft.IoTOperations/instances", "2024-09-15-preview", ""),
    "FRD QA": ("", "", ""),
    "Landing Zone": ("Microsoft.Resources/resourceGroups", "2024-03-01", "azurerm_resource_group"),
    "Service Groups": ("Microsoft.Management/managementGroups", "2023-04-01", "azurerm_management_group"),
    "Engage Center Connect": ("Microsoft.Communication/communicationServices", "2023-06-01-preview", "azurerm_communication_service"),
    "pubsub": ("Microsoft.SignalRService/webPubSub", "2024-04-01-preview", "azurerm_web_pubsub"),
    "Storage Hubs": ("Microsoft.Storage/storageAccounts", "2024-01-01", "azurerm_storage_account"),
    "Service Group Relationships": ("Microsoft.Management/managementGroups", "2023-04-01", "azurerm_management_group"),
    # double-space variant from filename '030777508 -icon-service-...'
    " icon service Service Group Relationships": ("Microsoft.Management/managementGroups", "2023-04-01", "azurerm_management_group"),
    "Stage Maps": ("", "", ""),
    "Logic Apps Template": ("Microsoft.Logic/workflows", "2019-05-01", "azurerm_logic_app_workflow"),
    "AKS Network Policy": ("Microsoft.ContainerService/managedClusters", "2024-09-01", "azurerm_kubernetes_cluster"),
    "Microsoft Discovery": ("", "", ""),
    "Scheduled Actions": ("Microsoft.CostManagement/scheduledActions", "2023-11-01", ""),
    "promethus": ("Microsoft.Monitor/accounts", "2023-04-03", "azurerm_monitor_workspace"),
    "Kubernetes Hub": ("Microsoft.ContainerService/managedClusters", "2024-09-01", "azurerm_kubernetes_cluster"),
    "Azure Local": ("Microsoft.AzureStackHCI/clusters", "2024-04-01", "azurerm_stack_hci_cluster"),
    "Azure App Testing": ("Microsoft.LoadTestService/loadTests", "2022-12-01", "azurerm_load_test"),
    "Azure Container Storage": ("Microsoft.KubernetesConfiguration/extensions", "2023-05-01", ""),
    "external id": ("Microsoft.AzureActiveDirectory/b2cDirectories", "2023-05-17-preview", "azurerm_aadb2c_directory"),
    "external id modified": ("Microsoft.AzureActiveDirectory/b2cDirectories", "2023-05-17-preview", "azurerm_aadb2c_directory"),
    "VNet Appliance": ("Microsoft.Network/networkVirtualAppliances", "2024-03-01", ""),
    "Monitor Issues": ("Microsoft.AlertsManagement/alerts", "2023-07-12-preview", ""),
    "Azure Consumption Commitment": ("Microsoft.Billing/billingAccounts", "2024-04-01", ""),
    "Edge Storage Accelerator": ("Microsoft.KubernetesConfiguration/extensions", "2023-05-01", ""),
    "Operation Center": ("Microsoft.Insights/components", "2020-02-02", "azurerm_application_insights"),
    "SQL Database Fleet Manager": ("Microsoft.Sql/servers/databases", "2023-05-01-preview", "azurerm_mssql_database"),
    "Breeze": ("", "", ""),
    "Network Foundation Hub": ("Microsoft.Network/networkManagers", "2024-03-01", "azurerm_network_manager"),
    "Azure Linux": ("Microsoft.Compute/virtualMachines", "2024-07-01", "azurerm_linux_virtual_machine"),
    "Network Security Hub": ("Microsoft.Network/networkManagers", "2024-03-01", "azurerm_network_manager"),
    "Hybrid Connectivity Hub": ("Microsoft.Network/networkManagers", "2024-03-01", "azurerm_network_manager"),
    "Azure Managed Redis": ("Microsoft.Cache/redisEnterprise", "2024-09-01-preview", "azurerm_redis_enterprise_cluster"),
    "AI at Edge": ("Microsoft.IoTOperations/instances", "2024-09-15-preview", ""),
    "VPNClientWindows": ("Microsoft.Network/vpnGateways", "2024-03-01", "azurerm_vpn_gateway"),
    "Planetary Computer Pro": ("Microsoft.Sustainability/sustainabilityAccounts", "2023-10-01-preview", ""),

    # ── Other ─────────────────────────────────────────────────────────────
    "Azure Virtual Desktop": ("Microsoft.DesktopVirtualization/workspaces", "2024-04-03", "azurerm_virtual_desktop_workspace"),
    "SSH Keys": ("Microsoft.Compute/sshPublicKeys", "2024-07-01", "azurerm_ssh_public_key"),
    "Internet Analyzer Profiles": ("Microsoft.Network/networkExperimentProfiles", "2019-11-01", ""),
    "Azure Cloud Shell": ("Microsoft.CloudShell/consoles", "2018-10-01", ""),
    "Video Analyzers": ("Microsoft.Media/videoAnalyzers", "2021-11-01-preview", ""),
    "ExpressRoute Direct": ("Microsoft.Network/expressRoutePorts", "2024-03-01", "azurerm_express_route_port"),
    "Cost Export": ("Microsoft.CostManagement/exports", "2023-11-01", "azurerm_cost_management_export_resource_group"),
    "Azure Communication Services": ("Microsoft.Communication/communicationServices", "2023-06-01-preview", "azurerm_communication_service"),
    "Peering Service": ("Microsoft.Peering/peeringServices", "2022-10-01", ""),
    "Azure Network Function Manager Functions": ("Microsoft.HybridNetwork/networkFunctions", "2023-09-01", ""),
    "Log Analytics Query Pack": ("Microsoft.OperationalInsights/queryPacks", "2019-09-01-preview", "azurerm_log_analytics_query_pack"),
    "Arc Kubernetes": ("Microsoft.Kubernetes/connectedClusters", "2024-07-15-preview", "azurerm_arc_kubernetes_cluster"),
    "Azure VMware Solution": ("Microsoft.AVS/privateClouds", "2023-09-01", "azurerm_vmware_private_cloud"),
    "Peerings": ("Microsoft.Peering/peerings", "2022-10-01", "azurerm_express_route_circuit_peering"),
    "Dashboard Hub": ("Microsoft.Portal/dashboards", "2022-12-01-preview", "azurerm_portal_dashboard"),
    "Azure Video Indexer": ("Microsoft.VideoIndexer/accounts", "2024-01-01", "azurerm_video_indexer_account"),
    "AVS VM": ("Microsoft.AVS/privateClouds/clusters", "2023-09-01", ""),
    "Arc PostgreSQL ": ("Microsoft.AzureArcData/postgresInstances", "2023-01-15-preview", ""),
    "Arc PostgreSQL": ("Microsoft.AzureArcData/postgresInstances", "2023-01-15-preview", ""),
    "Arc SQL Managed Instance": ("Microsoft.AzureArcData/sqlManagedInstances", "2023-01-15-preview", ""),
    "Arc SQL Server": ("Microsoft.AzureArcData/sqlServerInstances", "2023-01-15-preview", ""),
    "Data Collection Rules": ("Microsoft.Insights/dataCollectionRules", "2022-06-01", "azurerm_monitor_data_collection_rule"),
    "Resource Mover": ("Microsoft.Migrate/moveCollections", "2023-08-01", ""),
    "Azure Chaos Studio": ("Microsoft.Chaos/experiments", "2024-01-01", "azurerm_chaos_studio_experiment"),
    "Network Managers": ("Microsoft.Network/networkManagers", "2024-03-01", "azurerm_network_manager"),
    "Dedicated HSM": ("Microsoft.HardwareSecurityModules/dedicatedHSMs", "2024-06-30-preview", "azurerm_dedicated_hardware_security_module"),
    "Modular Data Center": ("Microsoft.DataBoxEdge/dataBoxEdgeDevices", "2023-12-01", "azurerm_databox_edge_device"),
    "Template Specs": ("Microsoft.Resources/templateSpecs", "2022-02-01", "azurerm_resource_group_template_deployment"),
    "Arc Data services": ("Microsoft.AzureArcData/dataControllers", "2023-01-15-preview", ""),
    "Azure Backup Center": ("Microsoft.RecoveryServices/vaults", "2024-04-01", "azurerm_recovery_services_vault"),
    "Backup Vault": ("Microsoft.DataProtection/backupVaults", "2024-04-01", "azurerm_data_protection_backup_vault"),
    "Device Update IoT Hub": ("Microsoft.DeviceUpdate/accounts", "2023-07-01", ""),
    "Fiji": ("", "", ""),
    "Azure Monitor Dashboard": ("Microsoft.Portal/dashboards", "2022-12-01-preview", "azurerm_portal_dashboard"),
    "SCVMM Management Servers": ("Microsoft.ScVmm/vmmServers", "2024-06-01", ""),
    "Cloud Services (extended support)": ("Microsoft.Compute/cloudServices", "2024-07-01", "azurerm_cloud_service"),
    "Azure Support Center Blue": ("", "", ""),
    "Web App + Database": ("Microsoft.Web/sites", "2023-12-01", "azurerm_linux_web_app"),
    "Azure HPC Workbenches": ("Microsoft.DesktopVirtualization/workspaces", "2024-04-03", ""),
    "Disk Pool": ("Microsoft.StoragePool/diskPools", "2021-08-01", "azurerm_disk_pool"),
    "Bare Metal Infrastructure": ("Microsoft.BareMetal/baremetalInstances", "2021-09-01-preview", ""),
    "Connected Vehicle Platform": ("Microsoft.ConnectedVehicle/platformAccounts", "2022-11-01-preview", ""),
    "Private Endpoints": ("Microsoft.Network/privateEndpoints", "2024-03-01", "azurerm_private_endpoint"),
    "Open Supply Chain Platform": ("", "", ""),
    "Aquila": ("", "", ""),
    "Reserved Capacity": ("Microsoft.Capacity/reservationOrders", "2022-11-01", "azurerm_capacity_reservation_group"),
    "Custom IP Prefix": ("Microsoft.Network/customIpPrefixes", "2024-03-01", "azurerm_custom_ip_prefix"),
    "FHIR Service": ("Microsoft.HealthcareApis/workspaces/fhirservices", "2023-11-01", "azurerm_healthcare_fhir_service"),
    "MedTech Service": ("Microsoft.HealthcareApis/workspaces/iotconnectors", "2023-11-01", "azurerm_healthcare_medtech_service"),
    "Managed Instance Apache Cassandra": ("Microsoft.DocumentDB/cassandraClusters", "2024-05-15", "azurerm_cosmosdb_cassandra_cluster"),
    "Confidential Ledgers": ("Microsoft.ConfidentialLedger/ledgers", "2023-06-28-preview", "azurerm_confidential_ledger"),
    "Test Base": ("Microsoft.TestBase/testBaseAccounts", "2022-04-01-preview", ""),
    "Azure Orbital": ("Microsoft.Orbital/spacecrafts", "2022-11-01", ""),
    "Capacity Reservation Groups": ("Microsoft.Compute/capacityReservationGroups", "2024-07-01", "azurerm_capacity_reservation_group"),
    "Windows Notification Services": ("Microsoft.NotificationHubs/namespaces", "2023-09-01", "azurerm_notification_hub_namespace"),
    "Azure Network Function Manager": ("Microsoft.HybridNetwork/networkFunctions", "2023-09-01", ""),
    "Mission Landing Zone": ("Microsoft.Resources/resourceGroups", "2024-03-01", "azurerm_resource_group"),
    "Mobile Networks": ("Microsoft.MobileNetwork/mobileNetworks", "2024-04-01", "azurerm_mobile_network"),
    "VM App Definitions": ("Microsoft.Compute/galleries/applications", "2024-03-02", ""),
    "VM App Versions": ("Microsoft.Compute/galleries/applications/versions", "2024-03-02", ""),
    "Azure Edge Hardware Center": ("Microsoft.EdgeOrder/addresses", "2022-05-01-preview", ""),
    "Resource Guard": ("Microsoft.DataProtection/resourceGuards", "2024-04-01", "azurerm_data_protection_resource_guard"),
    "Ceres": ("", "", ""),
    "Azurite": ("Microsoft.Storage/storageAccounts", "2024-01-01", "azurerm_storage_account"),
    "Update Management Center": ("Microsoft.Maintenance/maintenanceConfigurations", "2023-04-01", "azurerm_maintenance_configuration"),
    "Community Images": ("Microsoft.Compute/communityGalleries", "2024-03-02", ""),
    "VM Image Version": ("Microsoft.Compute/galleries/images/versions", "2024-03-02", "azurerm_shared_image_version"),
    "Savings Plans": ("Microsoft.BillingBenefits/savingsPlanOrders", "2022-11-01", ""),
    "Worker Container App": ("Microsoft.App/containerApps", "2024-08-02-preview", "azurerm_container_app"),
    "Azure Managed Grafana": ("Microsoft.Dashboard/grafana", "2023-09-01", "azurerm_dashboard_grafana"),
    "Targets Management": ("Microsoft.Chaos/experiments", "2024-01-01", "azurerm_chaos_studio_target"),
    "Storage Functions": ("Microsoft.Storage/storageAccounts", "2024-01-01", "azurerm_storage_account"),
    "Sonic Dash": ("", "", ""),
    "Compliance Center": ("Microsoft.Security/pricings", "2024-01-01", ""),
    "Network Security Perimeters": ("Microsoft.Network/networkSecurityPerimeters", "2023-08-01-preview", ""),
    "Azure Load Testing": ("Microsoft.LoadTestService/loadTests", "2022-12-01", "azurerm_load_test"),
    "Virtual Visits Builder": ("Microsoft.Communication/communicationServices", "2023-06-01-preview", ""),
    "Azure Quotas": ("Microsoft.Quota/quotas", "2023-02-01", ""),
    "Container Apps Environments": ("Microsoft.App/managedEnvironments", "2024-08-02-preview", "azurerm_container_app_environment"),
    "App Compliance Automation": ("Microsoft.AppComplianceAutomation/reports", "2024-06-27", ""),
    "Virtual Instance for SAP": ("Microsoft.Workloads/sapVirtualInstances", "2024-09-01", "azurerm_workloads_sap_three_tier_virtual_instance"),
    "Azure Center for SAP": ("Microsoft.Workloads/sapVirtualInstances", "2024-09-01", ""),
    "Azure Storage Mover": ("Microsoft.StorageMover/storageMovers", "2024-07-01", "azurerm_storage_mover"),
    "Central Service Instance For SAP": ("Microsoft.Workloads/sapVirtualInstances/centralInstances", "2024-09-01", ""),
    "Kubernetes Fleet Manager": ("Microsoft.ContainerService/fleets", "2024-05-02-preview", ""),
    "Express Route Traffic Collector": ("Microsoft.NetworkFunction/azureTrafficCollectors", "2023-11-01", ""),
    "Database Instance For SAP": ("Microsoft.Workloads/sapVirtualInstances/databaseInstances", "2024-09-01", ""),
    "Elastic SAN": ("Microsoft.ElasticSan/elasticSans", "2024-05-01", "azurerm_elastic_san"),
    "Microsoft Dev Box": ("Microsoft.DevCenter/devcenters", "2024-05-01-preview", "azurerm_dev_center"),
    "Azure Deployment Environments": ("Microsoft.DevCenter/projects", "2024-05-01-preview", "azurerm_dev_center_project"),
    "Azure Dev Tunnels": ("Microsoft.DevTunnels/tunnels", "2023-09-28-preview", ""),
    "Azure Sustainability": ("Microsoft.Sustainability/sustainabilityAccounts", "2023-10-01-preview", ""),
    "IcM Troubleshooting": ("", "", ""),
    "OSConfig": ("Microsoft.HybridCompute/machines/extensions", "2024-07-31-preview", ""),
    "Virtual Enclaves": ("Microsoft.Network/networkWatchers", "2024-03-01", ""),
    "AKS Istio": ("Microsoft.ContainerService/managedClusters", "2024-09-01", "azurerm_kubernetes_cluster"),
    "Defender CM Local Manager": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender External Management": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Freezer Monitor": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Historian": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender HMI": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Marquee": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Robot Controller": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Sensor": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Slot": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Web Guiding System": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender DCS Controller": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Distributer Control System": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Engineering Station": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Industrial Packaging System": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Industrial Printer": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Industrial Scale System": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Industrial Robot": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Meter": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender PLC": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Pneumatic Device": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Programable Board": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender Relay": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "Defender RTU": ("Microsoft.IoTSecurity/sensors", "2021-02-01-preview", ""),
    "HDI AKS Cluster": ("Microsoft.ContainerService/managedClusters", "2024-09-01", "azurerm_kubernetes_cluster"),
    "Monitor Health Models": ("Microsoft.Insights/components", "2020-02-02", "azurerm_application_insights"),
    "WAC Installer": ("", "", ""),
    "Azure A": ("", "", ""),
    "Edge Management": ("Microsoft.IoTOperations/instances", "2024-09-15-preview", ""),
    "Azure Sphere": ("Microsoft.AzureSphere/catalogs", "2024-07-01-preview", ""),
    "Exchange On Premises Access": ("Microsoft.Intune/deviceManagement", "2017-07-01", ""),
    "WAC": ("", "", ""),
    "AzureAttestation": ("Microsoft.Attestation/attestationProviders", "2021-06-01-preview", "azurerm_attestation_provider"),
    "RTOS": ("", "", ""),
    "Web Jobs": ("Microsoft.Web/sites/webjobs", "2023-12-01", ""),

    # ── Security ──────────────────────────────────────────────────────────
    "Detonation": ("Microsoft.Security/pricings", "2024-01-01", ""),
    "Microsoft Defender for IoT": ("Microsoft.IoTSecurity/defenderSettings", "2021-02-01-preview", ""),
    "Microsoft Defender EASM": ("Microsoft.Easm/workspaces", "2022-04-01-preview", "azurerm_security_center_subscription_pricing"),
    "Identity Secure Score": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Identity Risky Signins": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Entra Identity Risky Users": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Multifactor Authentication": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Azure Information Protection": ("Microsoft.InformationProtection/infoTypes", "2020-01-01", ""),
    "Conditional Access": ("Microsoft.AAD/domainServices", "2022-12-01", ""),
    "Microsoft Defender for Cloud": ("Microsoft.Security/pricings", "2024-01-01", "azurerm_security_center_subscription_pricing"),
    "Application Security Groups": ("Microsoft.Network/applicationSecurityGroups", "2024-03-01", "azurerm_application_security_group"),
    "Key Vaults": ("Microsoft.KeyVault/vaults", "2024-04-01-preview", "azurerm_key_vault"),
    "Azure Sentinel": ("Microsoft.OperationalInsights/workspaces", "2023-09-01", "azurerm_sentinel_log_analytics_workspace_onboarding"),
    "ExtendedSecurityUpdates": ("Microsoft.HybridCompute/machines", "2024-07-31-preview", ""),

    # ── Storage ────────────────────────────────────────────────────────────
    "Azure Databox Gateway": ("Microsoft.DataBoxEdge/dataBoxEdgeDevices", "2023-12-01", "azurerm_databox_edge_device"),
    "Azure HCP Cache": ("Microsoft.StorageCache/caches", "2023-11-01-preview", "azurerm_hpc_cache"),
    "Storage Actions": ("Microsoft.StorageActions/storageTasks", "2024-05-01-preview", ""),
    "Managed File Shares": ("Microsoft.Storage/storageAccounts/fileServices/shares", "2024-01-01", "azurerm_storage_share"),
    "Storage Accounts": ("Microsoft.Storage/storageAccounts", "2024-01-01", "azurerm_storage_account"),
    "StorSimple Device Managers": ("Microsoft.StorSimple/managers", "2017-06-01", ""),
    "Storage Explorer": ("Microsoft.Storage/storageAccounts", "2024-01-01", "azurerm_storage_account"),
    "StorSimple Data Managers": ("Microsoft.StorSimple/managers", "2017-06-01", ""),
    "Storage Sync Services": ("Microsoft.StorageSync/storageSyncServices", "2022-06-01", "azurerm_storage_sync"),
    "Azure NetApp Files": ("Microsoft.NetApp/netAppAccounts", "2024-07-01", "azurerm_netapp_account"),
    "Data Share Invitations": ("Microsoft.DataShare/accounts/shares", "2021-08-01", "azurerm_data_share"),
    "Data Shares": ("Microsoft.DataShare/accounts", "2021-08-01", "azurerm_data_share_account"),
    "Import Export Jobs": ("Microsoft.ImportExport/jobs", "2021-01-01", ""),
    "Azure Fileshares": ("Microsoft.Storage/storageAccounts/fileServices/shares", "2024-01-01", "azurerm_storage_share"),

    # ── Web ────────────────────────────────────────────────────────────────
    "Static Apps": ("Microsoft.Web/staticSites", "2023-12-01", "azurerm_static_web_app"),
    "SignalR": ("Microsoft.SignalRService/signalR", "2024-03-01", "azurerm_signalr_service"),
    "API Center": ("Microsoft.ApiCenter/services", "2024-03-15-preview", ""),
    "App Space": ("Microsoft.AppContainers/connectedEnvironments", "2024-02-02-preview", ""),
    "App Space Component": ("Microsoft.AppContainers/connectedEnvironments", "2024-02-02-preview", ""),
}

# ---------------------------------------------------------------------------
# Determine deployable flag
# ---------------------------------------------------------------------------

NON_DEPLOYABLE_STEMS = {
    # Truly has no ARM resource type → diagram-only
    "Browser", "Bug", "Code", "Commit", "Controls", "Controls Horizontal",
    "Counter", "Cubes", "Dev Console", "Download", "Error", "Fiji",
    "Folder Blank", "Folder Website", "FTP", "Gear", "Globe Error",
    "Globe Success", "Globe Warning", "Guide", "Heart", "Input Output",
    "Journey Hub", "Location", "Media File", "Mobile", "Mobile Engagement",
    "Power", "Powershell", "Power Up", "Process Explorer", "TFS VC Repository",
    "Toolbox", "Versions", "FRD QA", "Stage Maps", "Microsoft Discovery",
    "Breeze", "Sonic Dash", "Ceres", "Aquila", "Open Supply Chain Platform",
    "Azure Cloud Shell", "Azure Support Center Blue", "IcM Troubleshooting",
    "WAC Installer", "WAC", "Azure A", "RTOS", "Branch",
    "Biz Talk", "Module",
    # Admin/portal concepts with no deployable ARM type
    "Administrative Units", "Tenant Properties", "Entra Identity Licenses",
    "Enterprise Applications", "App Registrations", "Users", "Groups",
    "Entra Connect", "Entra ID Protection", "Identity Governance",
    "User Settings", "Verifiable Credentials", "Entra Verified ID",
    "Verification As A Service", "Security", "Multi Factor Authentication",
    "Multifactor Authentication", "Identity Secure Score",
    "Entra Identity Risky Signins", "Entra Identity Risky Users",
    "Conditional Access", "Azure Information Protection",
    "Detonation", "ExtendedSecurityUpdates",
    # IoT device types (Defender for IoT) — not ARM deployable resources per se
    # leaving them seeded as non-empty so they show up for diagramming
}


def is_deployable(display_name: str, resource_type: str) -> bool:
    if display_name in NON_DEPLOYABLE_STEMS:
        return False
    if not resource_type:
        return False
    return True


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_catalog(iconlist_path: str) -> dict:
    catalog: dict = {}

    with open(iconlist_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    for line in lines:
        # Extract filename: './Icons/compute/10021-icon-service-Virtual-Machine.svg'
        parts = line.split("/")
        if len(parts) < 3:
            continue
        filename = parts[-1]
        category = parts[-2].strip()
        stem = filename.replace(".svg", "")
        display_name = icon_stem_to_name(stem)

        if display_name in SEEDED_MAP:
            resource_type, api_version, tf_type = SEEDED_MAP[display_name]
            confidence = "seeded"
        else:
            # Auto-fallback: leave blank, mark for review
            resource_type = ""
            api_version = ""
            tf_type = ""
            confidence = "review"

        bicep_type = f"{resource_type}@{api_version}" if resource_type and api_version else ""
        deployable = is_deployable(display_name, resource_type)
        ref = schema_ref(resource_type) if resource_type else ""

        entry = {
            "icon": filename,
            "category": category,
            "resourceType": resource_type,
            "bicepType": bicep_type,
            "terraformType": tf_type,
            "schemaRef": ref,
            "deployable": deployable,
            "confidence": confidence,
        }

        # If the same display name appears twice (e.g., two icon files with same name
        # like Workspaces in compute vs compute/00330), first-seen wins for seeded;
        # review entries may be overwritten by a later seeded match.
        if display_name not in catalog or (
            catalog[display_name]["confidence"] == "review" and confidence == "seeded"
        ):
            catalog[display_name] = entry

    return catalog


if __name__ == "__main__":
    base = Path(__file__).parent
    iconlist = base / "iconlist.txt"
    out_path = base / "resource_catalog.json"

    catalog = build_catalog(str(iconlist))

    # Stats
    total = len(catalog)
    seeded = sum(1 for v in catalog.values() if v["confidence"] == "seeded")
    review = sum(1 for v in catalog.values() if v["confidence"] == "review")
    deployable = sum(1 for v in catalog.values() if v["deployable"])

    with open(out_path, "w") as f:
        json.dump(catalog, f, indent=2)

    print(f"✅  resource_catalog.json written → {out_path}")
    print(f"   Total entries : {total}")
    print(f"   Seeded        : {seeded}")
    print(f"   Needs review  : {review}")
    print(f"   Deployable    : {deployable}")

    if review:
        print("\n⚠️  Entries needing review (no ARM type mapped):")
        for name, v in sorted(catalog.items()):
            if v["confidence"] == "review":
                print(f"    [{v['category']}] {name}")
