# Makefile for rbx-infra
# Common operations for infrastructure management

.PHONY: help bootstrap validate lint k9s argocd-status provision update-ip

# Default target
help:
	@echo "RBX Infrastructure - Available Commands:"
	@echo ""
	@echo "  provision    - Provision cluster from scratch via Ansible (after VPS reinstall)"
	@echo "  update-ip    - Update firewall when your local IP changes"
	@echo "  bootstrap    - Initial cluster setup (install ArgoCD + root Application)"
	@echo "  validate     - Validate all Kubernetes manifests"
	@echo "  lint         - Lint YAML files"
	@echo "  k9s          - Open K9s terminal UI"
	@echo "  argocd-status - Show ArgoCD application status"
	@echo "  diff         - Show pending changes (argocd diff)"
	@echo ""

# Provision cluster from scratch (run after VPS reinstall)
provision:
	@echo "Running Ansible playbooks..."
	cd bootstrap/ansible && ansible-playbook -i inventory/hosts.yml site.yml

# Update firewall rule when your local IP changes
update-ip:
	$(eval NEW_IP4 := $(shell curl -4s ifconfig.me))
	$(eval NEW_IP6 := $(shell curl -6s ifconfig.me 2>/dev/null || echo ""))
	@echo "Updating kubectl firewall rules to $(NEW_IP4) / $(NEW_IP6)..."
	ssh root@158.220.116.31 "ufw delete allow from any to any port 6443 proto tcp 2>/dev/null; ufw allow from $(NEW_IP4) to any port 6443 proto tcp"
	@[ -n "$(NEW_IP6)" ] && ssh root@158.220.116.31 "ufw allow from $(NEW_IP6) to any port 6443 proto tcp" || true
	@echo "Done. kubectl access updated."

# Bootstrap a new cluster
bootstrap:
	@echo "Creating argocd namespace..."
	kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
	@echo ""
	@echo "Installing ArgoCD v2.11.3..."
	kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.11.3/manifests/install.yaml
	@echo ""
	@echo "Waiting for ArgoCD to be ready (may take ~2min)..."
	kubectl wait --for=condition=available --timeout=300s deployment/argocd-server -n argocd
	@echo ""
	@echo "Installing cert-manager (required before ArgoCD can sync ClusterIssuer)..."
	kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
	@echo ""
	@echo "Waiting for cert-manager to be ready..."
	kubectl wait --for=condition=available --timeout=180s deployment/cert-manager -n cert-manager
	kubectl wait --for=condition=available --timeout=180s deployment/cert-manager-webhook -n cert-manager
	@echo ""
	@echo "Applying AppProjects..."
	kubectl apply -f gitops/projects/
	@echo ""
	@echo "Applying root Application (App of Apps)..."
	kubectl apply -f gitops/app-of-apps/root.yml
	@echo ""
	@echo "Bootstrap complete! Access ArgoCD at: https://argocd.rbx.ia.br"
	@echo "Initial password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"

# Validate all manifests
validate:
	@echo "Validating Kubernetes manifests..."
	@find . -name "*.yml" -o -name "*.yaml" | grep -v ".github" | xargs kubeconform -summary

# Lint YAML files
lint:
	@echo "Linting YAML files..."
	yamllint -d '{extends: default, rules: {line-length: {max: 150}}}' .

# Open K9s
k9s:
	k9s

# ArgoCD status
argocd-status:
	@echo "ArgoCD Application Status:"
	@argocd app list 2>/dev/null || echo "argocd CLI not configured. Run: argocd login"

# Show pending changes
diff:
	argocd app diff root

# Create a new application
new-app:
	@read -p "Application name: " name; \
	mkdir -p apps/prod/$$name; \
	mkdir -p core/namespaces; \
	echo "Created apps/prod/$$name/ - add your manifests there"

# Sync all applications
sync:
	argocd app sync root

# Generate documentation
docs:
	@echo "Generating documentation..."
	@tree -L 3 -I '.git' . > docs/structure.txt
