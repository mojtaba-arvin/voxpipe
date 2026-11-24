package httpapi

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

type OIDCConfig struct {
	Issuer           string
	Audience         string
	JWKSURL          string
	IntrospectionURL string
	ClientID         string
	ClientSecret     string
	HTTPClient       *http.Client
	CacheTTL         time.Duration
	Clock            func() time.Time
}

type OIDCAuthenticator struct {
	issuer string
	audience string
	jwksURL string
	introspectionURL string
	clientID string
	clientSecret string
	client *http.Client
	cacheTTL time.Duration
	clock func() time.Time

	mu sync.Mutex
	keys map[string]*rsa.PublicKey
	keysExpire time.Time
	forcedRefreshAt time.Time
}

type oidcJWTHeader struct {
	Algorithm string `json:"alg"`
	KeyID string `json:"kid"`
	Type string `json:"typ"`
}

type oidcJWTClaims struct {
	Issuer string `json:"iss"`
	Audience json.RawMessage `json:"aud"`
	ExpiresAt int64 `json:"exp"`
	NotBefore int64 `json:"nbf"`
	TenantID string `json:"tenant_id"`
	Scope string `json:"scope"`
	AuthorizedParty string `json:"azp"`
}

type oidcJWKSet struct {
	Keys []struct {
		KeyType string `json:"kty"`
		Use string `json:"use"`
		Algorithm string `json:"alg"`
		KeyID string `json:"kid"`
		Modulus string `json:"n"`
		Exponent string `json:"e"`
	} `json:"keys"`
}

func NewOIDCAuthenticator(config OIDCConfig) (*OIDCAuthenticator, error) {
	if strings.TrimSpace(config.Issuer) == "" || strings.TrimSpace(config.Audience) == "" ||
		strings.TrimSpace(config.ClientID) == "" || strings.TrimSpace(config.ClientSecret) == "" {
		return nil, fmt.Errorf("OIDC issuer, audience, and introspection client credentials are required")
	}
	for name, value := range map[string]string{
		"issuer": config.Issuer, "JWKS URL": config.JWKSURL,
		"introspection URL": config.IntrospectionURL,
	} {
		parsed, err := url.ParseRequestURI(value)
		if err != nil || parsed.Host == "" || (parsed.Scheme != "https" && !isLoopbackHTTP(parsed)) {
			return nil, fmt.Errorf("OIDC %s must use HTTPS (HTTP is allowed only for loopback tests)", name)
		}
	}
	if config.HTTPClient == nil {
		config.HTTPClient = &http.Client{Timeout: 4 * time.Second}
	}
	if config.CacheTTL <= 0 {
		config.CacheTTL = 5 * time.Minute
	}
	if config.Clock == nil {
		config.Clock = time.Now
	}
	return &OIDCAuthenticator{
		issuer: config.Issuer,
		audience: config.Audience,
		jwksURL: config.JWKSURL,
		introspectionURL: config.IntrospectionURL,
		clientID: config.ClientID,
		clientSecret: config.ClientSecret,
		client: config.HTTPClient,
		cacheTTL: config.CacheTTL,
		clock: config.Clock,
		keys: make(map[string]*rsa.PublicKey),
	}, nil
}

