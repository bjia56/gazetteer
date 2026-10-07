// Package engine runs steps.
package engine

// Limit is the largest value a step may produce.
const Limit = 10

// Stepper is anything that can take a step.
type Stepper interface {
	// Step advances by n.
	Step(n int) int
}

// Engine does the work.
type Engine struct {
	total int
}

// New returns an engine that starts at zero.
func New() *Engine {
	return &Engine{}
}

// Step advances the engine by n, clamped to Limit.
func (e *Engine) Step(n int) int {
	e.total += Clamp(n)
	return e.total
}
