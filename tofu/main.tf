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
  default     = "westeurope"
}

variable "node_count" {
  description = "Number of nodes in the user pool (ignored when spot is true: then 1 regular node)"
  type        = number
  default     = 4
}

# Must have a local temp disk (the "d" in the name) of at least 128 GiB: the OS disk is ephemeral.
variable "node_vm_size" {
  description = "VM size for user pool nodes"
  type        = string
  default     = "Standard_D8ads_v5"
}

variable "spot" {
  description = "Run the workers as 1 regular node plus spot_node_count Azure Spot nodes (needs the Kyverno spot toleration policy)"
  type        = bool
  default     = false
}

variable "spot_node_count" {
  description = "Number of Spot nodes when spot is true"
  type        = number
  default     = 3
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.35"
}

variable "owner" {
  description = "Owner label for the resource group and AKS cluster"
  type        = string
}

resource "azurerm_resource_group" "this" {
  name     = var.name
  location = var.location
  tags = {
    owner = var.owner
  }
}

# Networking - create VNet, Subnet, NSG with known names so we can manage rules directly
resource "azurerm_virtual_network" "this" {
  name                = "${var.name}-vnet"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = ["10.0.0.0/8"]
}

resource "azurerm_subnet" "aks" {
  name                 = "${var.name}-aks-subnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.240.0.0/16"]
}

resource "azurerm_network_security_group" "aks" {
  name                = "${var.name}-nsg"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_subnet_network_security_group_association" "aks" {
  subnet_id                 = azurerm_subnet.aks.id
  network_security_group_id = azurerm_network_security_group.aks.id
}

resource "azurerm_network_security_rule" "allow_all_inbound" {
  name                        = "AllowAllInbound"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.aks.name
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
    vnet_subnet_id       = azurerm_subnet.aks.id
    max_pods             = 100
    temporary_name_for_rotation = "systemtmp"

    upgrade_settings {
      max_surge = "10%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin = "azure"
  }

  tags = {
    owner = var.owner
  }
}

# The AKS-managed NSG (in the MC_* resource group) is attached to node NICs.
# Azure evaluates both subnet and NIC NSGs, so we need the rule on both.
resource "terraform_data" "allow_all_inbound_aks_nsg" {
  depends_on = [azurerm_kubernetes_cluster.this]

  input = azurerm_kubernetes_cluster.this.node_resource_group

  provisioner "local-exec" {
    command = <<-EOT
      NSG_NAME=$(az network nsg list -g ${azurerm_kubernetes_cluster.this.node_resource_group} --query '[0].name' -o tsv)
      az network nsg rule create \
        -g ${azurerm_kubernetes_cluster.this.node_resource_group} \
        --nsg-name "$NSG_NAME" \
        -n AllowAllInbound \
        --priority 100 \
        --direction Inbound \
        --access Allow \
        --protocol '*' \
        --source-address-prefixes '*' \
        --destination-address-prefixes '*' \
        --source-port-ranges '*' \
        --destination-port-ranges '*'
    EOT
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "user" {
  name                  = "userpool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.node_vm_size
  node_count            = var.spot ? 1 : var.node_count
  os_disk_size_gb       = 128
  os_disk_type          = "Ephemeral"
  vnet_subnet_id        = azurerm_subnet.aks.id
  max_pods              = 100
  node_public_ip_enabled = true

  upgrade_settings {
    max_surge = "10%"
  }
}

# Spot nodes have no public IP: discover-node-ip then picks the regular user pool node, so an
# eviction never changes the demo's URLs. NodePorts are reachable through any node.
# AKS taints Spot nodes; infrastructure/kyverno-manifests/ adds the matching toleration to all pods.
# On eviction the node is deleted and the autoscaler replaces it once Spot capacity is available.
resource "azurerm_kubernetes_cluster_node_pool" "spot" {
  count                 = var.spot ? 1 : 0
  name                  = "spotpool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.node_vm_size
  priority              = "Spot"
  eviction_policy       = "Delete"
  spot_max_price        = -1
  node_taints           = ["kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
  auto_scaling_enabled  = true
  node_count            = var.spot_node_count
  min_count             = var.spot_node_count
  max_count             = var.spot_node_count
  os_disk_size_gb       = 128
  os_disk_type          = "Ephemeral"
  vnet_subnet_id        = azurerm_subnet.aks.id
  max_pods              = 100
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
