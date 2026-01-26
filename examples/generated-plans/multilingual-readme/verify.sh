#!/bin/bash
set -e
cd ~/voyage/workspace

echo "Checking README files exist..."

echo "Running: test -f README.es.md"
test -f README.es.md
echo "  PASSED"

echo "Running: test -f README.fr.md"
test -f README.fr.md
echo "  PASSED"

echo "Running: test -f README.de.md"
test -f README.de.md
echo "  PASSED"

echo "Running: test -f README.ja.md"
test -f README.ja.md
echo "  PASSED"

echo "Running: test -f README.pt-BR.md"
test -f README.pt-BR.md
echo "  PASSED"

echo ""
echo "Checking files are valid text..."
for f in README.es.md README.fr.md README.de.md README.ja.md README.pt-BR.md; do
  echo "Running: file $f (checking encoding)"
  file "$f" | grep -qE "(UTF-8|ASCII|text)"
  echo "  PASSED"
done

echo ""
echo "All exit criteria passed!"
