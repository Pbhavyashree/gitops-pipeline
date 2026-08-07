# Every command needed to run this locally, in one place. Anything a new
# person has to reconstruct from a README is a step that will be got wrong.

.PHONY: help install test lint fmt run docker-build cluster-up cluster-down \
        ingress argocd argocd-password deploy-staging monitoring clean

CLUSTER := gitops-demo
IMAGE := task-api:local

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install Python dependencies
	pip install -r requirements-dev.txt

test: ## Run tests with coverage
	pytest --cov=app --cov-report=term-missing

lint: ## Lint and format check
	ruff check app tests
	ruff format --check app tests

fmt: ## Auto-format
	ruff format app tests
	ruff check --fix app tests

run: ## Run the API locally
	uvicorn app.main:app --reload --port 8000

docker-build: ## Build the container image
	docker build -t $(IMAGE) .

cluster-up: ## Create the kind cluster
	kind create cluster --config kind/cluster.yaml
	@echo "Cluster ready. Next: make ingress"

cluster-down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)

ingress: ## Install the nginx ingress controller
	kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
	kubectl wait --namespace ingress-nginx \
	  --for=condition=ready pod \
	  --selector=app.kubernetes.io/component=controller \
	  --timeout=180s

load-image: docker-build ## Build and load the image into kind
	kind load docker-image $(IMAGE) --name $(CLUSTER)

argocd: ## Install ArgoCD and register the applications
	kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
	kubectl wait --namespace argocd \
	  --for=condition=available deployment/argocd-server --timeout=300s
	kubectl apply -f argocd/project.yaml
	kubectl apply -f argocd/application-staging.yaml
	@echo "ArgoCD installed. Run 'make argocd-ui' to reach the dashboard."

argocd-ui: ## Port-forward the ArgoCD dashboard to localhost:8081
	@echo "Dashboard: https://localhost:8081  (user: admin)"
	kubectl port-forward svc/argocd-server -n argocd 8081:443

argocd-password: ## Print the initial ArgoCD admin password
	@kubectl -n argocd get secret argocd-initial-admin-secret \
	  -o jsonpath="{.data.password}" | base64 -d; echo

deploy-staging: ## Install the chart directly, bypassing ArgoCD
	helm upgrade --install task-api charts/task-api \
	  --namespace task-api-staging --create-namespace \
	  -f charts/task-api/values.yaml \
	  -f charts/task-api/values-staging.yaml \
	  --set image.repository=task-api --set image.tag=local \
	  --wait

monitoring: ## Install kube-prometheus-stack
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo update
	helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
	  --namespace monitoring --create-namespace --wait
	kubectl apply -f monitoring/prometheus-rules.yaml

grafana: ## Port-forward Grafana to localhost:3000 (admin/prom-operator)
	kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80

helm-lint: ## Lint and render the chart for every environment
	helm lint charts/task-api
	helm template task-api charts/task-api -f charts/task-api/values.yaml -f charts/task-api/values-staging.yaml > /dev/null
	helm template task-api charts/task-api -f charts/task-api/values.yaml -f charts/task-api/values-production.yaml > /dev/null
	@echo "Chart renders cleanly for all environments"

clean: ## Remove local build artefacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
