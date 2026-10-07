package engine

import "testing"

func TestEngineStep(t *testing.T) {
	e := New()
	if got := e.Step(3); got != 3 {
		t.Fatalf("got %d", got)
	}
}

func TestClampLimits(t *testing.T) {
	if Clamp(100) != Limit {
		t.Fatal("not clamped")
	}
}
