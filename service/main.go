// vaultsearch — multi-tenant hybrid search API.
//
// One binary, two deployment modes (VAULT_MODE):
//
//	rls   — the database enforces isolation. Every request runs in a
//	        transaction that sets app.tenant_id from the authenticated
//	        tenant, and the SQL contains NO tenant predicate at all.
//	        FORCE ROW LEVEL SECURITY on documents does the work.
//	naive — the contrast deployment. The database has no RLS; isolation
//	        is the application filter `AND tenant_id = $tenant`, added
//	        to the primary search path and the get-by-id path. The
//	        /similar path was ported from the RLS codebase and carries
//	        no application filter — the classic retrofit gap the
//	        leakage suite measures. This asymmetry is deliberate and
//	        documented; it is how real multi-tenant leaks happen
//	        (a new query path that forgets the filter).
//
// In both modes the tenant identity comes ONLY from the API key.
// There is deliberately no tenant parameter anywhere in the API.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	armLimit = 50
	rrfK     = 60
)

type server struct {
	pool     *pgxpool.Pool
	mode     string // "rls" | "naive"
	keys     map[string]string
	embedURL string
}

type hit struct {
	DocID      string  `json:"doc_id"`
	TenantID   string  `json:"tenant_id"`
	Title      string  `json:"title"`
	Score      float64 `json:"score"`
	LexRank    int     `json:"lexical_rank,omitempty"`
	VecRank    int     `json:"vector_rank,omitempty"`
	ArmScore   float64 `json:"arm_score,omitempty"`
}

// fuseRRF merges ranked arms with Reciprocal Rank Fusion (k=60).
func fuseRRF(arms ...[]hit) []hit {
	merged := map[string]*hit{}
	for _, arm := range arms {
		for i, h := range arm {
			m, ok := merged[h.DocID]
			if !ok {
				cp := h
				m = &cp
				merged[h.DocID] = m
			}
			m.Score += 1.0 / float64(rrfK+i+1)
		}
	}
	out := make([]hit, 0, len(merged))
	for _, h := range merged {
		out = append(out, *h)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].DocID < out[j].DocID
	})
	return out
}

func (s *server) tenantFor(r *http.Request) (string, bool) {
	key := r.Header.Get("X-Api-Key")
	t, ok := s.keys[key]
	return t, ok && key != ""
}

// beginTenant starts a transaction and (in rls mode) sets the tenant
// context that the RLS policy reads. In naive mode the setting is still
// written (harmless: no policy consumes it) so both modes run identical
// session setup; only the SQL predicates differ.
func (s *server) beginTenant(ctx context.Context, tenant string) (pgx.Tx, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := tx.Exec(ctx, "SELECT set_config('app.tenant_id', $1, true)", tenant); err != nil {
		tx.Rollback(ctx)
		return nil, err
	}
	return tx, nil
}

