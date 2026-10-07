package main

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func write(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for rel, text := range files {
		p := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func extract(t *testing.T, files map[string]string, skips ...string) *ExtractOutput {
	t.Helper()
	root := t.TempDir()
	write(t, root, files)
	out, err := extractTree(ExtractOptions{Root: root, Dir: ".", Module: "example.com/m", Skips: skips, SnippetLines: 3})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func file(t *testing.T, out *ExtractOutput, path string) File {
	t.Helper()
	for _, f := range out.Files {
		if f.Path == path {
			return f
		}
	}
	t.Fatalf("no file %s in %v", path, paths(out))
	return File{}
}

func paths(out *ExtractOutput) []string {
	var ps []string
	for _, f := range out.Files {
		ps = append(ps, f.Path)
	}
	return ps
}

func sym(t *testing.T, f File, qual string) Symbol {
	t.Helper()
	for _, s := range f.Symbols {
		if s.Qual == qual {
			return s
		}
	}
	t.Fatalf("no symbol %s in %s", qual, f.Path)
	return Symbol{}
}

const engine = `// Package engine runs things.
package engine

// Limit caps a step.
const Limit = 10

const (
	A, b = 1, 2
)

// Stepper steps.
type Stepper interface {
	// Step advances.
	Step(n int) (int, error)
	fmt.Stringer
}

// Engine does the work.
type Engine struct {
	*Base
	sync.Mutex
	name string
}

type Base struct{}

type (
	// ID names an engine.
	ID int
	Alias = map[string]int
)

type Pair[K comparable, V any] struct{ k K }

// New makes an engine.
func New(name string, opts ...Option) *Engine { return nil }

// Step advances the engine.
func (e *Engine) Step(n int) (int, error) {
	return n, nil
}

func (p *Pair[K, V]) Get(k K) (v V, ok bool) { return }

func init() {}
func init() {}
`

func TestExtractSymbols(t *testing.T) {
	out := extract(t, map[string]string{"engine/engine.go": engine})
	f := file(t, out, "engine/engine.go")
	if f.Package != "engine" || f.Dir != "engine" || f.Doc != "Package engine runs things." {
		t.Fatalf("file facts: %+v", f)
	}
	want := map[string][2]string{
		"Stepper":      {"class", "type Stepper interface"},
		"Stepper.Step": {"method", "Step(n int) (int, error)"},
		"Engine":       {"class", "type Engine struct"},
		"ID":           {"class", "type ID int"},
		"Alias":        {"class", "type Alias = map[string]int"},
		"Pair":         {"class", "type Pair[K comparable, V any] struct"},
		"New":          {"function", "func New(name string, opts ...Option) *Engine"},
		"Engine.Step":  {"method", "func (e *Engine) Step(n int) (int, error)"},
		"Pair.Get":     {"method", "func (p *Pair[K, V]) Get(k K) (v V, ok bool)"},
	}
	for qual, w := range want {
		s := sym(t, f, qual)
		if s.Kind != w[0] || s.Sig != w[1] {
			t.Errorf("%s: got %s %q, want %s %q", qual, s.Kind, s.Sig, w[0], w[1])
		}
	}
	if s := sym(t, f, "Engine"); !reflect.DeepEqual(s.Bases, []string{"Base", "sync.Mutex"}) || s.Decl != "struct" {
		t.Errorf("Engine bases %v decl %s", s.Bases, s.Decl)
	}
	if s := sym(t, f, "Stepper"); !reflect.DeepEqual(s.Bases, []string{"fmt.Stringer"}) {
		t.Errorf("Stepper bases %v", s.Bases)
	}
	if s := sym(t, f, "Engine.Step"); s.Class != "Engine" || s.Doc != "Step advances the engine." || s.Private {
		t.Errorf("Engine.Step: %+v", s)
	}
	if s := sym(t, f, "Pair.Get"); s.Class != "Pair" {
		t.Errorf("generic receiver: %+v", s)
	}
	if s := sym(t, f, "ID"); s.Doc != "ID names an engine." || s.Decl != "type" {
		t.Errorf("ID: %+v", s)
	}
	if s := sym(t, f, "Alias"); s.Decl != "alias" {
		t.Errorf("Alias decl %s", s.Decl)
	}
	if s := sym(t, f, "Stepper.Step"); s.Class != "Stepper" || s.Doc != "Step advances." {
		t.Errorf("interface method: %+v", s)
	}
	inits := 0
	for _, s := range f.Symbols {
		if s.Qual == "init" {
			inits++
		}
	}
	if inits != 2 {
		t.Errorf("both init functions are kept, got %d", inits)
	}
	if !reflect.DeepEqual(f.Consts, []Const{{"Limit", 5, "10"}, {"A", 8, "1"}}) {
		t.Errorf("consts %+v", f.Consts)
	}
	for _, n := range []string{"Limit", "A", "New", "Engine", "Stepper", "ID", "Pair"} {
		found := false
		for _, d := range f.Defs {
			found = found || d == n
		}
		if !found {
			t.Errorf("Defs misses %s: %v", n, f.Defs)
		}
	}
}

func TestSnippetAndHash(t *testing.T) {
	out := extract(t, map[string]string{"a/a.go": "package a\n\nfunc F() {\n\tx := 1\n\ty := 2\n\t_ = x + y\n}\n"})
	s := sym(t, file(t, out, "a/a.go"), "F")
	if s.Line != 3 || s.End != 7 {
		t.Fatalf("span %d-%d", s.Line, s.End)
	}
	if s.Code != "func F() {\n\tx := 1\n\ty := 2\n// ... 2 more lines" {
		t.Errorf("code %q", s.Code)
	}
	again := extract(t, map[string]string{"a/a.go": "package a\n\n// now documented\nfunc F() {\n\tx := 1\n\ty := 2\n\t_ = x + y\n}\n"})
	if sym(t, file(t, again, "a/a.go"), "F").Hash != s.Hash {
		t.Error("a doc comment must not change the source hash")
	}
}

func TestColumnIsUTF16(t *testing.T) {
	// the emoji is two UTF-16 units but four bytes
	out := extract(t, map[string]string{"a/a.go": "package a\n\n/* \U0001F600 */ func Größe() {}\n"})
	s := sym(t, file(t, out, "a/a.go"), "Größe")
	if want := len("/* ")*1 + 2 + len(" */ func "); s.Col != want {
		t.Errorf("col %d, want %d", s.Col, want)
	}
}

func TestFilesThatAreNotCode(t *testing.T) {
	out := extract(t, map[string]string{
		"a/a.go":            "package a\nfunc A() {}\n",
		"a/a_test.go":       "package a\nfunc TestA() {}\n",
		"a/gen.go":          "// Code generated by tool. DO NOT EDIT.\n\npackage a\nfunc G() {}\n",
		"a/ignore.go":       "//go:build ignore\n\npackage main\nfunc main() {}\n",
		"a/broken.go":       "package a\nfunc (",
		"vendor/v/v.go":     "package v\n",
		"a/testdata/t.go":   "package t\n",
		"a/.hidden/h.go":    "package h\n",
		"a/_skip/s.go":      "package s\n",
		"nested/go.mod":     "module example.com/nested\n",
		"nested/n.go":       "package n\n",
		"a/zz.pb.go":        "package a\nfunc PB() {}\n",
		"a/notgo.txt":       "package a\n",
		"gen/keep/keep.go":  "package keep\n",
		"gen/skipme/gen.go": "package skipme\n",
	}, ".pb.go", "/gen/skipme/")
	got := strings.Join(paths(out), " ")
	if got != "a/a.go gen/keep/keep.go" && got != "gen/keep/keep.go a/a.go" {
		t.Errorf("files read: %s", got)
	}
	if len(out.Errors) != 1 || out.Errors[0].Path != "a/broken.go" {
		t.Errorf("errors: %+v", out.Errors)
	}
}

func TestImportsAreResolvedByName(t *testing.T) {
	out := extract(t, map[string]string{
		"engine/e.go": "package engine\nfunc Run() {}\nfunc Other() {}\n",
		"util/u.go":   "package helpers\nfunc Clamp() {}\n",
		"cmd/main.go": `package main

import (
	"fmt"
	"example.com/m/engine"
	h "example.com/m/util"
	"example.com/m"
	_ "example.com/m/side"
	"example.com/other/thing"
)

func main() {
	engine.Run()
	h.Clamp()
	fmt.Println(thing.X)
	engine := 3
	_ = engine
}
`,
	})
	f := file(t, out, "cmd/main.go")
	if !reflect.DeepEqual(f.Imports, []string{".", "engine", "side", "util"}) {
		t.Errorf("imports %v", f.Imports)
	}
	want := map[string][]string{".": {}, "engine": {"Run"}, "side": {}, "util": {"Clamp"}}
	if !reflect.DeepEqual(f.Uses, want) {
		t.Errorf("uses %v, want %v", f.Uses, want)
	}
}

func TestTestsSubcommand(t *testing.T) {
	root := t.TempDir()
	write(t, root, map[string]string{
		"a/a_test.go": `package a
import "testing"
func TestStep(t *testing.T) { t.Run("x", func(t *testing.T) { Clamp(New().Step(1)) }) }
func BenchmarkRun(b *testing.B) { Run() }
func Testify() {}
func helper(t *testing.T) { Hidden() }
func (s *S) TestMethod() {}
func TestA() {}
`,
		"a/bad_test.go": "package a\nfunc (",
	})
	res := testsOf(root, "a/a_test.go")
	var names []string
	for _, tf := range res.Tests {
		names = append(names, tf.Name)
	}
	if !res.OK || !reflect.DeepEqual(names, []string{"TestStep", "BenchmarkRun", "TestA"}) {
		t.Fatalf("tests %+v", res)
	}
	if uses := res.Tests[0].Uses; !contains(uses, "Clamp") || !contains(uses, "Step") || !contains(uses, "New") {
		t.Errorf("uses of TestStep (closures included): %v", uses)
	}
	if testsOf(root, "a/bad_test.go").OK {
		t.Error("an unparsable file is not ok")
	}
}

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

func TestTable(t *testing.T) {
	root := t.TempDir()
	base := "package a\n\nimport \"fmt\"\n\nvar X = 1\n\nfunc A() { fmt.Println(1) }\n\nfunc (t *T) B() { A() }\n"
	write(t, root, map[string]string{"a.go": base})
	before := tableOf(root, "a.go")
	if _, ok := before.Funcs["A"]; !ok || before.Funcs["T.B"].Name != "B" {
		t.Fatalf("funcs %+v", before.Funcs)
	}

	write(t, root, map[string]string{"a.go": strings.Replace(base, "Println(1)", "Println(2)", 1)})
	body := tableOf(root, "a.go")
	if body.Funcs["A"].Hash == before.Funcs["A"].Hash || body.Funcs["T.B"].Hash != before.Funcs["T.B"].Hash {
		t.Error("only A changed")
	}
	if body.Residual != before.Residual {
		t.Error("a body edit leaves the rest of the file alone")
	}

	write(t, root, map[string]string{"a.go": strings.Replace(base, "X = 1", "X = 2", 1)})
	if tableOf(root, "a.go").Residual == before.Residual {
		t.Error("a change outside functions moves the residual")
	}
	write(t, root, map[string]string{"a.go": "package a\nfunc ("})
	if r := tableOf(root, "a.go"); r.OK || r.Residual != "syntax-error" {
		t.Errorf("broken file: %+v", r)
	}
}

func TestNames(t *testing.T) {
	root := t.TempDir()
	write(t, root, map[string]string{"a.go": "package a\nimport \"net/http\"\nfunc F() { http.Get(x.y) }\n"})
	got := namesOf(root, "a.go").Names
	for _, n := range []string{"F", "http", "Get", "x", "y"} {
		if !contains(got, n) {
			t.Errorf("names %v miss %s", got, n)
		}
	}
}
