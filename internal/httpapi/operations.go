package httpapi

import (
	"net/http"
	"strconv"
)

func (s *Server) getOperationalSummary(w http.ResponseWriter, r *http.Request) {
	principal, ok := s.principal(w, r, scopeOperationsRead)
	if !ok { return }
	for key, values := range r.URL.Query() {
		if key != "window_hours" || len(values) != 1 {
			writeError(w, http.StatusBadRequest, "INVALID_OPERATIONS_REQUEST", "Only one optional window_hours parameter is supported")
			return
		}
	}
	windowHours := 24
	if raw := r.URL.Query().Get("window_hours"); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value < 1 || value > 168 {
			writeError(w, http.StatusBadRequest, "INVALID_OPERATIONS_REQUEST", "window_hours must be between 1 and 168")
			return
		}
		windowHours = value
	}
	summary, err := s.store.GetOperationalSummary(r.Context(), principal.TenantID, windowHours)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "OPERATIONS_SUMMARY_UNAVAILABLE", "Operational summary is temporarily unavailable")
		return
	}
	writeJSON(w, http.StatusOK, summary)
}
