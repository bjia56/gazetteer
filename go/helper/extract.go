package main

import (
	"bytes"
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
)

type Symbol struct {
	Kind    string   `json:"kind"`           // function, method or class (struct, interface and named types)
	Decl    string   `json:"decl,omitempty"` // struct, interface, alias or type
	Name    string   `json:"name"`
	Qual    string   `json:"qual"`            // Name, or Type.Name for methods
	Class   string   `json:"class,omitempty"` // type a method belongs to
	Line    int      `json:"line"`
	End     int      `json:"end"`
	Col     int      `json:"col"` // of the name, in UTF-16 units as language servers count
	Doc     string   `json:"doc"`
	Hash    string   `json:"h"`
	Private bool     `json:"private"`
	Sig     string   `json:"sig"`
	Code    string   `json:"code"`
	Bases   []string `json:"bases,omitempty"` // embedded types
}

type Const struct {
	Name  string `json:"name"`
	Line  int    `json:"line"`
	Value string `json:"value"`
}

type File struct {
	Path    string              `json:"path"`
	Dir     string              `json:"dir"`
	Package string              `json:"package"`
	Doc     string              `json:"doc"`
	Loc     int                 `json:"loc"`
	Symbols []Symbol            `json:"symbols"`
	Consts  []Const             `json:"consts"`
	Defs    []string            `json:"defs"`    // exported top-level names declared here
	Imports []string            `json:"imports"` // directories of packages of this module that the file imports
	Uses    map[string][]string `json:"uses"`    // directory -> names taken from that package
}

type ExtractOutput struct {
	Files  []File        `json:"files"`
	Errors []FileProblem `json:"errors"`
}

type FileProblem struct {
	Path  string `json:"path"`
	Error string `json:"error"`
}

type ExtractOptions struct {
	Root         string
	Dir          string
	Module       string
	Skips        []string
	SnippetLines int
}

var generatedRE = regexp.MustCompile(`(?m)^// Code generated .* DO NOT EDIT\.$`)

type parsed struct {
	rel  string
	src  []byte
	fset *token.FileSet
	file *ast.File
}

func parseFile(root, rel string) (*parsed, error) {
	src, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		return nil, err
	}
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, rel, src, parser.ParseComments)
	if err != nil {
		return nil, err
	}
	return &parsed{rel: rel, src: src, fset: fset, file: f}, nil
}

// generated reports the standard "Code generated ... DO NOT EDIT." header, which must precede the package clause.
func (p *parsed) generated() bool {
	for _, cg := range p.file.Comments {
		if cg.Pos() >= p.file.Package {
			break
		}
		for _, c := range cg.List {
			if generatedRE.MatchString(c.Text) {
				return true
			}
		}
	}
	return false
}

// ignored reports a "//go:build ignore" file: a generator or example that is not part of the package.
func (p *parsed) ignored() bool {
	for _, cg := range p.file.Comments {
		if cg.Pos() >= p.file.Package {
			break
		}
		for _, c := range cg.List {
			if strings.TrimSpace(c.Text) == "//go:build ignore" {
				return true
			}
		}
	}
	return false
}

func extractTree(o ExtractOptions) (*ExtractOutput, error) {
	var rels []string
	if err := walkGoFiles(o.Root, o.Dir, o.Skips, false, func(rel string) { rels = append(rels, rel) }); err != nil {
		return nil, err
	}
	out := &ExtractOutput{Files: []File{}, Errors: []FileProblem{}}
	var files []*parsed
	pkgNames := map[string]string{} // dir -> package name
	for _, rel := range rels {
		p, err := parseFile(o.Root, rel)
		if err != nil {
			out.Errors = append(out.Errors, FileProblem{Path: rel, Error: err.Error()})
			continue
		}
		if p.generated() || p.ignored() {
			continue
		}
		files = append(files, p)
		if dir := path.Dir(rel); pkgNames[dir] == "" {
			pkgNames[dir] = p.file.Name.Name
		}
	}
	for _, p := range files {
		out.Files = append(out.Files, describe(p, o, pkgNames))
	}
	return out, nil
}

func describe(p *parsed, o ExtractOptions, pkgNames map[string]string) File {
	lines := strings.Split(string(p.src), "\n")
	f := File{
		Path:    p.rel,
		Dir:     path.Dir(p.rel),
		Package: p.file.Name.Name,
		Doc:     docText(p.file.Doc),
		Loc:     len(lines),
		Symbols: []Symbol{},
		Consts:  []Const{},
		Defs:    []string{},
		Imports: []string{},
		Uses:    map[string][]string{},
	}
	b := &builder{p: p, lines: lines, o: o, f: &f}
	for _, decl := range p.file.Decls {
		switch d := decl.(type) {
		case *ast.FuncDecl:
			b.funcDecl(d)
		case *ast.GenDecl:
			b.genDecl(d)
		}
	}
	f.Imports, f.Uses = b.imports(pkgNames)
	return f
}

