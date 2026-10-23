package main

import (
	"net/http"
	"testing"
)

func TestFuseRRFOrder(t *testing.T) {
	lex := []hit{{DocID: "a"}, {DocID: "b"}, {DocID: "c"}}
	vec := []hit{{DocID: "b"}, {DocID: "a"}, {DocID: "d"}}
	out := fuseRRF(lex, vec)
	if len(out) != 4 {
		t.Fatalf("want 4 merged hits, got %d", len(out))
	}
	// a: 1/61 + 1/62 ; b: 1/62 + 1/61 -> a and b tie, doc_id order breaks the tie.
	if out[0].DocID != "a" || out[1].DocID != "b" {
		t.Fatalf("unexpected order: %s, %s", out[0].DocID, out[1].DocID)
	}
	// d appears in one arm at rank 3: must score below a/b but above c (rank3 in one arm too, tie -> doc order c<d)
	if out[2].DocID != "c" || out[3].DocID != "d" {
		t.Fatalf("unexpected tail order: %s, %s", out[2].DocID, out[3].DocID)
	}
}

func TestFuseRRFEmpty(t *testing.T) {
	if out := fuseRRF(nil, nil); len(out) != 0 {
		t.Fatalf("want empty, got %d", len(out))
	}
}

func TestTenantFor(t *testing.T) {
	s := &server{keys: map[string]string{"fixture-key-acme": "acme-cooking"}}
	r, _ := http.NewRequest("GET", "/", nil)
	if _, ok := s.tenantFor(r); ok {
		t.Fatal("missing key must not authenticate")
	}
	r.Header.Set("X-Api-Key", "wrong")
	if _, ok := s.tenantFor(r); ok {
		t.Fatal("wrong key must not authenticate")
	}
	r.Header.Set("X-Api-Key", "fixture-key-acme")
	tenant, ok := s.tenantFor(r)
	if !ok || tenant != "acme-cooking" {
		t.Fatalf("want acme-cooking, got %q ok=%v", tenant, ok)
	}
}
