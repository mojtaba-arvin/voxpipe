package httpapi

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

var (
	ErrUploadTooLarge = errors.New("upload exceeds configured limit")
	ErrUploadIntegrity = errors.New("upload size or checksum does not match declaration")
	ErrUploadConflict = errors.New("staged upload already exists with different content")
)

type LocalStager struct {
	root string
}

func NewLocalStager(root string) (*LocalStager, error) {
	if strings.TrimSpace(root) == "" {
		return nil, fmt.Errorf("staging root is required")
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, fmt.Errorf("create staging root: %w", err)
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve staging root: %w", err)
	}
	info, err := os.Lstat(absolute)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, fmt.Errorf("staging root must be a real directory")
	}
	if err := os.Chmod(absolute, 0700); err != nil {
		return nil, fmt.Errorf("secure staging root: %w", err)
	}
	return &LocalStager{root: absolute}, nil
}

func (s *LocalStager) Reference(tenantID, sessionID string) (string, error) {
	if !isUUID(tenantID) || !isUUID(sessionID) {
		return "", fmt.Errorf("tenant and session identifiers must be UUIDs")
	}
	return filepath.ToSlash(filepath.Join(tenantID, sessionID, "source")), nil
}

func (s *LocalStager) Write(
	tenantID, sessionID string,
	source io.Reader,
	expectedSize, maxSize int64,
	expectedSHA256 string,
) (int64, string, error) {
	if expectedSize < 1 || maxSize < 1 || expectedSize > maxSize {
		return 0, "", ErrUploadTooLarge
	}
	if len(expectedSHA256) != sha256.Size*2 {
		return 0, "", ErrUploadIntegrity
	}
	if _, err := hex.DecodeString(expectedSHA256); err != nil {
		return 0, "", ErrUploadIntegrity
	}
	directory, target, reference, err := s.paths(tenantID, sessionID)
	if err != nil {
		return 0, "", err
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return 0, "", fmt.Errorf("create private session staging directory: %w", err)
	}
	if err := s.rejectSymlinkPath(directory); err != nil {
		return 0, "", err
	}

	temporary, err := os.CreateTemp(directory, ".upload-*.part")
	if err != nil {
		return 0, "", fmt.Errorf("create upload staging file: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0600); err != nil {
		temporary.Close()
		return 0, "", fmt.Errorf("secure upload staging file: %w", err)
	}

	digest := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temporary, digest), io.LimitReader(source, maxSize+1))
	if copyErr != nil {
		temporary.Close()
		return 0, "", fmt.Errorf("stream upload: %w", copyErr)
	}
	if written > maxSize {
		temporary.Close()
		return 0, "", ErrUploadTooLarge
	}
	if written != expectedSize || hex.EncodeToString(digest.Sum(nil)) != strings.ToLower(expectedSHA256) {
		temporary.Close()
		return 0, "", ErrUploadIntegrity
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return 0, "", fmt.Errorf("sync upload staging file: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return 0, "", fmt.Errorf("close upload staging file: %w", err)
	}

	// Link is an atomic create-if-absent publication. Concurrent retries cannot
	// overwrite bytes already published for this session.
	if err := os.Link(temporaryPath, target); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return 0, "", fmt.Errorf("publish staged upload: %w", err)
		}
		existingSize, existingDigest, verifyErr := s.Verify(tenantID, sessionID)
		if verifyErr != nil || existingSize != expectedSize ||
			existingDigest != strings.ToLower(expectedSHA256) {
			return 0, "", ErrUploadConflict
		}
	}
	if directoryHandle, openErr := os.Open(directory); openErr == nil {
		_ = directoryHandle.Sync()
		_ = directoryHandle.Close()
	}
	return written, reference, nil
}

func (s *LocalStager) Verify(tenantID, sessionID string) (int64, string, error) {
	_, target, _, err := s.paths(tenantID, sessionID)
	if err != nil {
		return 0, "", err
	}
	if err := s.rejectSymlinkPath(filepath.Dir(target)); err != nil {
		return 0, "", err
	}
	info, err := os.Lstat(target)
	if err != nil {
		return 0, "", err
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return 0, "", fmt.Errorf("staged upload is not a regular file")
	}
	file, err := os.Open(target)
	if err != nil {
		return 0, "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return 0, "", err
	}
	return info.Size(), hex.EncodeToString(digest.Sum(nil)), nil
}

func (s *LocalStager) paths(tenantID, sessionID string) (string, string, string, error) {
	reference, err := s.Reference(tenantID, sessionID)
	if err != nil {
		return "", "", "", err
	}
	directory := filepath.Join(s.root, tenantID, sessionID)
	target := filepath.Join(directory, "source")
	relative, relErr := filepath.Rel(s.root, target)
	if relErr != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return "", "", "", fmt.Errorf("staging path escaped configured root")
	}
	return directory, target, reference, nil
}

func (s *LocalStager) rejectSymlinkPath(directory string) error {
	relative, err := filepath.Rel(s.root, directory)
	if err != nil || relative == "." || strings.HasPrefix(relative, "..") {
		return fmt.Errorf("invalid staging directory")
	}
	current := s.root
	for _, part := range strings.Split(relative, string(os.PathSeparator)) {
		current = filepath.Join(current, part)
		info, statErr := os.Lstat(current)
		if statErr != nil {
			return statErr
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
			return fmt.Errorf("staging path contains a non-directory or symbolic link")
		}
	}
	return nil
}
