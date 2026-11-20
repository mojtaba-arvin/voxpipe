package httpapi

import (
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"
)

var ErrUploadTimeout = errors.New("upload deadline exceeded")

type boundedUploadReader struct {
	reader io.Reader
	controller *http.ResponseController
	started time.Time
	inactivityTimeout time.Duration
	maxDuration time.Duration
}

func (r *boundedUploadReader) Read(buffer []byte) (int, error) {
	now := time.Now()
	deadline := now.Add(r.inactivityTimeout)
	if r.maxDuration > 0 {
		absolute := r.started.Add(r.maxDuration)
		if !absolute.After(now) { return 0, ErrUploadTimeout }
		if absolute.Before(deadline) { deadline = absolute }
	}
	if err := r.controller.SetReadDeadline(deadline); err != nil {
		return 0, fmt.Errorf("%w: request read deadline is unavailable", ErrUploadTimeout)
	}
	n, err := r.reader.Read(buffer)
	var networkError net.Error
	if errors.As(err, &networkError) && networkError.Timeout() { return n, ErrUploadTimeout }
	return n, err
}