type builder struct {
	p     *parsed
	lines []string
	o     ExtractOptions
	f     *File
}

func docText(cg *ast.CommentGroup) string {
	if cg == nil {
		return ""
	}
	return strings.TrimSpace(cg.Text())
}

var space = regexp.MustCompile(`\s+`)

func (b *builder) expr(e ast.Node) string {
	var buf bytes.Buffer
	if err := printer.Fprint(&buf, b.p.fset, e); err != nil {
		return ""
	}
	return space.ReplaceAllString(buf.String(), " ")
}

func (b *builder) fields(fl *ast.FieldList) string {
	if fl == nil {
		return ""
	}
	var parts []string
	for _, f := range fl.List {
		var names []string
		for _, n := range f.Names {
			names = append(names, n.Name)
		}
		t := b.expr(f.Type)
		if len(names) > 0 {
			t = strings.Join(names, ", ") + " " + t
		}
		parts = append(parts, t)
	}
	return strings.Join(parts, ", ")
}

func (b *builder) signature(name string, tparams *ast.FieldList, ft *ast.FuncType) string {
	s := name
	if tparams != nil {
		s += "[" + b.fields(tparams) + "]"
	}
	s += "(" + b.fields(ft.Params) + ")"
	if r := ft.Results; r != nil && len(r.List) > 0 {
		if len(r.List) == 1 && len(r.List[0].Names) == 0 {
			s += " " + b.expr(r.List[0].Type)
		} else {
			s += " (" + b.fields(r) + ")"
		}
	}
	return s
}

// utf16Col is the column, in UTF-16 units, of the byte offset col (1-based, as token.Position reports) of a line.
func utf16Col(line string, col int) int {
	if col-1 > len(line) {
		col = len(line) + 1
	}
	return len(utf16.Encode([]rune(line[:col-1])))
}

func (b *builder) symbol(kind, name, qual string, start, end token.Pos, namePos token.Pos, doc string) Symbol {
	sp, ep, np := b.p.fset.Position(start), b.p.fset.Position(end), b.p.fset.Position(namePos)
	chunk := b.lines[sp.Line-1 : ep.Line]
	h := sha1.Sum([]byte(strings.Join(chunk, "\n")))
	code := chunk
	if n := b.o.SnippetLines; n > 0 && len(chunk) > n {
		code = append(append([]string{}, chunk[:n]...), fmt.Sprintf("// ... %d more lines", len(chunk)-n))
	}
	return Symbol{
		Kind: kind, Name: name, Qual: qual, Line: sp.Line, End: ep.Line,
		Col:     utf16Col(b.lines[np.Line-1], np.Column),
		Doc:     doc,
		Hash:    hex.EncodeToString(h[:])[:12],
		Private: !ast.IsExported(name),
		Code:    strings.Join(code, "\n"),
	}
}

// receiverType is the name of the type a method is declared on: T for func (t T), (t *T) and (t *T[K]).
func receiverType(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.StarExpr:
		return receiverType(t.X)
	case *ast.IndexExpr:
		return receiverType(t.X)
	case *ast.IndexListExpr:
		return receiverType(t.X)
	case *ast.ParenExpr:
		return receiverType(t.X)
	case *ast.Ident:
		return t.Name
	}
	return ""
}

func (b *builder) funcDecl(d *ast.FuncDecl) {
	name := d.Name.Name
	if name == "_" {
		return
	}
	kind, qual, class, sig := "function", name, "", ""
	if d.Recv != nil && len(d.Recv.List) > 0 {
		class = receiverType(d.Recv.List[0].Type)
		kind, qual = "method", class+"."+name
		sig = "func (" + b.fields(d.Recv) + ") " + b.signature(name, d.Type.TypeParams, d.Type)
	} else {
		sig = "func " + b.signature(name, d.Type.TypeParams, d.Type)
		if ast.IsExported(name) {
			b.f.Defs = append(b.f.Defs, name)
		}
	}
	s := b.symbol(kind, name, qual, d.Pos(), d.End(), d.Name.Pos(), docText(d.Doc))
	s.Class, s.Sig = class, sig
	b.f.Symbols = append(b.f.Symbols, s)
}

func (b *builder) genDecl(d *ast.GenDecl) {
	switch d.Tok {
	case token.TYPE:
		for _, spec := range d.Specs {
			b.typeSpec(d, spec.(*ast.TypeSpec))
		}
	case token.CONST, token.VAR:
		for _, spec := range d.Specs {
			vs := spec.(*ast.ValueSpec)
			for i, n := range vs.Names {
				if !ast.IsExported(n.Name) {
					continue
				}
				b.f.Defs = append(b.f.Defs, n.Name)
				if d.Tok == token.CONST {
					value := ""
					if i < len(vs.Values) {
						value = b.expr(vs.Values[i])
					}
					if len(value) > 90 {
						value = value[:90]
					}
					b.f.Consts = append(b.f.Consts, Const{Name: n.Name, Line: b.p.fset.Position(n.Pos()).Line, Value: value})
				}
			}
		}
	}
}

