package engine

// Turbo is an engine that steps twice as far.
type Turbo struct {
	Engine
}

// Step doubles the step.
func (t *Turbo) Step(n int) int {
	return t.Engine.Step(n * 2)
}
