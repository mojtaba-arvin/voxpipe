package httpapi

import (
	"reflect"
	"testing"
)

func TestSanitizeAuditMetadataKeepsOnlyAllowlistedNonSensitiveFields(t *testing.T) {
	input := map[string]any{
		"from": "receiving",
		"to": "cancelled",
		"prior_state": "receiving",
		"staging_deleted": true,
		"asset_deleted": false,
		"object_deleted": true,
		"access_token": "secret-token",
		"authorization": "Bearer secret",
		"transcript": "private transcript",
		"audio": "raw audio bytes",
		"storage_path": "/private/path",
		"nested": map[string]any{"token": "secret"},
	}
	expected := map[string]any{
		"from": "receiving",
		"to": "cancelled",
		"prior_state": "receiving",
		"staging_deleted": true,
		"asset_deleted": false,
		"object_deleted": true,
	}
	if actual := sanitizeAuditMetadata(input); !reflect.DeepEqual(actual, expected) {
		t.Fatalf("unexpected sanitized audit metadata: %#v", actual)
	}
}

func TestSanitizeAuditErrorCodeRejectsFreeFormSensitiveText(t *testing.T) {
	if got := sanitizeAuditErrorCode("UPLOAD_CHECKSUM_MISMATCH"); got != "UPLOAD_CHECKSUM_MISMATCH" {
		t.Fatalf("stable error code changed: %q", got)
	}
	if got := sanitizeAuditErrorCode("token=secret"); got != "AUDIT_ERROR_REDACTED" {
		t.Fatalf("free-form error detail was not redacted: %q", got)
	}
	if got := sanitizeAuditErrorCode(""); got != "" {
		t.Fatalf("empty error code changed: %q", got)
	}
}