// embedded is the type name of an embedded field, without pointer or type arguments; "" for anything else
// (a union such as ~int | ~string is a constraint, not a base).
func embedded(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.StarExpr:
		return embedded(t.X)
	case *ast.IndexExpr:
		return embedded(t.X)
	case *ast.IndexListExpr:
		return embedded(t.X)
	case *ast.Ident:
		return t.Name
	case *ast.SelectorExpr:
		if x, ok := t.X.(*ast.Ident); ok {
			return x.Name + "." + t.Sel.Name
		}
	}
	return ""
}

func (b *builder) typeSpec(d *ast.GenDecl, ts *ast.TypeSpec) {
	name := ts.Name.Name
	if name == "_" {
		return
	}
	if ast.IsExported(name) {
		b.f.Defs = append(b.f.Defs, name)
	}
	start, doc := ts.Pos(), docText(ts.Doc)
	if !d.Lparen.IsValid() { // a lone "type T ..." declaration starts at the keyword and carries the doc comment
		start = d.Pos()
		if doc == "" {
			doc = docText(d.Doc)
		}
	}
	decl, under := "type", ""
	var bases []string
	var methods []Symbol
	switch t := ts.Type.(type) {
	case *ast.StructType:
		decl, under = "struct", "struct"
		for _, f := range t.Fields.List {
			if len(f.Names) == 0 {
				if n := embedded(f.Type); n != "" {
					bases = append(bases, n)
				}
			}
		}
	case *ast.InterfaceType:
		decl, under = "interface", "interface"
		for _, f := range t.Methods.List {
			if len(f.Names) == 0 {
				if n := embedded(f.Type); n != "" {
					bases = append(bases, n)
				}
				continue
			}
			ft, ok := f.Type.(*ast.FuncType)
			if !ok {
				continue
			}
			m := f.Names[0]
			s := b.symbol("method", m.Name, name+"."+m.Name, f.Pos(), f.End(), m.Pos(), docText(f.Doc))
			s.Class, s.Sig = name, b.signature(m.Name, nil, ft)
			methods = append(methods, s)
		}
	default:
		decl, under = "type", b.expr(ts.Type)
		if ts.Assign.IsValid() {
			decl, under = "alias", "= "+under
		}
		if len(under) > 100 {
			under = under[:100] + "..."
		}
	}
	sig := "type " + name
	if ts.TypeParams != nil {
		sig += "[" + b.fields(ts.TypeParams) + "]"
	}
	sig += " " + under
	s := b.symbol("class", name, name, start, ts.End(), ts.Name.Pos(), doc)
	s.Decl, s.Sig, s.Bases = decl, sig, bases
	b.f.Symbols = append(b.f.Symbols, s)
	b.f.Symbols = append(b.f.Symbols, methods...)
}

// imports resolves the file's imports of packages of this module (by directory) and the names it takes from
// each. Names are what the file spells as pkg.Name, which lets the caller tie an import to the files it uses.
func (b *builder) imports(pkgNames map[string]string) ([]string, map[string][]string) {
	local := map[string]string{} // name the file calls the package -> directory
	dirs := map[string]bool{}
	for _, im := range b.p.file.Imports {
		ip, err := strconv.Unquote(im.Path.Value)
		if err != nil {
			continue
		}
		var dir string
		switch {
		case ip == b.o.Module:
			dir = "."
		case strings.HasPrefix(ip, b.o.Module+"/"):
			dir = strings.TrimPrefix(ip, b.o.Module+"/")
		default:
			continue
		}
		dirs[dir] = true
		name := pkgNames[dir]
		if im.Name != nil {
			name = im.Name.Name
		}
		if name != "" && name != "_" && name != "." {
			local[name] = dir
		}
	}
	names := map[string]map[string]bool{}
	ast.Inspect(b.p.file, func(n ast.Node) bool {
		sel, ok := n.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		if x, ok := sel.X.(*ast.Ident); ok && x.Obj == nil {
			if dir, ok := local[x.Name]; ok {
				if names[dir] == nil {
					names[dir] = map[string]bool{}
				}
				names[dir][sel.Sel.Name] = true
			}
		}
		return true
	})
	imports := make([]string, 0, len(dirs))
	uses := map[string][]string{}
	for d := range dirs {
		imports = append(imports, d)
		ns := []string{}
		for n := range names[d] {
			ns = append(ns, n)
		}
		sort.Strings(ns)
		uses[d] = ns
	}
	sort.Strings(imports)
	return imports, uses
}
