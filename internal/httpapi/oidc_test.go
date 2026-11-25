package httpapi

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func jwksFor(key *rsa.PrivateKey, kid string) map[string]any {
	exponent := make([]byte, 4)
	e := key.PublicKey.E
	for i := 3; i >= 0; i-- {
		exponent[i] = byte(e)
		e >>= 8
	}
	for len(exponent) > 1 && exponent[0] == 0 {
		exponent = exponent[1:]
	}
	return map[string]any{"keys": []any{map[string]any{
		"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
		"n": base64.RawURLEncoding.EncodeToString(key.PublicKey.N.Bytes()),
		"e": base64.RawURLEncoding.EncodeToString(exponent),
	}}}
}

func oidcClaims(issuer, audience, tenant, scope string, expires, notBefore int64) map[string]any {
	return map[string]any{
		"iss": issuer, "aud": audience, "tenant_id": tenant,
		"scope": scope, "exp": expires, "nbf": notBefore,
	}
}

func TestOIDCAuthenticatorValidatesClaimsAndRevocation(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil { t.Fatal(err) }
	const issuer = "https://identity.example.test/"
	const tenant = "11111111-1111-4111-8111-111111111111"
	var mu sync.Mutex
	active := true
	jwks := jwksFor(key, "key-1")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/jwks":
			mu.Lock()
			defer mu.Unlock()
			_ = json.NewEncoder(w).Encode(jwks)
		case "/introspect":
			mu.Lock()
			defer mu.Unlock()
			if r.Method != http.MethodPost || r.FormValue("token") == "" {
				http.Error(w, "invalid", http.StatusBadRequest)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]bool{"active": active})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	auth, err := NewOIDCAuthenticator(OIDCConfig{
		Issuer: issuer, Audience: "voxpipe-api",
		JWKSURL: server.URL + "/jwks",
		IntrospectionURL: server.URL + "/introspect",
		ClientID: "voxpipe", ClientSecret: "test-secret",
	})
	if err != nil { t.Fatal(err) }

	now := time.Now().Unix()
	makeToken := func(iss, aud, tenantID, scope string, exp, nbf int64) string {
		t.Helper()
		token, signErr := signOIDCTestToken(key, "key-1", oidcClaims(iss, aud, tenantID, scope, exp, nbf))
		if signErr != nil { t.Fatal(signErr) }
		return token
	}
	valid := makeToken(issuer, "voxpipe-api", tenant, "ingestion:read ingestion:write transcript:read operations:read", now+300, now-10)
	principal, err := auth.Authenticate(valid)
	if err != nil { t.Fatalf("valid token rejected: %v", err) }
	if principal.TenantID != tenant || !principal.Allows(scopeIngestionRead) || !principal.Allows(scopeIngestionWrite) || !principal.Allows(scopeTranscriptRead) || !principal.Allows(scopeOperationsRead) {
		t.Fatalf("unexpected principal: %#v", principal)
	}

	for name, token := range map[string]string{
		"wrong issuer": makeToken("https://attacker.example", "voxpipe-api", tenant, "ingestion:read", now+300, now-10),
		"wrong audience": makeToken(issuer, "another-api", tenant, "ingestion:read", now+300, now-10),
		"expired": makeToken(issuer, "voxpipe-api", tenant, "ingestion:read", now-100, now-200),
		"not yet valid": makeToken(issuer, "voxpipe-api", tenant, "ingestion:read", now+300, now+120),
		"invalid tenant": makeToken(issuer, "voxpipe-api", "not-a-tenant", "ingestion:read", now+300, now-10),
		"missing scope": makeToken(issuer, "voxpipe-api", tenant, "profile", now+300, now-10),
		"multiple audiences without authorized party": func() string {
			claims := oidcClaims(issuer, "voxpipe-api", tenant, "ingestion:read", now+300, now-10)
			claims["aud"] = []string{"voxpipe-api", "another-api"}
			token, signErr := signOIDCTestToken(key, "key-1", claims)
			if signErr != nil { t.Fatal(signErr) }
			return token
		}(),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := auth.Authenticate(token); err == nil {
				t.Fatal("invalid token was accepted")
			}
		})
	}

	mu.Lock()
	active = false
	mu.Unlock()
	if _, err := auth.Authenticate(valid); err == nil {
		t.Fatal("revoked/inactive token was accepted")
	}
}

func TestOIDCAuthenticatorRefreshesKeysOnUnknownKeyID(t *testing.T) {
	first, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil { t.Fatal(err) }
	second, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil { t.Fatal(err) }
	const issuer = "https://identity.example.test"
	const tenant = "22222222-2222-4222-8222-222222222222"
	var mu sync.Mutex
	current := jwksFor(first, "first")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/jwks" {
			mu.Lock()
			defer mu.Unlock()
			_ = json.NewEncoder(w).Encode(current)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]bool{"active": true})
	}))
	defer server.Close()
	auth, err := NewOIDCAuthenticator(OIDCConfig{
		Issuer: issuer, Audience: "voxpipe-api", JWKSURL: server.URL + "/jwks",
		IntrospectionURL: server.URL + "/introspect", ClientID: "client",
		ClientSecret: "secret", CacheTTL: time.Hour,
	})
	if err != nil { t.Fatal(err) }
	now := time.Now().Unix()
	makeToken := func(key *rsa.PrivateKey, kid string) string {
		t.Helper()
		token, signErr := signOIDCTestToken(key, kid, oidcClaims(
			issuer, "voxpipe-api", tenant, "ingestion:read", now+300, now-10,
		))
		if signErr != nil { t.Fatal(signErr) }
		return token
	}
	if _, err := auth.Authenticate(makeToken(first, "first")); err != nil {
		t.Fatalf("initial signing key rejected: %v", err)
	}
	mu.Lock()
	current = jwksFor(second, "first")
	mu.Unlock()
	if _, err := auth.Authenticate(makeToken(second, "first")); err != nil {
		t.Fatalf("rotated signing key was not refreshed: %v", err)
	}
}

func TestOIDCAuthenticatorFailsClosedWhenIdentityProviderUnavailable(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil { t.Fatal(err) }
	auth, err := NewOIDCAuthenticator(OIDCConfig{
		Issuer: "https://identity.example.test", Audience: "voxpipe-api",
		JWKSURL: "http://127.0.0.1:1/jwks",
		IntrospectionURL: "http://127.0.0.1:1/introspect",
		ClientID: "client", ClientSecret: "secret",
		HTTPClient: &http.Client{Timeout: 100 * time.Millisecond},
	})
	if err != nil { t.Fatal(err) }
	now := time.Now().Unix()
	token, err := signOIDCTestToken(key, "key-1", oidcClaims(
		"https://identity.example.test", "voxpipe-api",
		"11111111-1111-4111-8111-111111111111", "ingestion:read", now+300, now-10,
	))
	if err != nil { t.Fatal(err) }
	if _, err := auth.Authenticate(token); err == nil {
		t.Fatal("identity-provider outage must fail closed")
	}
}

func TestOIDCConfigRejectsNonHTTPSProviderEndpoints(t *testing.T) {
	_, err := NewOIDCAuthenticator(OIDCConfig{
		Issuer: "http://identity.example.test", Audience: "voxpipe-api",
		JWKSURL: "https://identity.example.test/jwks",
		IntrospectionURL: "https://identity.example.test/introspect",
		ClientID: "client", ClientSecret: "secret",
	})
	if err == nil { t.Fatal("non-HTTPS issuer must be rejected") }
}
