#!/bin/bash
set -e
cd ~/voyage/workspace

echo "Running: npm run lint"
npm run lint
echo "  PASSED"

echo "Running: npm run typecheck"
npm run typecheck
echo "  PASSED"

echo "Running: npm test -- --run"
npm test -- --run
echo "  PASSED"

echo "Running: npm run build"
npm run build
echo "  PASSED"

echo ""
echo "All exit criteria passed!"
