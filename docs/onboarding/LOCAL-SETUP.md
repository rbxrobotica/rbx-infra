# Local Setup

**Audience:** new RBX engineer — follow this after reading
`ENGINEER-DAY-ONE.md`.

This guide installs every tool needed to work with the RBX
infrastructure and application codebases on your workstation.

---

## Minimum toolchain

| Tool | Version | Purpose |
|------|---------|---------|
| git | 2.40+ | Version control |
| GitHub CLI (`gh`) | 2.40+ | PRs, workflow dispatch, package queries |
| kubectl | 1.29+ | Cluster access |
| kustomize | 5.x | Manifest composition (optional, kubectl -k works) |
| OpenTofu | 1.7+ | DNS record management |
| pass | 1.7+ | Secret store (GPG-encrypted) |
| GnuPG | 2.4+ | Decrypts `pass` entries |
| SSH client | any | VPS access, tunnels |
| Rust | 1.83+ | Robson v3 development (if touching backend) |
| Node.js | 20 LTS | Frontend development (if touching frontend) |
| pnpm | 9.x | Frontend package manager |

---

## Installation

### Linux (Ubuntu/Debian)

```bash
# System packages
sudo apt update && sudo apt install -y \
  git gnupg2 pass ssh openssh-client

# GitHub CLI
(type -p wget >/dev/null || sudo apt install wget -y) \
  && sudo mkdir -p -m 755 /etc/apt/keyrings \
  && out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  && cat $out | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
  && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli-stable.list >/dev/null \
  && sudo apt update && sudo apt install gh -y

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
  && sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# OpenTofu
curl -fsSL https://get.opentofu.org/tofu.gpg | sudo tee /usr/share/keyrings/tofu-archive-keyring.gpg >/dev/null \
  && echo "deb [signed-by=/usr/share/keyrings/tofu-archive-keyring.gpg] https://packages.opentofu.org/opentofu/tofu/any/ any main" | sudo tee /etc/apt/sources.list.d/opentofu.list >/dev/null \
  && sudo apt update && sudo apt install -y tofu

# Rust (if needed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Node + pnpm (if needed)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo corepack enable && corepack prepare pnpm@latest --activate
```

### macOS

```bash
brew install git gnupg pass gh kubectl tofu node pnpm
rustup-init  # from https://rustup.rs
```

---

## Authentication

### GitHub

```bash
gh auth login
# Choose: GitHub.com, HTTPS, login with web browser
# Verify:
gh auth status
```

### pass (secret store)

RBX uses `pass` with a shared GPG key. The operator will:

1. Export the GPG secret key and send it securely.
2. Share the `pass` git repository URL.

You then:

```bash
# Import GPG key (operator provides the file)
gpg --import ~/.gnupg/rbx-secret-key.gpg

# Trust the key
gpg --edit-key ldamasio@gmail.com
# > trust > 5 (ultimate) > quit

# Initialize pass with the shared store
pass init ldamasio@gmail.com
pass git clone <pass-repo-url> ~/.password-store

# Verify:
pass ls
# Should show: rbx/dns/pdns-api-key, rbx/dns/pdns-db-password, etc.
```

### SSH keys

Generate an ED25519 key and send the public key to the operator:

```bash
ssh-keygen -t ed25519 -C "your.email@rbx.ia.br" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
# Send this to the operator for pantera/eagle/tiger authorized_keys
```

### Kubernetes (kubeconfig)

The operator will provide a kubeconfig file. Place it at:

```bash
mkdir -p ~/.kube
cp <provided-file> ~/.kube/config-rbx
export KUBECONFIG=~/.kube/config-rbx
# Add to your shell profile:
echo 'export KUBECONFIG=~/.kube/config-rbx' >> ~/.bashrc

# Verify:
kubectl get nodes
# Should show: tiger, jaguar, altaica, sumatrae
```

---

## DNS operations setup

DNS records are managed via OpenTofu against the PowerDNS API on
pantera. The API is only reachable through an SSH tunnel.

```bash
# Clone rbx-infra
git clone git@github.com:rbxrobotica/rbx-infra.git ~/apps/rbx-infra

# Tunnel to pantera's PDNS API
ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33

# Set provider env vars
source ~/apps/rbx-infra/scripts/dns-tofu-env.sh

# Verify:
cd ~/apps/rbx-infra/infra/terraform/dns
tofu plan
```

See `docs/infra/DNS.md` for full DNS operations guide.

---

## Verification checklist

Run through these commands to confirm everything works:

```bash
# 1. GitHub
gh repo view rbxrobotica/rbx-infra --json name

# 2. Kubernetes
kubectl get nodes
kubectl get ns

# 3. pass
pass rbx/dns/pdns-api-key

# 4. SSH to DNS servers
ssh root@149.102.139.33 'hostname'   # pantera
ssh root@167.86.92.97 'hostname'     # eagle

# 5. OpenTofu (requires tunnel + env)
source ~/apps/rbx-infra/scripts/dns-tofu-env.sh
cd ~/apps/rbx-infra/infra/terraform/dns && tofu plan
```

All green? You are ready to work. Return to `ENGINEER-DAY-ONE.md`
for your first task.
