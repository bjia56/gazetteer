package main

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// walkGoFiles calls visit with the path (relative to root, slash separated) of every Go source file under
// root/dir that the go tool itself would consider: no dot or underscore directories, no testdata or vendor,
// and no nested modules (those are units of their own). skips are substrings matched against "/<rel>".
func walkGoFiles(root, dir string, skips []string, wantTests bool, visit func(rel string)) error {
	start := filepath.Join(root, filepath.FromSlash(dir))
	return filepath.WalkDir(start, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			name := d.Name()
			if path == start {
				return nil
			}
			if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") || name == "testdata" || name == "vendor" {
				return filepath.SkipDir
			}
			if _, err := os.Stat(filepath.Join(path, "go.mod")); err == nil {
				return filepath.SkipDir
			}
			if skipped("/"+rel+"/", skips) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(rel, ".go") || strings.HasSuffix(rel, "_test.go") != wantTests {
			return nil
		}
		if skipped("/"+rel, skips) {
			return nil
		}
		visit(rel)
		return nil
	})
}

func skipped(rel string, skips []string) bool {
	for _, s := range skips {
		if s != "" && strings.Contains(rel, s) {
			return true
		}
	}
	return false
}
