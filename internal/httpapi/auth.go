package httpapi

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
)

var ErrUnauthenticated = errors.New("unauthenticated")

type Principal struct {
	TenantID string
	Scopes   map[string]struct{}
}

func (p Principal) Allows(scope string) bool {
	_, ok := p.Scopes[scope]
	return ok
}

type Authenticator interface {
	Authenticate(bearerToken string) (Principal, error)
}

type tokenGrant struct {
	digest  [sha256.Size]byte
	tenant  string
	scopes  map[string]struct{}
}

type StaticTokenAuthenticator struct {
	grants []tokenGrant
}

// NewStaticTokenAuthenticator accepts SHA-256 token digests, never raw tokens.
// It is intended for controlled deployments and tests; production identity
// providers should implement Authenticator and return the same principal shape.
func NewStaticTokenAuthenticator(entries []string) (*StaticTokenAuthenticator, error) {
	auth := &StaticTokenAuthenticator{}
	seen := make(map[[sha256.Size]byte]struct{})
	for _, entry := range entries {
		parts := strings.SplitN(entry, ":", 3)
		if len(parts) != 3 || len(parts[0]) != sha256.Size*2 {
			return nil, fmt.Errorf("invalid token grant entry")
		}
		rawDigest, err := hex.DecodeString(parts[0])
		if err != nil || len(rawDigest) != sha256.Size {
			return nil, fmt.Errorf("invalid token digest")
		}
		if !isUUID(parts[1]) {
			return nil, fmt.Errorf("token grant tenant must be a UUID")
		}
		scopes := make(map[string]struct{})
		for _, scope := range strings.Split(parts[2], ",") {
			scope = strings.TrimSpace(scope)
			if scope != "" {
				if scope != "ingestion:read" && scope != "ingestion:write" { return nil, fmt.Errorf("unsupported token scope") }
				scopes[scope] = struct{}{}
			}
		}
		if len(scopes) == 0 {
			return nil, fmt.Errorf("token grant must include at least one scope")
		}
		var digest [sha256.Size]byte
		copy(digest[:], rawDigest)
		if _, exists := seen[digest]; exists { return nil, fmt.Errorf("duplicate token digest grant") }
		seen[digest] = struct{}{}
		auth.grants = append(auth.grants, tokenGrant{
			digest: digest,
			tenant: strings.ToLower(parts[1]),
			scopes: scopes,
		})
	}
	if len(auth.grants) == 0 {
		return nil, fmt.Errorf("at least one API token grant is required")
	}
	return auth, nil
}

// NewAuthenticatorFromEnv requires an explicit authentication mode. Static
// digest grants are retained only for controlled development/bootstrap use.
func NewAuthenticatorFromEnv() (Authenticator, error) {
	switch strings.TrimSpace(os.Getenv("VOXPIPE_AUTH_MODE")) {
	case "static":
		value := strings.TrimSpace(os.Getenv("VOXPIPE_API_TOKEN_HASHES"))
		if value == "" {
			return nil, fmt.Errorf("VOXPIPE_API_TOKEN_HASHES is required in static mode")
		}
		return NewStaticTokenAuthenticator(strings.Split(value, ";"))
	case "oidc":
		return NewOIDCAuthenticator(OIDCConfig{
			Issuer: os.Getenv("VOXPIPE_OIDC_ISSUER"),
			Audience: os.Getenv("VOXPIPE_OIDC_AUDIENCE"),
			JWKSURL: os.Getenv("VOXPIPE_OIDC_JWKS_URL"),
			IntrospectionURL: os.Getenv("VOXPIPE_OIDC_INTROSPECTION_URL"),
			ClientID: os.Getenv("VOXPIPE_OIDC_CLIENT_ID"),
			ClientSecret: os.Getenv("VOXPIPE_OIDC_CLIENT_SECRET"),
		})
	default:
		return nil, fmt.Errorf("VOXPIPE_AUTH_MODE must explicitly be oidc or static")
	}
}

func (a *StaticTokenAuthenticator) Authenticate(token string) (Principal, error) {
	if token == "" {
		return Principal{}, ErrUnauthenticated
	}
	candidate := sha256.Sum256([]byte(token))
	for _, grant := range a.grants {
		if subtle.ConstantTimeCompare(candidate[:], grant.digest[:]) == 1 {
			return Principal{TenantID: grant.tenant, Scopes: grant.scopes}, nil
		}
	}
	return Principal{}, ErrUnauthenticated
}

func isUUID(value string) bool {
	if len(value) != 36 {
		return false
	}
	for i, character := range value {
		switch i {
		case 8, 13, 18, 23:
			if character != '-' {
				return false
			}
		default:
			if !((character >= '0' && character <= '9') ||
				(character >= 'a' && character <= 'f') ||
				(character >= 'A' && character <= 'F')) {
				return false
			}
		}
	}
	return true
}
