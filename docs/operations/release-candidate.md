# Release candidate procedure

## What the workflow produces

The `Release candidate artifacts` workflow is a build-only gate. It verifies that a tag commit is reachable from `main`, runs Python and Go validation, creates Linux amd64/arm64 API binaries and a Git source archive, generates a CycloneDX 1.6 SBOM from the Go module graph and installed Python validation environment, and writes SHA-256 checksums plus a release manifest.

Manual dispatch creates a `manual-<sha>` artifact set. Tag builds use the tag name as the version. Artifacts are retained by GitHub Actions for 30 days; they are not published as a GitHub Release.

## Verification before use

1. Confirm the exact tag points to a commit on `main`.
2. Inspect both Python and Go validation jobs in the release-candidate run.
3. Download the artifact and verify `sha256sum -c SHA256SUMS`.
4. Inspect the CycloneDX BOM and compare dependency inventory to `go.mod` and `requirements-dev.txt`.
5. Verify binary architecture and version manifest before deployment.

## Explicit limitations

This workflow does not build a container, sign artifacts, deploy, perform a canary, or execute rollback. VoxPipe has no configured deployment environment or production-like database migration target in this repository. Do not interpret a successful build as production readiness. Canary and rollback automation require deployment-specific health checks, immutable release identity, compatible database migrations, and a rehearsed restore path.
