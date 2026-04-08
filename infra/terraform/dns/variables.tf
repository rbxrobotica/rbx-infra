# PowerDNS API — reached via SSH tunnel to pantera
# ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33

variable "pdns_api_url" {
  description = "PowerDNS API endpoint (via SSH tunnel to pantera)"
  type        = string
  default     = "http://127.0.0.1:18081"
}

variable "pdns_api_key" {
  description = "PowerDNS API key (matches pdns_api_key in Ansible vault)"
  type        = string
  sensitive   = true
}

# Cluster ingress IP (tiger)
variable "k3s_ingress_ip" {
  description = "Public IP of the k3s cluster ingress"
  type        = string
  default     = "158.220.116.31"
}

# DKIM CNAMEs — filled after Postmark domain setup
# Leave empty ("") to skip record creation until values are available

variable "dkim_rbxsystems_ch" {
  description = "DKIM CNAME target for rbxsystems.ch (from Postmark)"
  type        = string
  default     = ""
}

variable "dkim_tx_rbxsystems_ch" {
  description = "DKIM CNAME target for tx.rbxsystems.ch (from Postmark)"
  type        = string
  default     = ""
}

variable "dkim_strategos_gr" {
  description = "DKIM CNAME target for strategos.gr (from Postmark)"
  type        = string
  default     = ""
}

variable "dkim_tx_strategos_gr" {
  description = "DKIM CNAME target for tx.strategos.gr (from Postmark)"
  type        = string
  default     = ""
}
