# GitOps Delivery Pipeline

A complete path from commit to running workload on Kubernetes: FastAPI service,
Helm chart, ArgoCD reconciliation, GitHub Actions CI, and Prometheus alerting —
runnable end to end on a local kind cluster.

The application is small on purpose. The subject of this repository is the
delivery path around it.

## The idea

In a push-based pipeline, CI holds cluster credentials and runs `kubectl apply`.
That means the cluster's actual state lives in the history of whichever pipeline
ran last, nobody can tell what is deployed without asking the cluster, and a
manual `kubectl edit` during an incident silently becomes permanent.

GitOps inverts it. CI's last act is to write the new image tag into git. An
in-cluster agent watches the repository and reconciles the cluster to match.
Git becomes the record of what should be running, drift is corrected
automatically, and rollback is `git revert`.

```
commit → CI: lint, test, helm lint, build, scan
            ↓
      writes image tag into values-staging.yaml
            ↓
      ArgoCD notices the commit, syncs staging
            ↓
      tag v0.1.0 → production syncs (manual approval)
```

## Running it locally

Needs Docker, kind, kubectl and Helm.

```bash
make cluster-up      # 3-node kind cluster
make ingress         # nginx ingress controller
make load-image      # build image, load into kind
make deploy-staging  # install the chart
make monitoring      # Prometheus and Grafana
```

Then `curl localhost:8080/health`. `make help` lists everything.

For the full GitOps loop, `make argocd` installs ArgoCD and registers the
staging Application; `make argocd-ui` opens the dashboard.

Running just the app, without Kubernetes:

```bash
make install && make test && make run
```

## What is here

| Path | Purpose |
|---|---|
| `app/` | FastAPI service with probes, Prometheus metrics, structured logs |
| `tests/` | 18 tests, 97% coverage |
| `charts/task-api/` | Helm chart with per-environment values overlays |
| `argocd/` | AppProject and per-environment Applications |
| `kind/` | Local 3-node cluster definition |
| `monitoring/` | PrometheusRule alerting definitions |
| `.github/workflows/` | Test, lint, chart validation, build, scan, tag promotion |

## Design decisions

**Three probes, three jobs.** Liveness answers "is this process wedged?" and
deliberately checks nothing external — if it called the database, a database
blip would restart every healthy pod simultaneously and turn a partial outage
into a total one. Readiness answers "should this pod get traffic?" and is where
dependency checks belong, because failing it removes the pod from the Service
without killing it. Startup exists so a slow boot is not mistaken for a crash
loop.

**CPU and memory limits are set differently on purpose.** Memory has
`request == limit`, because memory is incompressible and exceeding the limit
means an OOM kill. CPU has a limit well above its request, because CPU is
compressible — throttling a latency-sensitive service to save a fraction of a
core is usually the wrong trade.

**`maxUnavailable: 0` during rollouts.** Capacity never drops below the desired
count while new pods prove themselves. The alternative takes healthy pods out
before knowing the replacement works.

**The config checksum annotation.** Editing a ConfigMap updates the mounted
values but leaves running processes on the old configuration. Hashing the
ConfigMap into a pod annotation means a config change rolls the pods, which is
the behaviour people assume is happening anyway.

**Image tags are commit SHAs, never `latest`.** A mutable tag makes a rollout
unreproducible and a rollback meaningless — you cannot redeploy "the version
from before" if both versions answer to the same name.

**Staging syncs automatically, production does not.** Staging's job is to always
reflect `main`, so drift is caught immediately. Production tracks a git tag and
requires a human to approve the sync, because deciding to ship is a different
decision from correcting drift, and automating both together is how something
nobody intended goes out at 2am.

**The ArgoCD AppProject is restrictive.** It whitelists specific repositories,
namespaces and resource kinds. Without it, any Application in the project could
create cluster-scoped resources anywhere, which makes the project boundary
decorative.

**Metric labels use route templates, not raw paths.** `/api/v1/tasks/{task_id}`
rather than the actual ID — a label per task would create unbounded time series
and is a well-known way to take down a Prometheus instance. There is a test
asserting this.

**Alerts are symptom-based.** Error rate, p95 latency, no available replicas.
There are many causes for a service being slow and users care about none of
them. p95 rather than mean, because an average of 100ms hides a tenth of users
waiting three seconds.

**Three nodes in the local cluster.** Pod anti-affinity and PodDisruptionBudgets
are silently meaningless on a single node, so a one-node cluster would let those
configurations look correct while doing nothing.

## Security

Containers run as an unprivileged user with a read-only root filesystem, all
capabilities dropped, and no privilege escalation. Writable paths are explicit
`emptyDir` mounts, so needing to write is a deliberate decision. Images are
built multi-stage so no compiler reaches the runtime image, and scanned with
Trivy on every push to main.

## What I would add next

- **Progressive delivery** with Argo Rollouts — canary a percentage of traffic
  and roll back automatically on an error-rate SLO breach, rather than watching
  a dashboard during deploys
- **Sealed Secrets or External Secrets**, so secrets can live in git safely;
  right now there are none, which conveniently avoids the question
- **A real datastore** with a migration job as a Helm hook, ordered before the
  Deployment rolls
- **Multi-cluster** via ArgoCD ApplicationSets, generating Applications per
  cluster rather than duplicating manifests
- **SLO-based alerting** with error budgets, replacing fixed thresholds that are
  guesses at what "too slow" means
