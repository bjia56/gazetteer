// gazetteer-gohelper reads Go source for gazetteer: the facts the language server cannot give
// (symbols, signatures, doc comments, imports) and the per-test and per-function facts used to link tests
// and to rebuild incrementally. It prints one JSON document to stdout.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
)

var version = "dev"

type listFlag []string

func (l *listFlag) String() string     { return strings.Join(*l, ",") }
func (l *listFlag) Set(v string) error { *l = append(*l, v); return nil }

func emit(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	return enc.Encode(v)
}

func run(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: gazetteer-gohelper extract|tests|table|names|version ...")
	}
	cmd, rest := args[0], args[1:]
	if cmd == "version" {
		fmt.Println(version)
		return nil
	}
	fs := flag.NewFlagSet(cmd, flag.ContinueOnError)
	root := fs.String("root", ".", "directory the file paths are relative to")
	dir := fs.String("dir", ".", "extract: directory below root to read")
	module := fs.String("module", "", "extract: module path from go.mod")
	snippet := fs.Int("snippet-lines", 40, "extract: longest code snippet kept, in lines")
	var skips listFlag
	fs.Var(&skips, "skip", "extract: path substring to leave out (repeatable)")
	if err := fs.Parse(rest); err != nil {
		return err
	}
	switch cmd {
	case "extract":
		out, err := extractTree(ExtractOptions{Root: *root, Dir: *dir, Module: *module, Skips: skips, SnippetLines: *snippet})
		if err != nil {
			return err
		}
		return emit(out)
	case "tests":
		res := map[string]TestsResult{}
		for _, rel := range fs.Args() {
			res[rel] = testsOf(*root, rel)
		}
		return emit(res)
	case "table":
		res := map[string]TableResult{}
		for _, rel := range fs.Args() {
			res[rel] = tableOf(*root, rel)
		}
		return emit(res)
	case "names":
		res := map[string]NamesResult{}
		for _, rel := range fs.Args() {
			res[rel] = namesOf(*root, rel)
		}
		return emit(res)
	}
	return fmt.Errorf("unknown command %q", cmd)
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "gazetteer-gohelper:", err)
		os.Exit(2)
	}
}