func isLoopbackHTTP(parsed *url.URL) bool {
	if parsed.Scheme != "http" {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	return host == "localhost" || host == "127.0.0.1" || host == "::1"
}

func (a *OIDCAuthenticator) Authenticate(token string) (Principal, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || token == "" || len(token) > 32*1024 {
		return Principal{}, ErrUnauthenticated
	}
	headerBytes, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || len(headerBytes) > 4096 {
		return Principal{}, ErrUnauthenticated
	}
	var header oidcJWTHeader
	if json.Unmarshal(headerBytes, &header) != nil ||
		header.Algorithm != "RS256" || header.KeyID == "" ||
		(header.Type != "" && header.Type != "JWT") {
		return Principal{}, ErrUnauthenticated
	}
	claimsBytes, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || len(claimsBytes) > 16*1024 {
		return Principal{}, ErrUnauthenticated
	}
	var claims oidcJWTClaims
	if json.Unmarshal(claimsBytes, &claims) != nil {
		return Principal{}, ErrUnauthenticated
	}
	now := a.clock().Unix()
	if claims.Issuer != a.issuer ||
		!audienceContains(claims.Audience, a.audience, claims.AuthorizedParty) ||
		claims.ExpiresAt <= now-30 || claims.NotBefore > now+30 ||
		!isUUID(claims.TenantID) {
		return Principal{}, ErrUnauthenticated
	}
	publicKey, err := a.key(header.KeyID)
	if err != nil {
		return Principal{}, ErrUnauthenticated
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return Principal{}, ErrUnauthenticated
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(publicKey, crypto.SHA256, digest[:], signature); err != nil {
		publicKey, refreshErr := a.refreshKey(header.KeyID)
		if refreshErr != nil || rsa.VerifyPKCS1v15(publicKey, crypto.SHA256, digest[:], signature) != nil {
			return Principal{}, ErrUnauthenticated
		}
	}
	if err := a.introspect(token); err != nil {
		return Principal{}, ErrUnauthenticated
	}
	scopes := make(map[string]struct{})
	for _, scope := range strings.Fields(claims.Scope) {
		if scope == scopeIngestionRead || scope == scopeIngestionWrite || scope == scopeTranscriptRead {
			scopes[scope] = struct{}{}
		}
	}
	if len(scopes) == 0 {
		return Principal{}, ErrUnauthenticated
	}
	return Principal{TenantID: strings.ToLower(claims.TenantID), Scopes: scopes}, nil
}

func audienceContains(raw json.RawMessage, expected, authorizedParty string) bool {
	var single string
	if json.Unmarshal(raw, &single) == nil {
		return single == expected
	}
	var values []string
	if json.Unmarshal(raw, &values) != nil {
		return false
	}
	found := false
	for _, value := range values {
		if value == expected { found = true }
	}
	if !found { return false }
	return len(values) <= 1 || authorizedParty == expected
}

func (a *OIDCAuthenticator) key(keyID string) (*rsa.PublicKey, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	refreshed := false
	now := a.clock()
	if !now.Before(a.keysExpire) {
		if err := a.refreshKeysLocked(); err != nil { return nil, err }
		refreshed = true
	}
	if key := a.keys[keyID]; key != nil { return key, nil }
	// Unknown kids can trigger a forced refresh, but only once per cooldown
	// window so attacker-controlled key IDs cannot turn auth into a JWKS DoS.
	if !refreshed {
		if now.Sub(a.forcedRefreshAt) < 30*time.Second {
			return nil, errors.New("OIDC signing key is not in the cached key set")
		}
		a.forcedRefreshAt = now
		if err := a.refreshKeysLocked(); err != nil { return nil, err }
	}
	if key := a.keys[keyID]; key != nil { return key, nil }
	return nil, errors.New("OIDC signing key not found")
}

func (a *OIDCAuthenticator) refreshKey(keyID string) (*rsa.PublicKey, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	now := a.clock()
	if now.Sub(a.forcedRefreshAt) < 30*time.Second {
		return nil, errors.New("OIDC signing-key refresh is cooling down")
	}
	a.forcedRefreshAt = now
	if err := a.refreshKeysLocked(); err != nil { return nil, err }
	key := a.keys[keyID]
	if key == nil { return nil, errors.New("OIDC signing key not found after refresh") }
	return key, nil
}

func (a *OIDCAuthenticator) refreshKeysLocked() error {
	request, err := http.NewRequest(http.MethodGet, a.jwksURL, nil)
	if err != nil {
		return err
	}
	response, err := a.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("JWKS endpoint returned status %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20+1))
	if err != nil || len(body) > 1<<20 {
		return errors.New("JWKS response is invalid or too large")
	}
	var set oidcJWKSet
	if err := json.Unmarshal(body, &set); err != nil {
		return err
	}
	keys := make(map[string]*rsa.PublicKey)
	for _, item := range set.Keys {
		if item.KeyType != "RSA" || (item.Use != "" && item.Use != "sig") ||
			(item.Algorithm != "" && item.Algorithm != "RS256") || item.KeyID == "" {
			continue
		}
		modulus, errN := base64.RawURLEncoding.DecodeString(item.Modulus)
		exponent, errE := base64.RawURLEncoding.DecodeString(item.Exponent)
		if errN != nil || errE != nil || len(modulus) < 256 || len(exponent) == 0 || len(exponent) > 4 {
			continue
		}
		e := 0
		for _, value := range exponent {
			e = e<<8 | int(value)
		}
		n := new(big.Int).SetBytes(modulus)
		if e < 3 || e%2 == 0 || n.BitLen() < 2048 {
			continue
		}
		if _, duplicate := keys[item.KeyID]; duplicate {
			return errors.New("JWKS contains duplicate key identifiers")
		}
		keys[item.KeyID] = &rsa.PublicKey{N: n, E: e}
	}
	if len(keys) == 0 {
		return errors.New("JWKS contains no supported RSA signing keys")
	}
	a.keys = keys
	a.keysExpire = a.clock().Add(a.cacheTTL)
	return nil
}

func (a *OIDCAuthenticator) introspect(token string) error {
	form := url.Values{"token": []string{token}, "token_type_hint": []string{"access_token"}}
	request, err := http.NewRequest(http.MethodPost, a.introspectionURL, strings.NewReader(form.Encode()))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.SetBasicAuth(a.clientID, a.clientSecret)
	response, err := a.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("token introspection returned status %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 64*1024+1))
	if err != nil || len(body) > 64*1024 {
		return errors.New("token introspection response is invalid or too large")
	}
	var result struct {
		Active bool `json:"active"`
	}
	if json.Unmarshal(body, &result) != nil || !result.Active {
		return errors.New("access token is inactive")
	}
	return nil
}

func signOIDCTestToken(privateKey *rsa.PrivateKey, keyID string, claims map[string]any) (string, error) {
	header, err := json.Marshal(map[string]string{"alg": "RS256", "typ": "JWT", "kid": keyID})
	if err != nil {
		return "", err
	}
	payload, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	unsigned := base64.RawURLEncoding.EncodeToString(header) + "." +
		base64.RawURLEncoding.EncodeToString(payload)
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(nil, privateKey, crypto.SHA256, digest[:])
	if err != nil {
		return "", err
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}

