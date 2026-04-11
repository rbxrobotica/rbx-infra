terraform {
  required_version = ">= 1.5"

  required_providers {
    powerdns = {
      source  = "pan-net/powerdns"
      version = "~> 1.5"
    }
  }

  # State is local. terraform.tfstate is excluded from git.
  # Before running any plan/apply, open the SSH tunnel:
  #   ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33
}

provider "powerdns" {
  # server_url and api_key are read from PDNS_SERVER_URL and PDNS_API_KEY env vars.
  # Set them via scripts/dns-tofu-env.sh (reads PDNS_API_KEY from pass).
  # Never hardcode credentials here or in terraform.tfvars.
}
