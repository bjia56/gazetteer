package main

import (
	"crypto/sha1"
	"encoding/hex"
	"go/ast"
	"sort"
	"strconv"
	"strings"
)

type TestFn struct {
	Name string   `json:"name"`
	Uses []string `json:"uses"`
}

type TestsResult struct {
	OK    bool     `json:"ok"`
	Tests []TestFn `json:"tests"`
}

type FuncEntry struct {
	Hash string   `json:"h"`
	Uses []string `json:"uses"`
	Name string   `json:"name"`
}

type TableResult struct {
	OK       bool                 `json:"ok"`
	Funcs    map[string]FuncEntry `json:"funcs"`
	Residual string               `json:"residual"`
}

type NamesResult struct {
	OK    bool     `json:"ok"`
	Names []string `json:"names"`
}

// idents is every name written in the subtree: identifiers, field and method selectors included.
func idents(n ast.Node) []string {
	seen := map[string]bool{}
	ast.Inspect(n, func(x ast.Node) bool {
		if id, ok := x.(*ast.Ident); ok && id.Name != "_" {
			seen[id.Name] = true
		}
		return true
	})
	out := make([]string, 0, len(seen))
	for s := range seen {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}

// isTestName follows go test: Test, Benchmark, Fuzz and Example, then anything but a lower-case letter.
func isTestName(name string) bool {
	for _, prefix := range []string{"Test", "Benchmark", "Fuzz", "Example"} {
		if strings.HasPrefix(name, prefix) {
			rest := name[len(prefix):]
			return rest == "" || !(rest[0] >= 'a' && rest[0] <= 'z')
		}
	}
	return false
}

func testsOf(root, rel string) TestsResult {
	p, err := parseFile(root, rel)
	if err != nil {
		return TestsResult{Tests: []TestFn{}}
	}
	res := TestsResult{OK: true, Tests: []TestFn{}}
	for _, d := range p.file.Decls {
		if fn, ok := d.(*ast.FuncDecl); ok && fn.Recv == nil && isTestName(fn.Name.Name) {
			res.Tests = append(res.Tests, TestFn{Name: fn.Name.Name, Uses: idents(fn)})
		}
	}
	return res
}

func funcKey(fn *ast.FuncDecl) string {
	if fn.Recv != nil && len(fn.Recv.List) > 0 {
		return receiverType(fn.Recv.List[0].Type) + "." + fn.Name.Name
	}
	return fn.Name.Name
}

func tableOf(root, rel string) TableResult {
	p, err := parseFile(root, rel)
	if err != nil {
		return TableResult{Funcs: map[string]FuncEntry{}, Residual: "syntax-error"}
	}
	res := TableResult{OK: true, Funcs: map[string]FuncEntry{}}
	type span struct{ a, b int }
	var spans []span
	count := map[string]int{}
	for _, d := range p.file.Decls {
		fn, ok := d.(*ast.FuncDecl)
		if !ok {
			continue
		}
		a, b := p.fset.Position(fn.Pos()).Offset, p.fset.Position(fn.End()).Offset
		spans = append(spans, span{a, b})
		key := funcKey(fn)
		if count[key]++; count[key] > 1 { // several init functions may share a file
			key += "#" + strconv.Itoa(count[key])
		}
		h := sha1.Sum(p.src[a:b])
		res.Funcs[key] = FuncEntry{Hash: hex.EncodeToString(h[:]), Uses: idents(fn), Name: fn.Name.Name}
	}
	// the file without its function declarations: what is left changing means more than single functions changed
	rest := make([]byte, 0, len(p.src))
	at := 0
	for _, s := range spans {
		rest = append(rest, p.src[at:s.a]...)
		at = s.b
	}
	rest = append(rest, p.src[at:]...)
	h := sha1.Sum(rest)
	res.Residual = hex.EncodeToString(h[:])
	return res
}

// namesOf is every name a file mentions, plus the last element of each import path (what imports usually bind).
func namesOf(root, rel string) NamesResult {
	p, err := parseFile(root, rel)
	if err != nil {
		return NamesResult{Names: []string{}}
	}
	seen := map[string]bool{}
	for _, n := range idents(p.file) {
		seen[n] = true
	}
	for _, im := range p.file.Imports {
		if ip, err := strconv.Unquote(im.Path.Value); err == nil {
			seen[ip[strings.LastIndex(ip, "/")+1:]] = true
		}
	}
	out := make([]string, 0, len(seen))
	for s := range seen {
		out = append(out, s)
	}
	sort.Strings(out)
	return NamesResult{OK: true, Names: out}
}
