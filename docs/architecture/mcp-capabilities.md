# MCP Capability Contract

Contract version: 1.0.0. Transport: stdio.

The MCP server is a thin authenticated adapter over the Go HTTP API. Tenant identity is derived from the bearer token; callers cannot supply tenant IDs.

| Tool | Scope | HTTP mapping | Retry behavior |
|---|---|---|---|
| create_ingestion | ingestion:write | POST /v1/ingestions | Reuse the same idempotency_key for safe retries. |
| get_ingestion_status | ingestion:read | GET /v1/ingestions/{session_id} | Retry transient transport and server errors with bounded backoff. |
| get_asset | ingestion:read | GET /v1/assets/{asset_id} | Retry transient transport and server errors with bounded backoff. |
| search_assets | ingestion:read | GET /v1/assets/search | Retry transient transport and server errors with bounded backoff. |

## Tool contracts

### create_ingestion

Create an authenticated batch ingestion session; upload bytes separately through the returned HTTP URL.

Required scope: ingestion:write.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "idempotency_key",
    "expected_size_bytes",
    "expected_sha256",
    "source"
  ],
  "properties": {
    "idempotency_key": {
      "type": "string",
      "minLength": 16,
      "maxLength": 128
    },
    "expected_size_bytes": {
      "type": "integer",
      "minimum": 1
    },
    "expected_sha256": {
      "type": "string",
      "pattern": "^[a-fA-F0-9]{64}$"
    },
    "content_type_hint": {
      "type": "string",
      "maxLength": 128
    },
    "source": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "kind"
      ],
      "properties": {
        "kind": {
          "type": "string",
          "enum": [
            "file_upload",
            "device_capture",
            "telephony",
            "live_stream",
            "import"
          ]
        },
        "external_reference": {
          "type": "string",
          "maxLength": 2048
        },
        "recorded_at": {
          "type": "string",
          "format": "date-time"
        },
        "declared_by": {
          "type": "string",
          "maxLength": 256
        }
      }
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "session_id",
    "state",
    "expected_size_bytes",
    "expected_sha256",
    "upload_url"
  ],
  "properties": {
    "session_id": {
      "type": "string",
      "format": "uuid"
    },
    "state": {
      "type": "string"
    },
    "expected_size_bytes": {
      "type": "integer"
    },
    "expected_sha256": {
      "type": "string"
    },
    "upload_url": {
      "type": "string"
    }
  }
}

Stable errors:
- INVALID_IDEMPOTENCY_KEY; retryable: false
- INVALID_REQUEST; retryable: false
- UPLOAD_SIZE_LIMIT; retryable: false
- INVALID_CHECKSUM; retryable: false
- INVALID_SOURCE; retryable: false
- IDEMPOTENCY_CONFLICT; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### get_ingestion_status

Get tenant-scoped ingestion session and durable job status.

Required scope: ingestion:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "session_id"
  ],
  "properties": {
    "session_id": {
      "type": "string",
      "format": "uuid"
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "session_id",
    "state",
    "created_at",
    "updated_at"
  ],
  "properties": {
    "session_id": {
      "type": "string",
      "format": "uuid"
    },
    "state": {
      "type": "string"
    },
    "job": {
      "type": "object"
    },
    "asset_id": {
      "type": "string",
      "format": "uuid"
    },
    "failure": {
      "type": "object"
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}

Stable errors:
- NOT_FOUND; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### get_asset

Get public asset metadata without tenant IDs or private storage references.

Required scope: ingestion:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "asset_id"
  ],
  "properties": {
    "asset_id": {
      "type": "string",
      "format": "uuid"
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "asset_id",
    "source",
    "media",
    "integrity",
    "state",
    "created_at",
    "updated_at"
  ],
  "properties": {
    "asset_id": {
      "type": "string",
      "format": "uuid"
    },
    "source": {
      "type": "object"
    },
    "media": {
      "type": "object"
    },
    "integrity": {
      "type": "object"
    },
    "state": {
      "type": "string"
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}

Stable errors:
- NOT_FOUND; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### search_assets

Search metadata by asset ID, source kind, MIME type, container format, or codec; not transcript or semantic search.

Required scope: ingestion:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "query"
  ],
  "properties": {
    "query": {
      "type": "string",
      "minLength": 2,
      "maxLength": 128
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "items",
    "limit"
  ],
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "limit": {
      "type": "integer"
    }
  }
}

Stable errors:
- INVALID_SEARCH_QUERY; retryable: false
- INVALID_SEARCH_LIMIT; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

## Security and search boundaries

- Tenant identity comes from the authenticated principal; caller-supplied tenant IDs are forbidden.
- Asset responses omit tenant identifiers and private storage/object references.
- search_assets performs metadata search only. Transcript, vector, and semantic search are not implemented.
- MCP creates ingestion sessions; audio bytes are transferred separately through the authenticated HTTP upload API.
