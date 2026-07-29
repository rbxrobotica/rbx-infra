# PowerDNS API — reached via SSH tunnel to pantera
# ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33
#
# Credentials are NOT passed as Terraform variables.
# The provider reads PDNS_SERVER_URL and PDNS_API_KEY directly from env.
# Use scripts/dns-tofu-env.sh as the entrypoint for all tofu invocations.

# Cluster ingress IP (tiger)
variable "k3s_ingress_ip" {
  description = "Public IP of the k3s cluster ingress"
  type        = string
  default     = "158.220.116.31"
}

# DKIM CNAMEs, filled after Postmark domain setup.
# Leave empty ("") to skip record creation until values are available.
#
# WARNING, verified live 2026-07-29: every variable below is still "" and unset
# in terraform.tfvars, so all five `pm._domainkey.*` resources have count = 0 and
# have NEVER been created. The DKIM that actually signs RBX mail today was made
# outside Terraform and does not match this model in any respect:
#
#   modelled here:  CNAME  pm._domainkey.rbxsystems.ch
#   actually live:  TXT    20260503181522pm._domainkey.rbxsystems.ch
#
# Postmark generates a timestamp-based selector per domain and serves the key as
# a TXT record. Filling these variables would therefore NOT adopt the working
# record; it would publish a second, wrongly named CNAME beside it.
#
# To bring DKIM under Terraform, model the real selector and record type instead
# of setting these. See docs/PLAN-dns-email-architecture.md, DKIM Configuration.

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

variable "dkim_rbx_ia_br" {
  description = "DKIM CNAME target for rbx.ia.br (from Postmark)"
  type        = string
  default     = ""
}
