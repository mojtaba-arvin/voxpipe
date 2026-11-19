# Object Storage Boundary

## Port

`ObjectStoragePort` is a provider-neutral interface:

- `put(key, stream, expected_sha256, overwrite)` stores an immutable object and returns its key, size, and SHA-256 digest.
- `open(key)` returns a caller-owned binary stream.
- `head(key)` returns current size and digest.
- `delete(key)` removes an object and reports whether it existed.

Provider SDK classes, bucket clients, signed URLs, and vendor-specific metadata must not cross this boundary.

## Object key rules

- Keys are relative POSIX-style paths scoped by tenant and asset.
- Keys are opaque references, not URLs and never contain credentials.
- Absolute paths, drive prefixes, backslashes, empty segments, dot segments, and control characters are rejected.
- Tenant identity and key prefix must be derived by trusted application code, not accepted from a client as an authorization decision.

## Local filesystem adapter

`LocalFilesystemObjectStore` is for development and single-node deployments:

- Writes stream into a temporary file in the target directory.
- The file is flushed and fsynced before an atomic create-if-absent hard link or explicit atomic replace.
- Repeated writes of identical content to the same key are idempotent.
- Different content at an existing key conflicts unless overwrite is explicitly enabled.
- Optional expected SHA-256 is verified before publication.
- Reads and `head` calculate the current digest; callers can compare it with the asset integrity field.
- Temporary files are removed on failure.
- Symlinked parents/targets and path traversal are rejected.

The root directory must be owned by the service account and not writable by untrusted users. The adapter checks symlinks and resolved paths but is not a defense against a hostile process racing filesystem changes under a shared writable root. Production deployments should isolate the worker and use a dedicated object-storage adapter.

## Integrity and deletion

The storage adapter returns a content digest; the asset metadata stores that digest as the canonical integrity reference. Retrieval can compare the current object digest with the asset record before processing.

Deletion is one operation in a larger asset lifecycle. The application layer must coordinate deletion of object bytes, database metadata, transcripts, embeddings, caches, and provider copies. A local object delete does not imply that derived data has been deleted.