func (s *server) embed(ctx context.Context, text string) (string, error) {
	body := strings.NewReader(fmt.Sprintf(`{"texts":[%s]}`, strconv.Quote(text)))
	req, err := http.NewRequestWithContext(ctx, "POST", s.embedURL, body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var out struct {
		Vectors [][]float64 `json:"vectors"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil || len(out.Vectors) != 1 {
		return "", fmt.Errorf("bad embed response")
	}
	parts := make([]string, len(out.Vectors[0]))
	for i, v := range out.Vectors[0] {
		parts[i] = strconv.FormatFloat(v, 'f', 6, 64)
	}
	return "[" + strings.Join(parts, ",") + "]", nil
}

// lexicalQuerySQL is the lexical arm, shared verbatim with the offline
// evaluation harness. Construction: the query text is stemmed to lexemes
// with to_tsvector and OR-combined into a tsquery. (AND-combined
// plainto_tsquery is the degenerate default for natural-language queries:
// it requires every query word — including meta words like "guide" — to
// appear in the document, and returns nothing. OR + ts_rank is the
// standard Postgres lexical baseline.) An all-stopword query degrades to
// an empty tsquery, which matches nothing.
const lexicalQuerySQL = `
	SELECT d.doc_id, d.tenant_id, d.title, ts_rank(d.tsv, q.tsq) AS score
	FROM documents d,
	LATERAL (SELECT COALESCE(to_tsquery('english',
	           (SELECT string_agg(lx, ' | ')
	            FROM unnest(tsvector_to_array(to_tsvector('english', $1))) AS lx)),
	           ''::tsquery) AS tsq) q
	WHERE d.tsv @@ q.tsq`

func (s *server) armLexical(ctx context.Context, tx pgx.Tx, tenant, query string, limit int) ([]hit, error) {
	sql := lexicalQuerySQL
	args := []any{query}
	if s.mode == "naive" {
		sql += ` AND d.tenant_id = $2`
		args = append(args, tenant)
	}
	sql += ` ORDER BY score DESC, d.doc_id LIMIT ` + strconv.Itoa(limit)
	return s.queryHits(ctx, tx, sql, args, true)
}

func (s *server) armVector(ctx context.Context, tx pgx.Tx, tenant, vec string, limit int) ([]hit, error) {
	sql := `SELECT doc_id, tenant_id, title, 1 - (embedding <=> $1::vector) AS score
	        FROM documents`
	args := []any{vec}
	if s.mode == "naive" {
		sql += ` WHERE tenant_id = $2`
		args = append(args, tenant)
	}
	sql += ` ORDER BY embedding <=> $1::vector, doc_id LIMIT ` + strconv.Itoa(limit)
	return s.queryHits(ctx, tx, sql, args, false)
}

func (s *server) queryHits(ctx context.Context, tx pgx.Tx, sql string, args []any, lexical bool) ([]hit, error) {
	rows, err := tx.Query(ctx, sql, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []hit
	rank := 0
	for rows.Next() {
		var h hit
		if err := rows.Scan(&h.DocID, &h.TenantID, &h.Title, &h.ArmScore); err != nil {
			return nil, err
		}
		rank++
		if lexical {
			h.LexRank = rank
		} else {
			h.VecRank = rank
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

type searchRequest struct {
	Query string `json:"query"`
	Mode  string `json:"mode"` // fused | lexical | vector
	K     int    `json:"k"`
}

func (s *server) handleSearch(w http.ResponseWriter, r *http.Request) {
	tenant, ok := s.tenantFor(r)
	if !ok {
		writeErr(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	var req searchRequest
	raw, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err := json.Unmarshal(raw, &req); err != nil || strings.TrimSpace(req.Query) == "" {
		writeErr(w, http.StatusBadRequest, "bad request")
		return
	}
	if req.K <= 0 || req.K > 50 {
		req.K = 10
	}
	if req.Mode == "" {
		req.Mode = "fused"
	}
	if req.Mode != "fused" && req.Mode != "lexical" && req.Mode != "vector" {
		writeErr(w, http.StatusBadRequest, "bad request")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	tx, err := s.beginTenant(ctx, tenant)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "internal")
		return
	}
	defer tx.Rollback(ctx)

	var results []hit
	switch req.Mode {
	case "lexical":
		results, err = s.armLexical(ctx, tx, tenant, req.Query, armLimit)
	case "vector":
		var vec string
		vec, err = s.embed(ctx, req.Query)
		if err == nil {
			results, err = s.armVector(ctx, tx, tenant, vec, armLimit)
		}
	case "fused":
		var lex, vec []hit
		var vecStr string
		vecStr, err = s.embed(ctx, req.Query)
		if err == nil {
			lex, err = s.armLexical(ctx, tx, tenant, req.Query, armLimit)
		}
		if err == nil {
			vec, err = s.armVector(ctx, tx, tenant, vecStr, armLimit)
		}
		if err == nil {
			byDoc := map[string]*hit{}
			merged := fuseRRF(lex, vec)
			for i := range merged {
				byDoc[merged[i].DocID] = &merged[i]
			}
			for _, h := range lex {
				if m, ok := byDoc[h.DocID]; ok {
					m.LexRank = h.LexRank
				}
			}
			for _, h := range vec {
				if m, ok := byDoc[h.DocID]; ok {
					m.VecRank = h.VecRank
				}
			}
			results = merged
		}
	}
	if err != nil {
		log.Printf("search error (tenant %s): %v", tenant, err)
		writeErr(w, http.StatusInternalServerError, "internal")
		return
	}
	if len(results) > req.K {
		results = results[:req.K]
	}
	if results == nil {
		results = []hit{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"tenant": tenant, "mode": req.Mode, "results": results})
}

func (s *server) handleGetDoc(w http.ResponseWriter, r *http.Request) {
	tenant, ok := s.tenantFor(r)
	if !ok {
		writeErr(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	docID := strings.TrimPrefix(r.URL.Path, "/documents/")
	if docID == "" || strings.Contains(docID, "/") {
		writeErr(w, http.StatusBadRequest, "bad request")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	tx, err := s.beginTenant(ctx, tenant)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "internal")
		return
	}
	defer tx.Rollback(ctx)
	sql := `SELECT doc_id, tenant_id, title, body FROM documents WHERE doc_id = $1`
	args := []any{docID}
	if s.mode == "naive" {
		sql += ` AND tenant_id = $2`
		args = append(args, tenant)
	}
	var d struct {
		DocID    string `json:"doc_id"`
		TenantID string `json:"tenant_id"`
		Title    string `json:"title"`
		Body     string `json:"body"`
	}
	err = tx.QueryRow(ctx, sql, args...).Scan(&d.DocID, &d.TenantID, &d.Title, &d.Body)
	if err != nil {
		// Not found and not-visible are deliberately indistinguishable.
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	writeJSON(w, http.StatusOK, d)
}

func (s *server) handleSimilar(w http.ResponseWriter, r *http.Request) {
	tenant, ok := s.tenantFor(r)
	if !ok {
		writeErr(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	docID := strings.TrimPrefix(r.URL.Path, "/similar/")
	k := 10
	if v := r.URL.Query().Get("k"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 50 {
			k = n
		}
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	tx, err := s.beginTenant(ctx, tenant)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "internal")
		return
	}
	defer tx.Rollback(ctx)

	// NOTE (the documented naive-mode gap): this path applies no tenant
	// predicate of its own in either mode. In rls mode the policy still
	// scopes both the seed lookup and the neighbour scan. In naive mode
	// nothing does — that is precisely the measured difference.
	var seed string
	err = tx.QueryRow(ctx, `SELECT embedding::text FROM documents WHERE doc_id = $1`, docID).Scan(&seed)
	if err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	results, err := s.queryHits(ctx, tx,
		`SELECT doc_id, tenant_id, title, 1 - (embedding <=> $1::vector) AS score
		 FROM documents WHERE doc_id <> $2
		 ORDER BY embedding <=> $1::vector, doc_id LIMIT `+strconv.Itoa(k),
		[]any{seed, docID}, false)
	if err != nil {
		log.Printf("similar error (tenant %s): %v", tenant, err)
		writeErr(w, http.StatusInternalServerError, "internal")
		return
	}
	if results == nil {
		results = []hit{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"tenant": tenant, "seed": docID, "results": results})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func main() {
	dsn := os.Getenv("VAULT_DSN")
	mode := os.Getenv("VAULT_MODE")
	if dsn == "" || (mode != "rls" && mode != "naive") {
		log.Fatal("VAULT_DSN and VAULT_MODE=rls|naive are required")
	}
	var keys map[string]string
	if err := json.Unmarshal([]byte(os.Getenv("VAULT_API_KEYS")), &keys); err != nil || len(keys) == 0 {
		log.Fatal("VAULT_API_KEYS must be a JSON object of apiKey -> tenant")
	}
	pool, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		log.Fatal(err)
	}
	s := &server{
		pool:     pool,
		mode:     mode,
		keys:     keys,
		embedURL: getenvDefault("EMBED_URL", "http://127.0.0.1:7433/embed"),
	}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /search", s.handleSearch)
	mux.HandleFunc("GET /documents/", s.handleGetDoc)
	mux.HandleFunc("GET /similar/", s.handleSimilar)
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "mode": mode})
	})
	addr := getenvDefault("VAULT_ADDR", "127.0.0.1:7443")
	log.Printf("vaultsearch service mode=%s on %s", mode, addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func getenvDefault(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
