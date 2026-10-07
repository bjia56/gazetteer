// Command run drives an engine.
package main

import (
	"fmt"

	"example.com/gosample/engine"
)

func main() {
	e := engine.New()
	fmt.Println(e.Step(engine.Limit + 1))
}
