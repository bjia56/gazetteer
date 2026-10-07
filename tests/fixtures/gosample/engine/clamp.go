package engine

// Clamp limits a value to Limit. 😀 emoji before the declaration keep the UTF-16 column honest.
/* 😀 */ func Clamp(value int) int {
	if value > Limit {
		return Limit
	}
	return value
}

// Reset puts the engine back to zero. It lives in another file than Engine.
func (e *Engine) Reset() {
	e.total = 0
}

func init() {}
func init() {}
