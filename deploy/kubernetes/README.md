# Kubernetes

A kustomize base for bench-recon. It's the shape the maintainer's own cluster
runs, without anything specific to that cluster: namespace, storage class,
exposure and image version are yours to set in an overlay. (The maintainer's own
overlay — NFS PVCs, a nightly backup CronJob, a pinned LAN LoadBalancer IP, Flux
image automation — lives in `cluster-management/apps/bench-recon`.)

## Before you apply

1. **Pull access.** The image is a private GHCR package. Create a pull secret in
   your namespace and add it in your overlay (see the example below).
2. **The Secret.** Fill in `secret.example.yaml` and apply it by hand. The app
   won't start without `BENCHRECON_PASSWORD_HASH`.

## An overlay

```yaml
# overlays/bench/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: bench-recon
resources:
  - https://github.com/IamMrCupp/bench-recon//deploy/kubernetes?ref=main
images:
  - name: ghcr.io/iammrcupp/bench-recon
    newTag: "0.3"                         # pin a release
patches:
  - target: {kind: Deployment, name: bench-recon}
    patch: |-
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value: [{name: ghcr-pull}]
      # The volume is NFS: use the NFS-safe SQLite settings.
      - op: replace
        path: /spec/template/spec/containers/0/env/2/value
        value: "1"
  - target: {kind: PersistentVolumeClaim, name: bench-recon-data}
    patch: |-
      - op: add
        path: /spec/storageClassName
        value: nfs-client
  # LAN only: a fixed LoadBalancer IP, never a public ingress.
  - target: {kind: Service, name: bench-recon}
    patch: |-
      - op: add
        path: /spec/type
        value: LoadBalancer
      - op: add
        path: /spec/loadBalancerIP
        value: 192.0.2.50  # placeholder: a free address on your LAN
```

Then `kubectl apply -k overlays/bench`.

## What not to change

- **`replicas: 1` and `strategy: Recreate`.** SQLite takes exactly one writer,
  and the login rate limiter lives in one process's memory.
- **`BENCHRECON_SQLITE_NFS=1` on NFS.** The default WAL journal corrupts on NFS
  instead of failing.
- **No public exposure.** This service holds sensitive engagement records —
  client and target identity, scope, findings, credentials.

Backups aren't in the base. A SQLite `.backup` plus a copy of the artifact blobs,
from a CronJob to a second volume, is the pattern the maintainer's cluster uses.
