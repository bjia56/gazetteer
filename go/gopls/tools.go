//go:build tools

// Package tools pins the gopls that ships with gazetteer. Bump with:
//
//	go get golang.org/x/tools/gopls@<version> && go mod tidy
package tools

import _ "golang.org/x/tools/gopls"
