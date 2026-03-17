terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

variable "name" {
  description = "Name for the resource group and AKS cluster"
  type        = string
}

variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "germanywestcentral"
}

variable "node_count" {
  description = "Number of nodes in the user pool"
  type        = number
  default     = 3
}

variable "node_vm_size" {
  description = "VM size for user pool nodes"
  type        = string
  default     = "Standard_D4s_v3"
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.31"
}

resource "azurerm_resource_group" "this" {
  name     = var.name
  location = var.location
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = var.name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = var.name
  kubernetes_version  = var.kubernetes_version

  default_node_pool {
    name                 = "system"
    node_count           = 1
    vm_size              = "Standard_D2s_v3"
    os_disk_size_gb      = 128
    temporary_name_for_rotation = "systemtmp"
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin = "azure"
  }

  # Disable all monitoring
  monitor_metrics {
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "user" {
  name                  = "userpool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.node_vm_size
  node_count            = var.node_count
  os_disk_size_gb       = 128
  enable_node_public_ip = true
}

# Allow all inbound traffic on the AKS node resource group NSG
data "azurerm_resource_group" "node_rg" {
  name = azurerm_kubernetes_cluster.this.node_resource_group
}

data "azurerm_resources" "nsgs" {
  resource_group_name = data.azurerm_resource_group.node_rg.name
  type                = "Microsoft.Network/networkSecurityGroups"
}

resource "azurerm_network_security_rule" "allow_all_inbound" {
  count                       = length(data.azurerm_resources.nsgs.resources)
  name                        = "AllowAllInbound"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = data.azurerm_resource_group.node_rg.name
  network_security_group_name = data.azurerm_resources.nsgs.resources[count.index].name
}

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "cluster_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "kube_config_command" {
  value = "az aks get-credentials --resource-group ${var.name} --name ${var.name}"
}
